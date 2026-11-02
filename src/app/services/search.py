# -*- coding: utf-8 -*-
"""
模块: app.services.search
文件名: search.py
作者: xionggaoqing
时间: 2026-10-27
说明: 检索服务：FTS5（二元切分 + unicode61）主方案，`LIKE` 降级（手册「四、检索」）。

    ★索引侧与查询侧必须用同一个 `bigram_tokenize()`，否则永远查不到。
    ★`project_id` 必须参与过滤，否则跨项目串数据。
    数据源：大纲单文件（kind=outline）+ `summaries/`（kind=summary）。
"""

from __future__ import annotations

import re
from typing import Any

import frontmatter
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.core.paths import summaries_dir
from app.db.fts import bigram_tokenize, ensure_fts_schema, fts_available
from app.db.models import ProjectIndex
from app.services import files
from app.services.story import outline_relpath_of
from app.services.workspace import WorkspaceGuard

logger = get_logger(__name__)

_SNIPPET_LEN = 120


async def index_document(
    session: AsyncSession,
    *,
    project_id: str,
    doc_ref: str,
    kind: str,
    title: str,
    content: str,
) -> bool:
    """写入/更新一条派生索引（UPSERT）。返回是否走 FTS（False = 已降级，不落 FTS）。"""
    if not fts_available():
        return False
    conn = await session.connection()
    await ensure_fts_schema(conn)
    await conn.execute(
        text("DELETE FROM story_fts WHERE doc_ref = :ref AND project_id = :pid"),
        {"ref": doc_ref, "pid": project_id},
    )
    await conn.execute(
        text(
            "INSERT INTO story_fts (doc_ref, project_id, kind, title, content) "
            "VALUES (:ref, :pid, :kind, :title, :content)"
        ),
        {
            "ref": doc_ref,
            "pid": project_id,
            "kind": kind,
            "title": bigram_tokenize(title),
            "content": bigram_tokenize(content),
        },
    )
    return True


async def reindex_project(session: AsyncSession, project_id: str) -> dict[str, Any]:
    """全量重建某项目的派生索引（大纲 + 摘要）。"""
    conn = await session.connection()
    available = fts_available()
    if available:
        await ensure_fts_schema(conn)
        await conn.execute(
            text("DELETE FROM story_fts WHERE project_id = :pid"), {"pid": project_id}
        )

    indexed = 0
    if available:
        guard = WorkspaceGuard(project_id)
        rel = outline_relpath_of(project_id)
        if guard.exists(rel):
            outline_text = guard.read(rel)
            meta = files.read_novel_meta(project_id)
            await index_document(
                session,
                project_id=project_id,
                doc_ref=rel,
                kind="outline",
                title=str(meta.get("title") or project_id),
                content=outline_text,
            )
            indexed += 1

        sdir = summaries_dir(project_id)
        if sdir.exists():
            for path in sorted(sdir.glob("ch-*.md")):
                try:
                    summary_meta, content = _read_summary(path)
                except Exception as exc:  # pragma: no cover
                    logger.warning("summary_read_failed", path=str(path), error=str(exc))
                    continue
                await index_document(
                    session,
                    project_id=project_id,
                    doc_ref=f"summaries/{path.name}",
                    kind="summary",
                    title=str(summary_meta.get("title") or path.stem),
                    content=content,
                )
                indexed += 1

    await _mark_dirty(session, project_id, dirty=0)
    return {"project_id": project_id, "fts5": available, "indexed": indexed}


def _read_summary(path: Any) -> tuple[dict[str, Any], str]:
    """读摘要 md 的 frontmatter + 正文。"""

    post = frontmatter.load(str(path))
    return dict(post.metadata), post.content


async def _mark_dirty(session: AsyncSession, project_id: str, *, dirty: int) -> None:

    idx = await session.get(ProjectIndex, project_id)
    if idx is not None:
        idx.dirty = dirty


async def search_settings(
    session: AsyncSession,
    project_id: str,
    query: str,
    *,
    scope: str = "all",
    limit: int = 20,
) -> dict[str, Any]:
    """检索（限本项目）。FTS5 不可用或异常时降级 `LIKE`，`data.degraded=true`。"""
    q = query.strip()
    if not q:
        return {"query": query, "scope": scope, "total": 0, "degraded": False, "items": []}

    if fts_available():
        try:
            items = await _fts_search(session, project_id, q, scope=scope, limit=limit)
            return {
                "query": query,
                "scope": scope,
                "total": len(items),
                "degraded": False,
                "items": items,
            }
        except Exception as exc:
            logger.warning("fts_search_failed_fallback_like", error=str(exc))

    items = await _like_search(session, project_id, q, scope=scope, limit=limit)
    return {"query": query, "scope": scope, "total": len(items), "degraded": True, "items": items}


async def _fts_search(
    session: AsyncSession, project_id: str, q: str, *, scope: str, limit: int
) -> list[dict[str, Any]]:
    conn = await session.connection()
    await ensure_fts_schema(conn)
    match_expr = _build_match(bigram_tokenize(q))
    if not match_expr:
        return []

    sql = """
        SELECT doc_ref, kind, title, snippet(story_fts, 4, '', '', '…', 16) AS snip,
               bm25(story_fts) AS score
        FROM story_fts
        WHERE story_fts MATCH :match AND project_id = :pid
    """
    params: dict[str, Any] = {"match": match_expr, "pid": project_id}
    if scope != "all":
        sql += " AND kind = :kind"
        params["kind"] = scope
    sql += " ORDER BY score LIMIT :limit"
    params["limit"] = limit

    rows = (await conn.execute(text(sql), params)).all()
    return [
        {
            "doc_ref": r[0],
            "kind": r[1],
            "title": _detokenize_preview(r[2]),
            "snippet": (r[3] or "").strip(),
            "score": float(r[4]) if r[4] is not None else 0.0,
        }
        for r in rows
    ]


def _build_match(tokenized: str) -> str:
    """把二元切分结果转成 FTS5 MATCH 表达式（OR 语义，避免长查询零命中）。"""
    tokens = [t for t in tokenized.split() if t]
    if not tokens:
        return ""
    # 用双引号包裹每个 token，避免被当作语法符号
    return " OR ".join(f'"{t}"' for t in tokens[:32])


def _detokenize_preview(tokenized_title: str) -> str:
    """索引里的 title 是二元切分后的文本，展示时去掉空格做近似还原。"""
    return re.sub(r"\s+", "", tokenized_title or "")


async def _like_search(
    session: AsyncSession, project_id: str, q: str, *, scope: str, limit: int
) -> list[dict[str, Any]]:
    """降级路径：直接读文件做子串匹配（接口签名与返回结构不变）。"""
    guard = WorkspaceGuard(project_id)
    items: list[dict[str, Any]] = []

    if scope in ("all", "outline"):
        rel = outline_relpath_of(project_id)
        if guard.exists(rel):
            content = guard.read(rel)
            items.extend(_like_hits(rel, "outline", content, q, limit))

    if scope in ("all", "summary"):
        sdir = summaries_dir(project_id)
        if sdir.exists():
            for path in sorted(sdir.glob("ch-*.md")):
                content = path.read_text(encoding="utf-8")
                items.extend(_like_hits(f"summaries/{path.name}", "summary", content, q, limit))
                if len(items) >= limit:
                    break

    return items[:limit]


def _like_hits(doc_ref: str, kind: str, content: str, q: str, limit: int) -> list[dict[str, Any]]:
    if q not in content:
        return []
    idx = content.index(q)
    start = max(0, idx - _SNIPPET_LEN // 2)
    snippet = content[start : start + _SNIPPET_LEN].replace("\n", " ")
    first_line = next((ln for ln in content.split("\n") if ln.strip().startswith("#")), "")
    return [
        {
            "doc_ref": doc_ref,
            "kind": kind,
            "title": first_line.lstrip("# ").strip() or doc_ref.rsplit("/", 1)[-1],
            "snippet": snippet,
            "score": 0.0,
        }
    ]


async def rebuild_summaries(session: AsyncSession, project_id: str) -> dict[str, Any]:
    """重建章节摘要与派生索引（手册「六 → 路由树 · 摘要」）。

    - LLM 摘要：为尚无摘要的章节生成 `summaries/ch-{id}.md`
      （★v1101：定稿入口移除后不再按 status 过滤；未配置模型时自动跳过，不阻断索引重建）；
    - 派生索引：全量重建 `story_fts` 并刷新统计。
    """
    # ① 摘要生成（尽力而为：无模型/失败只记录，不抛）
    summarize_note = "未生成（正文过短或缺少可用模型时跳过）"
    try:
        from app.agent.loop.knowledge import summarize_all_chapters

        result_summary = await summarize_all_chapters(session, project_id)
        await session.commit()
        if result_summary["summarized"]:
            summarize_note = f"新生成 {result_summary['summarized']} 篇"
    except Exception as exc:
        logger.warning("rebuild_summaries_llm_skipped", project_id=project_id, error=str(exc))
        summarize_note = f"LLM 摘要跳过：{exc}"

    # ② 派生索引重建
    result = await reindex_project(session, project_id)
    sdir = summaries_dir(project_id)
    result["summary_files"] = len(list(sdir.glob("ch-*.md"))) if sdir.exists() else 0
    result["summarize"] = summarize_note
    return result
