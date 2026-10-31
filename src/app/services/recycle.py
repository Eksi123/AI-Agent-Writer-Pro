# -*- coding: utf-8 -*-
"""
模块: app.services.recycle
文件名: recycle.py
作者: xionggaoqing
时间: 2026-10-31
说明: 回收站（v1031）——被删除**章节**的归档 / 浏览 / 回收 / 彻底删除。

    需求要点：
    - 回收站只保留**最近 20 本**「删除过章节」的小说（含整本被删除的小说）；
    - 按小说浏览：卷 → 该卷下被删除的章节；
    - 单个章节可「回收」（还原）或「彻底删除」；
    - 整本小说被删除（工作区已移入 `data/trash/`）时，回收其中任一章节
      会**连同小说项目与它的大纲一并恢复**（工作区整体搬回 `data/workspaces/`）。

    存储布局（文件是事实源，DB 只存 novels 登记行）：
        data/recycle/{project_id}/index.yaml         ← 清单（书名快照 + 被删章节列表）
        data/recycle/{project_id}/chapters/c{id}.md  ← 被删章节原文件（frontmatter + 正文）
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path
from typing import Any

import yaml
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError
from app.core.logging import get_logger
from app.core.paths import (
    chapter_file,
    recycle_chapters_dir,
    recycle_index_file,
    recycle_novel_dir,
    recycle_root,
    trash_root,
    workspace_dir,
)
from app.db.base import utcnow
from app.db.models import Novel, ProjectIndex
from app.services import audit, files

logger = get_logger(__name__)

#: 回收站最多保留多少本小说（只保存最近删除的 20 本）
MAX_NOVELS = 20

#: 路径里用到的 ID 直接来自 URL，未校验就拼进路径可穿越出 `data/recycle/`
#: （回收站不经 `WorkspaceGuard`），所以在任何拼路径之前先挡住。
#: `novel_id` 是 `new_id()`（uuid4，含连字符），`chapter_id` 是 `short_id()`（8 位十六进制）；
#: 两者都不含 `.` / `/` / `\`，故按字符集白名单即可杜绝穿越。
_SAFE_NOVEL_ID = re.compile(r"^[0-9a-fA-F-]{1,64}$")
_SAFE_CHAPTER_ID = re.compile(r"^[0-9a-fA-F]{1,32}$")


def _require_safe_id(value: str, pattern: re.Pattern[str], what: str) -> str:
    """校验来自 URL 的 ID，不合白名单一律按「不存在」处理（不泄露路径细节）。"""
    v = str(value or "").strip()
    if not pattern.match(v):
        raise NotFoundError(f"回收站里没有这个{what}：{value}")
    return v


def _safe_novel_id(novel_id: str) -> str:
    return _require_safe_id(novel_id, _SAFE_NOVEL_ID, "小说")


def _safe_chapter_id(chapter_id: str) -> str:
    return _require_safe_id(chapter_id, _SAFE_CHAPTER_ID, "章节")


# ---------------------------------------------------------------------------
# index.yaml 读写
# ---------------------------------------------------------------------------


def _dump_yaml(data: dict[str, Any]) -> str:
    return yaml.safe_dump(data, allow_unicode=True, sort_keys=False, default_flow_style=False, width=100)


def _read_index(project_id: str) -> dict[str, Any]:
    path = recycle_index_file(project_id)
    if not path.exists():
        return {}
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError:  # pragma: no cover - 坏文件按空处理
        logger.warning("recycle_index_broken", project_id=project_id, path=str(path))
        return {}
    if not isinstance(data, dict):
        return {}
    data.setdefault("chapters", [])
    return data


def _write_index(project_id: str, index: dict[str, Any]) -> None:
    path = recycle_index_file(project_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_dump_yaml(index), encoding="utf-8")


def _drop_entry(project_id: str) -> None:
    """该小说在回收站里已无任何被删章节 → 清掉它的归档目录。"""
    d = recycle_novel_dir(project_id)
    if d.exists():
        shutil.rmtree(d)
    logger.info("recycle_entry_dropped", project_id=project_id)


def _all_entries() -> list[dict[str, Any]]:
    """扫描 `data/recycle/` 下全部条目，按 `updated_at` 倒序（最新在前）。"""
    root = recycle_root()
    entries: list[dict[str, Any]] = []
    if not root.exists():
        return entries
    for d in root.iterdir():
        if not d.is_dir():
            continue
        index = _read_index(d.name)
        if not index.get("novel_id"):
            continue
        entries.append(index)
    entries.sort(key=lambda e: str(e.get("updated_at") or ""), reverse=True)
    return entries


def _enforce_cap() -> None:
    """超出 20 本 → 丢弃最旧的条目（连目录一起删）。"""
    entries = _all_entries()
    if len(entries) <= MAX_NOVELS:
        return
    for stale in entries[MAX_NOVELS:]:
        _drop_entry(str(stale.get("novel_id")))
    logger.info("recycle_cap_enforced", kept=MAX_NOVELS, dropped=len(entries) - MAX_NOVELS)


# ---------------------------------------------------------------------------
# 归档（删除章节 / 删除整本小说时调用）
# ---------------------------------------------------------------------------


def archive_chapter(
    project_id: str,
    *,
    vol_id: str,
    volume_title: str,
    volume_sort_order: int,
    chapter_id: str,
    meta: dict[str, Any],
    novel_title: str,
) -> None:
    """把一个即将被删除的章节归档到回收站。

    原文件整体搬到 `data/recycle/{project_id}/chapters/c{chapter_id}.md`
    （保留 frontmatter，回收时可原样还原），并在 `index.yaml` 里记一条清单。
    """
    src = chapter_file(project_id, vol_id, chapter_id)
    dst = recycle_chapters_dir(project_id) / f"c{chapter_id}.md"
    dst.parent.mkdir(parents=True, exist_ok=True)
    if src.exists():
        shutil.copy2(src, dst)

    index = _read_index(project_id)
    index.update(
        {
            "novel_id": project_id,
            "title": novel_title or index.get("title") or "",
            # 章节级删除不改变「整本已删除」标记
            "novel_deleted": bool(index.get("novel_deleted")),
            "trash_dir": index.get("trash_dir"),
            "updated_at": utcnow(),
        }
    )
    chapters: list[dict[str, Any]] = [
        c for c in (index.get("chapters") or []) if str(c.get("id")) != chapter_id
    ]
    chapters.append(
        {
            "id": chapter_id,
            "title": meta.get("title") or "",
            "volume_id": vol_id,
            "volume_title": volume_title,
            "volume_sort_order": int(volume_sort_order or 0),
            "sort_order": int(meta.get("sort_order") or 0),
            "word_count": int(meta.get("word_count") or 0),
            "deleted_at": utcnow(),
        }
    )
    index["chapters"] = chapters
    _write_index(project_id, index)
    _enforce_cap()
    logger.info("recycle_chapter_archived", project_id=project_id, chapter_id=chapter_id)


def archive_novel(project_id: str, *, novel_title: str, trash_dir: str | None) -> None:
    """整本小说被删除（工作区已移入 `data/trash/`）→ 登记进回收站。

    此时章节随工作区一起进了 trash，回收时按「整本恢复」处理。
    """
    index = _read_index(project_id)
    index.update(
        {
            "novel_id": project_id,
            "title": novel_title or index.get("title") or "",
            "novel_deleted": True,
            "trash_dir": trash_dir or index.get("trash_dir"),
            "updated_at": utcnow(),
        }
    )
    _write_index(project_id, index)
    _enforce_cap()
    logger.info("recycle_novel_archived", project_id=project_id, trash_dir=trash_dir)


# ---------------------------------------------------------------------------
# 浏览
# ---------------------------------------------------------------------------


def _trash_chapter_count(ws: Path | None) -> int:
    """trash 工作区里还剩多少个章节文件（整本被删的小说，章节在 trash 里）。"""
    if ws is None:
        return 0
    chapters_root = ws / "chapters"
    if not chapters_root.exists():
        return 0
    return sum(
        1
        for vdir in chapters_root.iterdir()
        if vdir.is_dir() and vdir.name.startswith("v")
        for _ in vdir.glob("c*.md")
    )


def _novel_dto(index: dict[str, Any]) -> dict[str, Any]:
    chapters = index.get("chapters") or []
    count = len(chapters)
    if bool(index.get("novel_deleted")):
        # ★v1031 续：整本被删的小说，可回收章节在 trash 工作区里；trash 被清干净后计数归 0
        count = _trash_chapter_count(_trash_workspace(str(index.get("novel_id")), index.get("trash_dir")))
    return {
        "novel_id": str(index.get("novel_id")),
        "title": str(index.get("title") or "未命名小说"),
        #: True = 整本小说被删除（工作区在 trash 里），回收任一章节即恢复整本
        "novel_deleted": bool(index.get("novel_deleted")),
        "chapter_count": count,
        "updated_at": index.get("updated_at"),
    }


def _prune_empty_entries() -> None:
    """★v1031 续：卷章已被删干净的条目直接清掉（含整本被删但 trash 已无章节的），
    保证「回收站里卷章删干净 → 该本小说也删除」在所有路径下都成立。"""
    for index in _all_entries():
        nid = str(index.get("novel_id"))
        dto = _novel_dto(index)
        if dto["chapter_count"] > 0:
            continue
        if dto["novel_deleted"]:
            ws = _trash_workspace(nid, index.get("trash_dir"))
            if ws is not None and ws.exists():
                shutil.rmtree(ws)
        _drop_entry(nid)
        logger.info("recycle_entry_pruned_empty", project_id=nid)


def list_recycled_novels() -> list[dict[str, Any]]:
    """回收站首页：最近删除过章节的小说（最多 20 本，最新在前）。"""
    _prune_empty_entries()
    return [_novel_dto(e) for e in _all_entries()[:MAX_NOVELS]]


def _trash_workspace(project_id: str, trash_dir: str | None) -> Path | None:
    """定位整本被删小说在 trash 里的工作区目录。"""
    if trash_dir:
        p = Path(trash_dir)
        if p.exists():
            return p
    root = trash_root()
    if not root.exists():
        return None
    candidates = sorted(
        (d for d in root.iterdir() if d.is_dir() and d.name.startswith(f"{project_id}-")),
        key=lambda d: d.name,
        reverse=True,
    )
    return candidates[0] if candidates else None


def _read_trash_contents(ws: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """从 trash 工作区读取 volumes.yaml 与全部章节（整本被删时的目录视图）。"""
    volumes: list[dict[str, Any]] = []
    vfile = ws / "volumes.yaml"
    if vfile.exists():
        try:
            data = yaml.safe_load(vfile.read_text(encoding="utf-8")) or {}
            volumes = [v for v in (data.get("volumes") or []) if isinstance(v, dict)]
        except yaml.YAMLError:  # pragma: no cover
            volumes = []

    chapters: list[dict[str, Any]] = []
    chapters_root = ws / "chapters"
    if chapters_root.exists():
        for vdir in sorted(chapters_root.iterdir()):
            if not vdir.is_dir() or not vdir.name.startswith("v"):
                continue
            vol_id = vdir.name[1:]
            for path in sorted(vdir.glob("c*.md")):
                chapter_id = path.stem[1:]
                try:
                    import frontmatter

                    post = frontmatter.load(str(path))
                    meta = dict(post.metadata)
                    chapters.append(
                        {
                            "id": chapter_id,
                            "title": meta.get("title") or "",
                            "volume_id": vol_id,
                            "sort_order": int(meta.get("sort_order") or 0),
                            "word_count": int(meta.get("word_count") or 0),
                            "deleted_at": None,
                        }
                    )
                except Exception as exc:  # pragma: no cover - 坏文件跳过
                    logger.warning("recycle_chapter_read_failed", path=str(path), error=str(exc))
    return volumes, chapters


def get_recycled_contents(novel_id: str) -> dict[str, Any]:
    """回收站二级页：某本小说的「卷 → 被删章节」目录。"""
    novel_id = _safe_novel_id(novel_id)
    index = _read_index(novel_id)
    if not index.get("novel_id"):
        raise NotFoundError(f"回收站里没有这本小说：{novel_id}")

    novel_deleted = bool(index.get("novel_deleted"))
    volumes: list[dict[str, Any]] = []
    chapters: list[dict[str, Any]] = []

    if novel_deleted:
        # 整本被删：章节随工作区在 trash 里，直接从 trash 读目录
        ws = _trash_workspace(novel_id, index.get("trash_dir"))
        if ws is not None:
            trashed_vols, trashed_chs = _read_trash_contents(ws)
            volumes = trashed_vols
            chapters = trashed_chs
        # trash 已不在（被手工清理）时退化为 index 里归档过的章节
        if not chapters:
            chapters = list(index.get("chapters") or [])
    else:
        chapters = list(index.get("chapters") or [])

    # 卷分组：卷名/序号优先取清单里的快照
    vol_map: dict[str, dict[str, Any]] = {}
    for v in volumes:
        vid = str(v.get("id"))
        vol_map[vid] = {
            "id": vid,
            "title": str(v.get("title") or ""),
            "sort_order": int(v.get("sort_order") or 0),
        }
    for ch in chapters:
        vid = str(ch.get("volume_id") or "")
        if vid and vid not in vol_map:
            vol_map[vid] = {
                "id": vid,
                "title": str(ch.get("volume_title") or ""),
                "sort_order": int(ch.get("volume_sort_order") or 0),
            }

    ordered_vols = sorted(
        vol_map.values(), key=lambda v: (v["sort_order"], v["title"])
    )
    result = []
    for v in ordered_vols:
        items = sorted(
            (c for c in chapters if str(c.get("volume_id") or "") == v["id"]),
            key=lambda c: (int(c.get("sort_order") or 0), str(c.get("title") or "")),
        )
        if not items:
            continue
        result.append(
            {
                "id": v["id"],
                "title": v["title"],
                "sort_order": v["sort_order"],
                "chapters": items,
                "chapter_count": len(items),
            }
        )

    return {
        **_novel_dto(index),
        "volumes": result,
        "total_chapters": len(chapters),
    }


# ---------------------------------------------------------------------------
# 回收 / 彻底删除
# ---------------------------------------------------------------------------


async def restore_chapter(session: AsyncSession, novel_id: str, chapter_id: str) -> dict[str, Any]:
    """回收一个被删章节。

    - 整本小说被删（工作区在 trash）→ **整本恢复**：工作区搬回 `data/workspaces/`、
      `novels.status` 置回 active，小说项目与它的大纲一并回到左边栏；
    - 仅章节被删 → 章节文件还原到原卷目录，卷若已被删空则按清单快照重建，
      卷内章节重新连续编号。
    """
    novel_id = _safe_novel_id(novel_id)
    chapter_id = _safe_chapter_id(chapter_id)
    index = _read_index(novel_id)
    if not index.get("novel_id"):
        raise NotFoundError(f"回收站里没有这本小说：{novel_id}")

    if bool(index.get("novel_deleted")):
        return await _restore_whole_novel(session, novel_id, index)

    entry = next(
        (c for c in (index.get("chapters") or []) if str(c.get("id")) == chapter_id), None
    )
    if entry is None:
        raise NotFoundError(f"回收站里没有这一章：{chapter_id}")

    src = recycle_chapters_dir(novel_id) / f"c{chapter_id}.md"
    if not src.exists():
        raise NotFoundError(f"回收站里找不到该章节文件：{chapter_id}")

    vol_id = str(entry.get("volume_id") or "")
    if not vol_id:
        raise NotFoundError("回收站记录缺少卷信息，无法还原")

    # 卷在删除最后一章时可能已被级联删除 → 按清单快照重建
    volumes = files.read_volumes(novel_id)
    vol = files.find_volume(volumes, vol_id)
    if vol is None:
        vol = {
            "id": vol_id,
            "title": str(entry.get("volume_title") or "未命名卷"),
            "summary": "",
            "sort_order": max((int(v.get("sort_order") or 0) for v in volumes), default=0) + 1,
            "created_at": utcnow(),
            "updated_at": utcnow(),
        }
        volumes.append(vol)

    # 还原章节文件（保留原 frontmatter，内容一字不差）
    dst = chapter_file(novel_id, vol_id, chapter_id)
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)
    src.unlink()

    # 卷内重新连续编号（1..N）
    files.write_volumes(novel_id, volumes)
    siblings = sorted(
        (
            files.read_chapter_meta(novel_id, vol_id, p.stem[1:])
            | {"id": p.stem[1:]}
            for p in files.list_chapter_files(novel_id, vol_id)
        ),
        key=lambda m: (int(m.get("sort_order") or 0), str(m.get("title") or "")),
    )
    for i, meta in enumerate(siblings, start=1):
        if int(meta.get("sort_order") or 0) != i:
            cid = str(meta["id"])
            full, content = files.read_chapter(novel_id, vol_id, cid)
            full["sort_order"] = i
            files.write_chapter(novel_id, vol_id, cid, full, content)

    # 清单里移除这一章
    index["chapters"] = [
        c for c in (index.get("chapters") or []) if str(c.get("id")) != chapter_id
    ]
    if index["chapters"]:
        index["updated_at"] = utcnow()
        _write_index(novel_id, index)
    else:
        _drop_entry(novel_id)

    await _refresh_index(session, novel_id)
    await audit.record(
        session,
        "recycle.restore",
        entity_type="chapter",
        entity_id=f"{novel_id}:{chapter_id}",
        detail={"title": entry.get("title"), "volume_id": vol_id},
        commit=True,
    )
    logger.info("recycle_chapter_restored", project_id=novel_id, chapter_id=chapter_id)
    return {
        "novel_id": novel_id,
        "chapter_id": chapter_id,
        "restored": True,
        "novel_restored": False,
    }


async def _restore_whole_novel(
    session: AsyncSession, novel_id: str, index: dict[str, Any]
) -> dict[str, Any]:
    """整本恢复：trash 工作区搬回 workspaces + 登记行置回 active。"""
    ws = _trash_workspace(novel_id, index.get("trash_dir"))
    if ws is None:
        raise NotFoundError("回收站里找不到这本小说的工作区，无法恢复")

    target = workspace_dir(novel_id)
    if target.exists():
        shutil.rmtree(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(ws), str(target))

    novel = await session.get(Novel, novel_id)
    if novel is not None:
        novel.status = "active"
        novel.updated_at = utcnow()
    else:  # pragma: no cover - 登记行缺失时按工作区事实源重建
        meta = files.read_novel_meta(novel_id)
        novel = Novel(
            id=novel_id,
            title=str(meta.get("title") or index.get("title") or "未命名小说"),
            workspace_dir=f"workspaces/{novel_id}",
            outline_file=str(meta.get("outline_file") or ""),
            author_name=meta.get("author_name"),
            genre=meta.get("genre"),
            synopsis=meta.get("synopsis"),
            status="active",
        )
        session.add(novel)
    await session.flush()

    _drop_entry(novel_id)
    await _refresh_index(session, novel_id)
    await audit.record(
        session,
        "recycle.restore_novel",
        entity_type="novel",
        entity_id=novel_id,
        detail={"title": index.get("title")},
        commit=True,
    )
    logger.info("recycle_novel_restored", project_id=novel_id)
    return {
        "novel_id": novel_id,
        "chapter_id": None,
        "restored": True,
        "novel_restored": True,
    }


async def purge_chapter(session: AsyncSession, novel_id: str, chapter_id: str) -> dict[str, Any]:
    """彻底删除回收站里的一个章节（不可再恢复）。"""
    novel_id = _safe_novel_id(novel_id)
    chapter_id = _safe_chapter_id(chapter_id)
    index = _read_index(novel_id)
    if not index.get("novel_id"):
        raise NotFoundError(f"回收站里没有这本小说：{novel_id}")

    if bool(index.get("novel_deleted")):
        # 整本被删的小说：章节在 trash 工作区里，彻底删除 = 删掉 trash 里的该章文件
        ws = _trash_workspace(novel_id, index.get("trash_dir"))
        removed = False
        if ws is not None:
            chapters_root = ws / "chapters"
            if chapters_root.exists():
                for vdir in chapters_root.iterdir():
                    path = vdir / f"c{chapter_id}.md"
                    if vdir.is_dir() and path.exists():
                        path.unlink()
                        removed = True
                        break
        if not removed:
            raise NotFoundError(f"回收站里没有这一章：{chapter_id}")
        # ★v1031 续：卷章被删干净 → 这本小说也彻底删除（连同 trash 里的工作区/大纲）
        if _trash_chapter_count(ws) == 0:
            if ws is not None and ws.exists():
                shutil.rmtree(ws)
            _drop_entry(novel_id)
            await audit.record(
                session,
                "recycle.purge",
                entity_type="chapter",
                entity_id=f"{novel_id}:{chapter_id}",
                detail={"chapter_id": chapter_id},
                commit=False,
            )
            await audit.record(
                session,
                "recycle.purge_novel",
                entity_type="novel",
                entity_id=novel_id,
                detail={"title": index.get("title"), "reason": "chapters_all_purged"},
                commit=True,
            )
            logger.info("recycle_novel_purged_empty", project_id=novel_id)
            return {"novel_id": novel_id, "chapter_id": chapter_id, "purged": True}
    else:
        path = recycle_chapters_dir(novel_id) / f"c{chapter_id}.md"
        if not path.exists():
            raise NotFoundError(f"回收站里没有这一章：{chapter_id}")
        path.unlink()

    index["chapters"] = [
        c for c in (index.get("chapters") or []) if str(c.get("id")) != chapter_id
    ]
    if index["chapters"] or index.get("novel_deleted"):
        index["updated_at"] = utcnow()
        _write_index(novel_id, index)
    else:
        _drop_entry(novel_id)

    await audit.record(
        session,
        "recycle.purge",
        entity_type="chapter",
        entity_id=f"{novel_id}:{chapter_id}",
        detail={"chapter_id": chapter_id},
        commit=True,
    )
    logger.info("recycle_chapter_purged", project_id=novel_id, chapter_id=chapter_id)
    return {"novel_id": novel_id, "chapter_id": chapter_id, "purged": True}


async def _refresh_index(session: AsyncSession, project_id: str) -> None:
    """恢复后刷新派生索引（章数 / 字数）。"""
    idx = await session.get(ProjectIndex, project_id)
    if idx is None:
        idx = ProjectIndex(project_id=project_id)
        session.add(idx)
    count = 0
    words = 0
    for vol in files.read_volumes(project_id):
        vol_id = str(vol.get("id"))
        for path in files.list_chapter_files(project_id, vol_id):
            count += 1
            try:
                meta = files.read_chapter_meta(project_id, vol_id, path.stem[1:])
                words += int(meta.get("word_count") or 0)
            except Exception:  # pragma: no cover - 坏文件按 0 字
                continue
    idx.chapter_count = count
    idx.total_words = words
    idx.last_updated = utcnow()
    idx.dirty = 0
    await session.flush()


def find_novel_title(session_novel: Novel | None, project_id: str) -> str:
    """归档时取书名快照（登记行缺失就回落工作区 novel.yaml）。"""
    if session_novel is not None and session_novel.title:
        return session_novel.title
    return str(files.read_novel_meta(project_id).get("title") or "")


async def get_novel_title(session: AsyncSession, project_id: str) -> str:
    """按 project_id 读书名（含已软删除的登记行）。"""
    row = (await session.execute(select(Novel).where(Novel.id == project_id))).scalar_one_or_none()
    return find_novel_title(row, project_id)
