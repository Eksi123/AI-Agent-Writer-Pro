# -*- coding: utf-8 -*-
"""
模块: app.agent.workflows.novel_export.preflight
文件名: preflight.py
作者: xionggaoqing
时间: 2026-11-02
说明: 固定流程：导出成书预检（架构优化 v1102，workflows 新增流程）。

    导出机制本体在 `services/exporters.py`（zip/EPUB/MOBI/TXT 打包，应用功能）；
    本流程只做**导出前的质量关卡**——纯规则判定（不调 LLM，可预测、可单测、零成本）：

    E1 结构完整性：大纲文件存在且可解析；卷章非空；
    E2 内容完备性：空章 / 过短章（<50 字）/ 未定稿章清单；
    E3 格式可用性：mobi 需要 calibre（缺失 → 阻断项）；epub/txt/markdown 恒可用；
    E4 汇总：checklist（blocking / warning 两级）+ 是否放行（无 blocking 即放行）。

    调用方（API `POST /novels/{id}/export-preflight`）拿 checklist 给用户过目，
    用户确认后仍走既有 `POST /novels/{id}/export`（预检不阻断导出，只提示）。
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.loop.knowledge.chapters import list_chapters_ordered
from app.core.logging import get_logger
from app.core.paths import outline_relpath
from app.db.models import Novel
from app.services import files
from app.services.workspace import WorkspaceGuard

logger = get_logger(__name__)

#: 正文字数下限（低于视为「过短章」告警）
_MIN_CHAPTER_CHARS = 50


async def run_export_preflight(
    session: AsyncSession, project_id: str, fmt: str = "epub"
) -> dict[str, Any]:
    """执行导出预检（E1→E4），返回 checklist 结构化结果。

    Returns:
        {"passed": bool, "format": fmt, "checks": [{"level", "code", "message", ...}]}
        level ∈ blocking|warning；passed = 无 blocking 项。
    """
    checks: list[dict[str, Any]] = []

    novel = await session.get(Novel, project_id)
    if novel is None:
        return {
            "passed": False,
            "format": fmt,
            "checks": [{"level": "blocking", "code": "novel_missing", "message": "项目不存在"}],
        }

    # ---- E1 结构完整性 ----
    outline_rel = novel.outline_file or outline_relpath(novel.title)
    guard = WorkspaceGuard(project_id)
    try:
        outline_exists = bool(outline_rel) and guard.exists(outline_rel)
    except Exception:  # pragma: no cover - 服务层探测失败按不存在处理
        outline_exists = False
    if not outline_exists:
        checks.append(
            {
                "level": "warning",
                "code": "outline_missing",
                "message": f"大纲文件缺失（{outline_rel}），导出包将不含大纲",
            }
        )

    chapters = await list_chapters_ordered(project_id)
    if not chapters:
        checks.append(
            {"level": "blocking", "code": "no_chapters", "message": "尚无任何章节，无法导出"}
        )
        return {"passed": False, "format": fmt, "checks": checks}

    # ---- E2 内容完备性 ----
    empty: list[str] = []
    short: list[str] = []
    draft: list[str] = []
    for meta in chapters:
        title = str(meta.get("title") or meta.get("id"))
        cid = str(meta.get("id"))
        status = str(meta.get("status") or "")
        if status and status != "finalized":
            draft.append(title)
        try:
            _m, content = files.read_chapter(project_id, str(meta.get("volume_id")), cid)
            text = (content or "").strip()
        except (FileNotFoundError, OSError):
            empty.append(title)
            continue
        if not text:
            empty.append(title)
        elif len(text) < _MIN_CHAPTER_CHARS:
            short.append(title)

    if empty:
        checks.append(
            {
                "level": "warning",
                "code": "empty_chapters",
                "message": f"{len(empty)} 个空章将导出为空白：{'、'.join(empty[:8])}{'…' if len(empty) > 8 else ''}",
                "chapters": empty,
            }
        )
    if short:
        checks.append(
            {
                "level": "warning",
                "code": "short_chapters",
                "message": f"{len(short)} 个章节正文不足 {_MIN_CHAPTER_CHARS} 字：{'、'.join(short[:8])}{'…' if len(short) > 8 else ''}",
                "chapters": short,
            }
        )
    if draft:
        checks.append(
            {
                "level": "warning",
                "code": "draft_chapters",
                "message": f"{len(draft)} 个章节未定稿：{'、'.join(draft[:8])}{'…' if len(draft) > 8 else ''}",
                "chapters": draft,
            }
        )

    # ---- E3 格式可用性 ----
    if fmt == "mobi":
        from app.services.exporters import calibre_available

        if not calibre_available():
            checks.append(
                {
                    "level": "blocking",
                    "code": "calibre_missing",
                    "message": "MOBI 导出需要 calibre（ebook-convert），当前未安装",
                }
            )
    elif fmt not in ("epub", "txt", "markdown"):
        checks.append(
            {"level": "blocking", "code": "bad_format", "message": f"不支持的导出格式：{fmt}"}
        )

    # ---- E4 汇总 ----
    passed = not any(c["level"] == "blocking" for c in checks)
    logger.info(
        "export_preflight_done",
        project_id=project_id,
        fmt=fmt,
        passed=passed,
        checks=len(checks),
    )
    return {"passed": passed, "format": fmt, "checks": checks}
