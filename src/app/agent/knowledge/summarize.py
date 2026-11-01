# -*- coding: utf-8 -*-
"""
模块: app.agent.knowledge.summarize
文件名: summarize.py
作者: xionggaoqing
时间: 2026-10-27
说明: 章节摘要生成（手册「二、(3) 知识库服务函数」）。

    - `summarize_chapter`：手动保存 / Agent 运行结束后触发，**唯一可写 `summaries/`** 的入口；
    - `summarize_all_chapters`：批量为全部章节补摘要（★v1101：定稿入口移除，不再按状态过滤）；
    - 服务层直连 ChatModel（同门禁 G1/G3，不进 Agent 循环——单次转换无需工具与多轮）。
"""

from __future__ import annotations

import json
import re
from typing import Any

import frontmatter
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.knowledge.chapters import list_chapters_ordered
from app.agent.tracing import observation
from app.core.logging import get_logger
from app.core.paths import summary_file
from app.db.base import utcnow
from app.db.models import Novel
from app.services import files
from app.services.workspace import WorkspaceGuard

logger = get_logger(__name__)


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


async def summarize_all_chapters(
    session: AsyncSession, project_id: str, *, model: Any = None, force: bool = False
) -> dict[str, Any]:
    """★v1101：批量为**全部**章节补摘要（定稿入口移除后不再按 status 过滤）。

    正文过短 / 无模型等跳过原因由 `summarize_chapter` 自行返回 skipped。
    """
    chapters = await list_chapters_ordered(project_id)
    done, skipped = [], []
    for meta in chapters:
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
