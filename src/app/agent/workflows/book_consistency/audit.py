# -*- coding: utf-8 -*-
"""
模块: app.agent.workflows.book_consistency.audit
文件名: audit.py
作者: xionggaoqing
时间: 2026-11-02
说明: 固定流程：全书一致性审查（workflows 新增流程）。

    与四阶段固定流程同一设计哲学：**服务层硬编排 + 直连 LLM + JSON 结构化输出**
    （可预测、可单测、成本低），不进 Agent 循环；审查维度与 reviewer 子代理一致
    （时间线 / 人物状态 / 设定冲突 / 未回收伏笔 / 正文与简介匹配），
    子代理 spec 的 `workflows` 绑定即指向本流程。

    流程（A1→A3）：
    - A1 素材收集：章节清单 + 各章摘要（无摘要则正文节选），**按卷分批**防上下文爆炸；
    - A2 分批审查：每卷一次 LLM 调用，产出该卷问题清单（JSON）；
    - A3 汇总：合并各卷问题 → 按级别排序 → 写报告到 `outline/consistency-audit.md`
      并返回结构化结果（供 API / 前端渲染）。

    无模型可用时返回 `skipped`（不抛错，与 summarize_chapter 同语义）。
"""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.loop.knowledge.chapters import list_chapters_ordered
from app.agent.loop.prompts import render
from app.agent.workflows.writing_steps.state import llm_json
from app.core.logging import get_logger
from app.core.paths import summary_file, workspace_dir
from app.db.models import Novel
from app.services import files

logger = get_logger(__name__)

#: 每卷审查素材的单章摘要上限（防提示词膨胀）
_CHAPTER_EXCERPT_LEN = 400
#: 问题级别排序权重
_LEVEL_ORDER = {"blocking": 0, "suggestion": 1}


def _read_summary(project_id: str, chapter_id: str) -> str:
    path = summary_file(project_id, chapter_id)
    if not path.exists():
        return ""
    try:
        import frontmatter

        post = frontmatter.load(str(path))
        key_events = post.metadata.get("key_events")
        if isinstance(key_events, list) and key_events:
            return "；".join(str(e) for e in key_events).strip()
        return (post.content or "").strip()
    except Exception as exc:  # pragma: no cover
        logger.warning("audit_summary_read_failed", chapter_id=chapter_id, error=str(exc))
        return ""


def _chapter_material(project_id: str, meta: dict[str, Any]) -> str:
    """单章素材：标题 + 摘要（优先）或正文节选。"""
    title = str(meta.get("title") or meta.get("id"))
    cid = str(meta.get("id"))
    summary = _read_summary(project_id, cid)
    if summary:
        return f"### {title}\n摘要：{summary[:_CHAPTER_EXCERPT_LEN]}"
    try:
        _m, content = files.read_chapter(project_id, str(meta.get("volume_id")), cid)
        text = (content or "").strip().replace("\n", " ")
        return f"### {title}\n正文节选:{text[:_CHAPTER_EXCERPT_LEN]}"
    except (FileNotFoundError, OSError):
        return f"### {title}\n（无内容）"


async def run_book_consistency_audit(
    session: AsyncSession,
    project_id: str,
    *,
    model: Any = None,
) -> dict[str, Any]:
    """执行全书一致性审查（A1→A3），返回结构化结果并把报告落盘 outline/。

    Returns:
        {"status": "done"|"skipped", "issues": [...], "volumes_checked": n, "report_path": str}
        skipped 时带 reason（无章节 / 无模型）。
    """
    novel = await session.get(Novel, project_id)
    if novel is None:
        return {"status": "skipped", "reason": "项目不存在", "issues": []}

    # ---- A1 素材收集（按卷分组） ----
    chapters = await list_chapters_ordered(project_id)
    if not chapters:
        return {"status": "skipped", "reason": "尚无章节", "issues": []}

    if model is None:
        from app.agent.loop.llm.factory import get_default_chat_model

        try:
            _ref, model = await get_default_chat_model(session)
        except Exception as exc:
            logger.info("audit_skipped_no_model", error=str(exc))
            return {"status": "skipped", "reason": f"未配置可用模型：{exc}", "issues": []}

    volumes: dict[str, list[dict[str, Any]]] = {}
    for meta in chapters:
        key = str(meta.get("volume_title") or "未分卷")
        volumes.setdefault(key, []).append(meta)

    # ---- A2 分批审查（每卷一次 LLM 调用） ----
    all_issues: list[dict[str, Any]] = []
    for vol_title, metas in volumes.items():
        material = "\n\n".join(_chapter_material(project_id, m) for m in metas)
        prompt = render(
            "audit_volume.md.j2",
            title=novel.title,
            volume_title=vol_title,
            material=material[:12000],
        )
        try:
            resp = await model.ainvoke(prompt)
            result = llm_json(resp.content if hasattr(resp, "content") else str(resp))
        except Exception as exc:
            logger.warning("audit_volume_failed", volume=vol_title, error=str(exc))
            all_issues.append(
                {
                    "level": "suggestion",
                    "chapter": "",
                    "type": "audit_error",
                    "detail": f"卷「{vol_title}」审查调用失败：{exc}",
                    "advice": "稍后重试或检查模型配置",
                    "volume": vol_title,
                }
            )
            continue
        for issue in result.get("issues") or []:
            if isinstance(issue, dict):
                issue.setdefault("volume", vol_title)
                all_issues.append(issue)

    # ---- A3 汇总 + 落盘报告 ----
    all_issues.sort(key=lambda i: _LEVEL_ORDER.get(str(i.get("level")), 9))
    report = _render_report(novel.title, volumes, all_issues)
    report_path = workspace_dir(project_id) / "outline" / "consistency-audit.md"
    try:
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(report, encoding="utf-8")
    except OSError as exc:  # pragma: no cover
        logger.warning("audit_report_write_failed", error=str(exc))

    logger.info(
        "book_audit_done",
        project_id=project_id,
        volumes=len(volumes),
        issues=len(all_issues),
    )
    return {
        "status": "done",
        "issues": all_issues,
        "volumes_checked": len(volumes),
        "report_path": str(report_path),
    }


def _render_report(
    title: str, volumes: dict[str, list[dict[str, Any]]], issues: list[dict[str, Any]]
) -> str:
    """把审查结果渲染为 Markdown 报告（落 outline/consistency-audit.md）。"""
    lines = [
        f"# 《{title}》全书一致性审查报告",
        "",
        f"- 审查卷数：{len(volumes)}",
        f"- 问题总数：{len(issues)}"
        f"（阻断 {sum(1 for i in issues if i.get('level') == 'blocking')} /"
        f" 建议 {sum(1 for i in issues if i.get('level') != 'blocking')}）",
        "",
    ]
    if not issues:
        lines.append("未发现问题。")
        return "\n".join(lines)

    lines.append("| 级别 | 卷 | 章 | 类型 | 问题 | 建议 |")
    lines.append("| --- | --- | --- | --- | --- | --- |")
    for i in issues:
        level = "阻断" if i.get("level") == "blocking" else "建议"
        detail = str(i.get("detail") or "").replace("|", "\\|").replace("\n", " ")
        advice = str(i.get("advice") or "").replace("|", "\\|").replace("\n", " ")
        lines.append(
            f"| {level} | {i.get('volume', '')} | {i.get('chapter', '')} "
            f"| {i.get('type', '')} | {detail[:200]} | {advice[:200]} |"
        )
    return "\n".join(lines)


def audit_result_json(result: dict[str, Any]) -> str:
    """结构化结果 → JSON 字符串（API 层落审计 / 回传前端）。"""
    return json.dumps(result, ensure_ascii=False, default=str)
