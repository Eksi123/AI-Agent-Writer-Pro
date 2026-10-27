#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
模块: app.agent.knowledge
文件名: __init__.py
作者: xionggaoqing
时间: 2026-10-27
说明: 知识库服务（手册「二、(3) 知识库服务函数」，非 Agent 工具，服务层内部调用）。

    - `build_recap`：前情提要，经 `RecapInjectionMiddleware.abefore_model` 注入系统提示；
    - `summarize_chapter`：章节摘要生成（定稿或 Agent 运行结束后触发），写 `summaries/`；
    - 大纲章简介解析：供门禁 G0/G1/G3（M4）。
"""

from __future__ import annotations

import json
import re
from typing import Any

import frontmatter
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.core.paths import summary_file
from app.db.base import utcnow
from app.db.models import Novel
from app.services import files
from app.services.workspace import WorkspaceGuard

logger = get_logger(__name__)

#: 前情提要最多带的章数（近 N 章摘要 + 上一卷末章锚点）
_RECAP_CHAPTERS = 6
_RECAP_EXCERPT_LEN = 600


async def list_chapters_ordered(project_id: str) -> list[dict[str, Any]]:
    """按大纲派生顺序列出全部章节元信息（含 volume_id / title / sort_order / status）。"""
    volumes = files.read_volumes(project_id)
    volumes.sort(key=lambda v: int(v.get("sort_order") or 0))
    out: list[dict[str, Any]] = []
    for vol in volumes:
        vol_id = str(vol.get("id"))
        vol_sort = int(vol.get("sort_order") or 0)
        metas: list[dict[str, Any]] = []
        for path in files.list_chapter_files(project_id, vol_id):
            chapter_id = path.stem[1:]
            try:
                meta, _content = files.read_chapter(project_id, vol_id, chapter_id)
            except (FileNotFoundError, OSError):
                continue
            meta = dict(meta)
            meta["id"] = chapter_id
            meta["volume_id"] = vol_id
            meta["volume_title"] = vol.get("title")
            meta["_vol_sort"] = vol_sort
            metas.append(meta)
        metas.sort(key=lambda m: int(m.get("sort_order") or 0))
        out.extend(metas)
    return out


async def build_pinned_chapters(
    session: AsyncSession, project_id: str, chapter_ids: list[str]
) -> str:
    """构建「已读章节」上下文段（会话级 Chips，手册「三、(3) 输入窗口」）。

    每章给标题 + 摘要（无摘要则正文节选），供 RecapInjection 注入系统提示。
    """
    if not chapter_ids:
        return ""
    chapters = await list_chapters_ordered(project_id)
    by_id = {str(m.get("id")): m for m in chapters}
    lines: list[str] = ["## 用户指定的上下文章节"]
    for cid in chapter_ids[:8]:  # 上限 8 章，防提示词膨胀
        meta = by_id.get(cid)
        if meta is None:
            continue
        title = str(meta.get("title") or cid)
        vol_title = str(meta.get("volume_title") or "")
        head = f"### 【{vol_title} · {title}】" if vol_title else f"### 【{title}】"
        lines.append(head)
        summary = _read_summary_text(project_id, cid)
        if summary:
            lines.append(f"摘要：{summary[:500]}")
        else:
            try:
                _m, content = files.read_chapter(project_id, str(meta.get("volume_id")), cid)
                text = re.sub(r"\s+", " ", content or "").strip()
                lines.append(f"节选：{text[:500]}{'…' if len(text) > 500 else ''}")
            except (FileNotFoundError, OSError):
                continue
    return "\n".join(lines) if len(lines) > 1 else ""


async def build_recap(
    session: AsyncSession,
    project_id: str,
    *,
    before_chapter_id: str | None = None,
    max_chapters: int = _RECAP_CHAPTERS,
) -> str:
    """构建前情提要（前 N 章摘要，无摘要则取正文开头节选）。

    Args:
        before_chapter_id: 只汇总该章**之前**的章节（生成第 N 章时传第 N 章 id）。
    """
    chapters = await list_chapters_ordered(project_id)
    if not chapters:
        return ""

    if before_chapter_id:
        cut: list[dict[str, Any]] = []
        for meta in chapters:
            if meta.get("id") == before_chapter_id:
                break
            cut.append(meta)
        chapters = cut
    if not chapters:
        return "（这是全书第一章，暂无前情提要）"

    recent = chapters[-max_chapters:]
    guard = WorkspaceGuard(project_id)
    lines: list[str] = []

    novel = await session.get(Novel, project_id)
    if novel is not None:
        lines.append(f"书名：{novel.title}")
        if novel.genre:
            lines.append(f"题材：{novel.genre}")
        if novel.synopsis:
            lines.append(f"梗概：{novel.synopsis[:300]}")
        lines.append("")

    total = len(chapters)
    if total > len(recent):
        lines.append(f"（已写 {total} 章，以下仅列最近 {len(recent)} 章）")

    for meta in recent:
        chapter_id = str(meta.get("id"))
        title = str(meta.get("title") or chapter_id)
        vol_title = str(meta.get("volume_title") or "")
        head = f"- 【{vol_title} · {title}】" if vol_title else f"- 【{title}】"

        summary = _read_summary_text(project_id, chapter_id)
        if summary:
            lines.append(head + summary[:_RECAP_EXCERPT_LEN])
        else:
            excerpt = _read_chapter_excerpt(guard, project_id, meta)
            lines.append(head + (excerpt or "（暂无内容）"))

    # 上一卷末章锚点（跨卷衔接，门禁 G1 复用）
    anchor = _cross_volume_anchor(project_id, chapters, recent)
    if anchor:
        lines.append("")
        lines.append(anchor)

    return "\n".join(lines).strip()


def _read_summary_text(project_id: str, chapter_id: str) -> str:
    path = summary_file(project_id, chapter_id)
    if not path.exists():
        return ""
    try:
        post = frontmatter.load(str(path))
        key_events = post.metadata.get("key_events")
        if isinstance(key_events, list) and key_events:
            return "；".join(str(e) for e in key_events).strip()
        return (post.content or "").strip()
    except Exception as exc:  # pragma: no cover
        logger.warning("summary_read_failed", chapter_id=chapter_id, error=str(exc))
        return ""


def _read_chapter_excerpt(guard: WorkspaceGuard, project_id: str, meta: dict[str, Any]) -> str:
    try:
        _m, content = files.read_chapter(project_id, str(meta.get("volume_id")), str(meta.get("id")))
    except (FileNotFoundError, OSError):
        return ""
    text = re.sub(r"\s+", " ", content or "").strip()
    if len(text) > _RECAP_EXCERPT_LEN:
        return text[:_RECAP_EXCERPT_LEN] + "…"
    return text


def _cross_volume_anchor(
    project_id: str, chapters: list[dict[str, Any]], recent: list[dict[str, Any]]
) -> str:
    """若最近章节不含上一卷内容，补一条上一卷末章锚点（跨卷衔接）。"""
    if not recent:
        return ""
    first_vol = recent[0].get("volume_id")
    earlier_other_vol = [c for c in chapters if c.get("volume_id") != first_vol]
    if not earlier_other_vol:
        return ""
    last = earlier_other_vol[-1]
    title = str(last.get("title") or "")
    vol_title = str(last.get("volume_title") or "")
    summary = _read_summary_text(project_id, str(last.get("id"))) or "（上一卷末章，详见正文）"
    return f"- ★上一卷衔接锚点【{vol_title} · {title}】{summary[:300]}"


# ---------------------------------------------------------------------------
# 章节摘要生成（LLM）
# ---------------------------------------------------------------------------

_SUMMARIZE_PROMPT = """\
你是小说编辑。请为下面这一章生成**摘要**，供后续章节写作时作前情提要使用。

要求：
1. 只输出 JSON，不要任何解释文字或 markdown 代码块标记；
2. JSON 结构：{{"key_events": ["要点1", "要点2", ...], "characters": ["出场人物"], "foreshadowing": ["埋下或回收的伏笔"], "ending_state": "章末人物状态与所处情境一句话"}}；
3. `key_events` 3-6 条，每条不超过 40 字，按时间顺序；
4. 严格忠于原文，不要推测或补充未写出的情节；
5. 全部用中文。

书名：{title}
卷：{volume_title}
章：{chapter_title}

正文：
{content}
"""


async def summarize_chapter(
    session: AsyncSession,
    project_id: str,
    volume_id: str,
    chapter_id: str,
    *,
    model: Any = None,
    force: bool = False,
) -> dict[str, Any]:
    """生成章节摘要并写入 `summaries/ch-{id}.md`（★仅此函数可写 summaries/）。

    `model` 为 None 时按当前默认模型构建；无可用模型则跳过（返回 skipped）。
    写入成功后同步派生索引。
    """
    novel = await session.get(Novel, project_id)
    if novel is None:
        return {"skipped": True, "reason": "项目不存在"}

    try:
        meta, content = files.read_chapter(project_id, volume_id, chapter_id)
    except FileNotFoundError:
        return {"skipped": True, "reason": "章节文件不存在"}

    out_path = summary_file(project_id, chapter_id)
    if out_path.exists() and not force:
        return {"skipped": True, "reason": "摘要已存在（force=false）"}

    text = (content or "").strip()
    if len(text) < 30:
        return {"skipped": True, "reason": "正文过短，无需摘要"}

    if model is None:
        from app.agent.llm.factory import get_default_chat_model

        try:
            _ref, model = await get_default_chat_model(session)
        except Exception as exc:
            logger.info("summarize_skipped_no_model", error=str(exc))
            return {"skipped": True, "reason": f"未配置可用模型：{exc}"}

    volumes = files.read_volumes(project_id)
    vol = files.find_volume(volumes, volume_id) or {}

    prompt = _SUMMARIZE_PROMPT.format(
        title=novel.title,
        volume_title=vol.get("title") or "",
        chapter_title=meta.get("title") or chapter_id,
        content=text[:24000],
    )

    from app.agent.tracing import observation

    with observation("summarize_chapter", metadata={"project_id": project_id, "chapter_id": chapter_id}):
        try:
            from langchain_core.messages import HumanMessage

            resp = await model.ainvoke([HumanMessage(content=prompt)])
            raw = _message_text(resp)
        except Exception as exc:
            logger.warning("summarize_llm_failed", chapter_id=chapter_id, error=str(exc))
            return {"skipped": True, "reason": f"摘要生成失败：{exc}"}

    parsed = _parse_summary_json(raw)
    model_name = getattr(model, "model_name", None) or getattr(model, "model", None) or type(model).__name__

    body_lines: list[str] = []
    for event in parsed.get("key_events") or []:
        body_lines.append(f"- {event}")
    if parsed.get("ending_state"):
        body_lines.append(f"- 章末状态：{parsed['ending_state']}")
    if parsed.get("foreshadowing"):
        body_lines.append("- 伏笔：" + "；".join(str(f) for f in parsed["foreshadowing"]))
    body = "\n".join(body_lines) or (raw or "").strip()[:800]

    fm = {
        "chapter_id": chapter_id,
        "volume_id": volume_id,
        "title": str(meta.get("title") or chapter_id),
        "key_events": parsed.get("key_events") or [],
        "characters": parsed.get("characters") or [],
        "foreshadowing": parsed.get("foreshadowing") or [],
        "ending_state": parsed.get("ending_state") or "",
        "model": str(model_name),
        "created_at": utcnow(),
    }
    guard = WorkspaceGuard(project_id)
    guard.write(f"summaries/ch-{chapter_id}.md", frontmatter.dumps(frontmatter.Post(body, **fm)) + "\n", allow_summaries=True)

    # 同步派生索引（失败不阻断）
    try:
        from app.services import search as search_service

        await search_service.reindex_project(session, project_id)
    except Exception as exc:  # pragma: no cover
        logger.warning("summary_reindex_failed", chapter_id=chapter_id, error=str(exc))

    return {"chapter_id": chapter_id, "path": f"summaries/ch-{chapter_id}.md", "key_events": fm["key_events"]}


def _message_text(resp: Any) -> str:
    content = getattr(resp, "content", resp)
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict):
                parts.append(str(block.get("text") or block.get("content") or ""))
        return "".join(parts)
    return str(content)


_JSON_BLOCK = re.compile(r"\{.*\}", re.S)


def _parse_summary_json(raw: str) -> dict[str, Any]:
    """容错解析模型返回的 JSON（可能带代码块标记或前后解释文字）。"""
    if not raw:
        return {}
    text = raw.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text).strip()
    try:
        value = json.loads(text)
        return value if isinstance(value, dict) else {}
    except ValueError:
        pass
    m = _JSON_BLOCK.search(text)
    if m:
        try:
            value = json.loads(m.group(0))
            return value if isinstance(value, dict) else {}
        except ValueError:
            return {}
    return {}


async def summarize_finalized_chapters(
    session: AsyncSession, project_id: str, *, model: Any = None, force: bool = False
) -> dict[str, Any]:
    """批量为已定稿（status=finalized）章节生成摘要。"""
    chapters = await list_chapters_ordered(project_id)
    done, skipped = [], []
    for meta in chapters:
        if str(meta.get("status") or "draft") != "finalized":
            skipped.append({"id": meta.get("id"), "reason": "未定稿"})
            continue
        result = await summarize_chapter(
            session,
            project_id,
            str(meta.get("volume_id")),
            str(meta.get("id")),
            model=model,
            force=force,
        )
        (done if not result.get("skipped") else skipped).append(result)
    return {"summarized": len(done), "skipped": len(skipped), "details": skipped[:20]}
