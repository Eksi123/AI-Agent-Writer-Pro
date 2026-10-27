# -*- coding: utf-8 -*-
"""
模块: app.services.story
文件名: story.py
作者: xionggaoqing
时间: 2026-10-27
说明: 大纲读写与校验服务。

    ★`sync_order_from_outline` 是卷章 `sort_order` 的**唯一写入口**（手册「四」/ 铁律 10）：
    - 触发点：① `PUT /novels/{id}/outline` 保存成功后；② Agent 改大纲时经 FileChangeNotify（M3）；
    - 规则：按卷节 H1 先后写 volumes.yaml 的 sort_order（1..N）；按各卷节下章简介 H2 先后写
      对应章 frontmatter 的 sort_order（卷内 1..N）；匹配以归一化「卷名+章名完全一致」为准；
    - 不做：不改 ID / 文件名、不移动章节文件、不支持跨卷移动、不产生修订快照、不递增 version。
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError
from app.core.logging import get_logger
from app.core.paths import outline_filename
from app.db.base import utcnow
from app.services import audit, files
from app.services.outline import (
    OutlineParseResult,
    normalize_title,
    parse_outline,
    render_template,
)
from app.services.workspace import WorkspaceGuard

logger = get_logger(__name__)


def outline_relpath_of(project_id: str) -> str:
    """从 novel.yaml 取大纲相对路径（唯一寻址依据，不按 title 现推）。"""
    meta = files.read_novel_meta(project_id)
    rel = str(meta.get("outline_file") or "").strip()
    if rel:
        return rel
    # 回落：按标题派生（仅当 novel.yaml 缺字段时）
    return f"outline/{outline_filename(str(meta.get('title') or project_id))}"


async def read_outline(session: AsyncSession, project_id: str) -> dict[str, Any]:
    """读大纲全文。文件不存在时返回模板内容（不落盘，保持解析只读原则）。"""
    from app.services import novels

    await novels.get_novel(session, project_id)
    guard = WorkspaceGuard(project_id)
    rel = outline_relpath_of(project_id)

    if guard.exists(rel):
        content = guard.read(rel)
    else:
        title = str(files.read_novel_meta(project_id).get("title") or project_id)
        content = render_template(title)
        logger.warning("outline_missing_return_template", project_id=project_id, rel=rel)

    return {
        "novel_id": project_id,
        "path": rel,
        "filename": rel.rsplit("/", 1)[-1],
        "content": content,
        "word_count": files.count_words(content),
        "order_synced": False,
    }


async def write_outline(
    session: AsyncSession, project_id: str, content: str
) -> dict[str, Any]:
    """保存大纲，成功后执行 `sync_order_from_outline` 回填卷章顺序。"""
    from app.services import novels

    novel = await novels.get_novel(session, project_id)
    guard = WorkspaceGuard(project_id)
    rel = outline_relpath_of(project_id)

    guard.write(rel, content)

    # 同步 novel.yaml.outline_file（防止历史上字段缺失）
    meta = files.read_novel_meta(project_id)
    if meta.get("outline_file") != rel:
        meta["outline_file"] = rel
        meta["updated_at"] = utcnow()
        files.write_novel_meta(project_id, meta)

    sync = sync_order_from_outline(project_id, content)

    novel.updated_at = utcnow()
    await session.flush()
    await audit.record(
        session,
        "outline.save",
        entity_type="novel",
        entity_id=project_id,
        detail={
            "path": rel,
            "word_count": files.count_words(content),
            "volumes_reordered": sync["volumes_updated"],
            "chapters_reordered": sync["chapters_updated"],
            "volume_order": sync["volume_order"],
            "warnings": sync["warnings"][:20],
        },
        commit=True,
    )
    await novels.refresh_project_index(session, project_id)

    return {
        "novel_id": project_id,
        "path": rel,
        "filename": rel.rsplit("/", 1)[-1],
        "content": content,
        "word_count": files.count_words(content),
        "order_synced": True,
        "order_sync": sync,
    }


def sync_order_from_outline(project_id: str, outline_text: str | None = None) -> dict[str, Any]:
    """★按大纲小节先后回填 `sort_order`（卷章顺序的唯一写入口）。

    只改 `sort_order`：不改 ID、不改文件名、不移动文件、不写修订快照、不递增 `version`。
    大纲中未出现的卷/章保持原值并追加在该卷末尾（出「未在大纲中登记」警告）。
    """
    if outline_text is None:
        guard = WorkspaceGuard(project_id)
        rel = outline_relpath_of(project_id)
        outline_text = guard.read(rel) if guard.exists(rel) else ""

    parsed: OutlineParseResult = parse_outline(outline_text)
    warnings: list[str] = list(parsed.warnings)

    volumes_updated, chapters_updated = 0, 0
    volume_order: list[str] = []

    # ---- 卷顺序 ----
    volumes = files.read_volumes(project_id)
    if volumes:
        by_norm = {normalize_title(str(v.get("title") or "")): v for v in volumes}
        claimed: set[str] = set()
        next_sort = 1
        for sec in parsed.volumes:
            vol = by_norm.get(sec.normalized)
            if vol is None:
                warnings.append(
                    f"大纲卷节「{sec.title}」在 volumes.yaml 中无匹配卷（归一化后比较），已跳过"
                )
                continue
            claimed.add(str(vol.get("id")))
            volume_order.append(str(vol.get("title")))
            if int(vol.get("sort_order") or 0) != next_sort:
                vol["sort_order"] = next_sort
                volumes_updated += 1
            next_sort += 1

        # 大纲未登记的卷：保持相对次序，追加到末尾
        leftovers = [v for v in volumes if str(v.get("id")) not in claimed]
        leftovers.sort(key=lambda v: int(v.get("sort_order") or 0))
        for vol in leftovers:
            warnings.append(f"卷「{vol.get('title')}」未在大纲中登记，已追加到末尾")
            if int(vol.get("sort_order") or 0) != next_sort:
                vol["sort_order"] = next_sort
                volumes_updated += 1
            next_sort += 1

        files.write_volumes(project_id, volumes)

    # ---- 章顺序（卷内 1..N）----
    for sec in parsed.volumes:
        vol = _find_vol_by_norm(volumes, sec.normalized)
        if vol is None:
            continue
        vol_id = str(vol.get("id"))
        chapter_metas: list[tuple[str, dict[str, Any]]] = []
        for path in files.list_chapter_files(project_id, vol_id):
            chapter_id = path.stem[1:]
            try:
                meta, _content = files.read_chapter(project_id, vol_id, chapter_id)
            except Exception:  # pragma: no cover
                continue
            chapter_metas.append((chapter_id, meta))

        by_norm = {normalize_title(str(m.get("title") or "")): (cid, m) for cid, m in chapter_metas}
        claimed_ch: set[str] = set()
        next_sort = 1
        for ch_sec in sec.chapters:
            hit = by_norm.get(ch_sec.normalized)
            if hit is None:
                warnings.append(
                    f"卷「{sec.title}」下大纲章「{ch_sec.title}」无匹配章节文件，已跳过"
                )
                continue
            cid, meta = hit
            claimed_ch.add(cid)
            if int(meta.get("sort_order") or 0) != next_sort:
                meta["sort_order"] = next_sort
                chapters_updated += 1
                _write_meta_only(project_id, vol_id, cid, meta)
            next_sort += 1

        for cid, meta in chapter_metas:
            if cid in claimed_ch:
                continue
            warnings.append(f"章「{meta.get('title')}」未在大纲中登记，已追加到卷末尾")
            if int(meta.get("sort_order") or 0) != next_sort:
                meta["sort_order"] = next_sort
                chapters_updated += 1
                _write_meta_only(project_id, vol_id, cid, meta)
            next_sort += 1

    result = {
        "volumes_updated": volumes_updated,
        "chapters_updated": chapters_updated,
        "volume_order": volume_order,
        "warnings": warnings,
    }
    if volumes_updated or chapters_updated:
        logger.info("order_synced", project_id=project_id, **{k: result[k] for k in ("volumes_updated", "chapters_updated")})
    return result


def _find_vol_by_norm(volumes: list[dict[str, Any]], normalized: str) -> dict[str, Any] | None:
    for v in volumes:
        if normalize_title(str(v.get("title") or "")) == normalized:
            return v
    return None


def _write_meta_only(project_id: str, vol_id: str, chapter_id: str, meta: dict[str, Any]) -> None:
    """只改 frontmatter（顺序回填）：保留正文不变，**不递增 version、不写快照**。"""
    from app.core.paths import chapter_file

    path = chapter_file(project_id, vol_id, chapter_id)
    if not path.exists():
        raise NotFoundError(f"章节文件不存在：{path}")
    _meta, content = files.read_chapter(project_id, vol_id, chapter_id)
    files.write_chapter(project_id, vol_id, chapter_id, meta, content)


# ---------------------------------------------------------------------------
# 校验
# ---------------------------------------------------------------------------


async def validate_outline(session: AsyncSession, project_id: str) -> dict[str, Any]:
    """解析校验：归一化一致性、缺失清单、序数与顺序冲突、未登记项、错误行号。

    与 G0 复用同一份 `parse_outline`，避免两套解析漂移。校验不阻断保存。
    """
    from app.services import novels

    await novels.get_novel(session, project_id)
    guard = WorkspaceGuard(project_id)
    rel = outline_relpath_of(project_id)
    text = guard.read(rel) if guard.exists(rel) else ""

    parsed = parse_outline(text)
    issues: list[dict[str, Any]] = [{"level": "warning", "line": None, "message": w} for w in parsed.warnings]

    volumes = files.read_volumes(project_id)
    vol_norms = {normalize_title(str(v.get("title") or "")): v for v in volumes}

    mismatches: list[dict[str, Any]] = []
    missing: list[dict[str, Any]] = []
    unregistered: list[dict[str, Any]] = []

    matched_vol_ids: set[str] = set()
    for sec in parsed.volumes:
        vol = vol_norms.get(sec.normalized)
        if vol is None:
            mismatches.append(
                {"kind": "volume", "outline_title": sec.title, "line": sec.line_no,
                 "message": "大纲卷节在 volumes.yaml 中无归一化匹配"}
            )
            continue
        vol_id = str(vol.get("id"))
        matched_vol_ids.add(vol_id)

        # 序数 vs sort_order 冲突告警
        ordinal = _ordinal_of(sec.title)
        if ordinal is not None and ordinal != int(vol.get("sort_order") or 0):
            issues.append(
                {"level": "warning", "line": sec.line_no,
                 "message": f"卷「{sec.title}」序数({ordinal})与 sort_order({vol.get('sort_order')})不一致"}
            )

        chapter_metas: dict[str, tuple[str, dict[str, Any]]] = {}
        for path in files.list_chapter_files(project_id, vol_id):
            cid = path.stem[1:]
            try:
                m, _c = files.read_chapter(project_id, vol_id, cid)
            except Exception:  # pragma: no cover
                continue
            chapter_metas[normalize_title(str(m.get("title") or ""))] = (cid, m)

        matched_ch: set[str] = set()
        for ch_sec in sec.chapters:
            hit = chapter_metas.get(ch_sec.normalized)
            if hit is None:
                missing.append(
                    {"kind": "chapter", "volume": sec.title, "outline_title": ch_sec.title,
                     "line": ch_sec.line_no, "reason": "无对应章节文件"}
                )
                continue
            cid, m = hit
            matched_ch.add(cid)
            ch_ordinal = _ordinal_of(ch_sec.title)
            if ch_ordinal is not None and ch_ordinal != int(m.get("sort_order") or 0):
                issues.append(
                    {"level": "warning", "line": ch_sec.line_no,
                     "message": f"章「{ch_sec.title}」序数({ch_ordinal})与 sort_order({m.get('sort_order')})不一致"}
                )
            if ch_sec.intro_empty:
                missing.append(
                    {"kind": "chapter_intro", "volume": sec.title, "outline_title": ch_sec.title,
                     "line": ch_sec.line_no, "reason": "小节存在但简介为空"}
                )

        for _norm, (cid, m) in chapter_metas.items():
            if cid not in matched_ch:
                unregistered.append(
                    {"kind": "chapter", "volume": sec.title, "title": m.get("title"), "id": cid,
                     "message": "章节未在大纲中登记，顺序保持并追加到卷末尾"}
                )

    for v in volumes:
        if str(v.get("id")) not in matched_vol_ids:
            unregistered.append(
                {"kind": "volume", "title": v.get("title"), "id": v.get("id"),
                 "message": "卷未在大纲中登记，顺序保持并追加到末尾"}
            )

    if not parsed.volumes and not parsed.appendices:
        issues.append(
            {"level": "error", "line": 1, "message": "大纲为空或无任何 H1 卷节/附录节"}
        )

    ok = not any(i["level"] == "error" for i in issues)
    return {
        "novel_id": project_id,
        "ok": ok,
        "volume_count": len(parsed.volumes),
        "chapter_count": sum(len(v.chapters) for v in parsed.volumes),
        "appendix_count": len(parsed.appendices),
        "issues": issues,
        "missing": missing,
        "mismatches": mismatches,
        "unregistered": unregistered,
    }


_CN_DIGITS = {"零": 0, "一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}


def _ordinal_of(title: str) -> int | None:
    """从「第X卷 / 第X章」标题里解析序数；无法解析返回 None。"""
    import re

    m = re.match(r"^第\s*([0-9零一二三四五六七八九十百千万两]+)\s*[卷章回节篇部集话話]", title.strip())
    if not m:
        return None
    token = m.group(1)
    if token.isdigit():
        return int(token)
    # 简易中文数字解析（覆盖常见 1–99）
    if token == "十":
        return 10
    if token.startswith("十"):
        rest = token[1:]
        return 10 + _CN_DIGITS.get(rest, 0) if rest in _CN_DIGITS else 10
    if "十" in token:
        tens, _, ones = token.partition("十")
        return _CN_DIGITS.get(tens, 1) * 10 + (_CN_DIGITS.get(ones, 0) if ones else 0)
    return _CN_DIGITS.get(token)
