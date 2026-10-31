# -*- coding: utf-8 -*-
"""
模块: app.services.novels
文件名: novels.py
作者: xionggaoqing
时间: 2026-10-27
说明: 小说项目服务：CRUD、软删除、重命名（同步重命名大纲文件）、统计缓存。

    手册要点：
    - 创建时：① 写 `novels` 登记行（生成 `project_id`=uuid）② 初始化工作区 ③ 写入大纲模板文件；
    - **显式命名**（铁律 6，违规 42201）；
    - 删除为**软删除**：工作区移入 `data/trash/{id}-{ts}/` + `status="deleted"`；
    - `outline_file` 是大纲唯一寻址依据（重命名事务内同步改文件名 + novel.yaml + DB）；
    - DB 是文件值的缓存，不一致时以文件为准（铁律 3）。
"""

from __future__ import annotations

import shutil
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError
from app.core.logging import get_logger
from app.core.paths import (
    init_workspace,
    outline_relpath,
    safe_filename,
    trash_root,
    workspace_dir,
)
from app.db.base import new_id, utcnow
from app.db.models import Novel, ProjectIndex
from app.services import audit, files, recycle
from app.services.naming import validate_novel_title
from app.services.outline import render_template
from app.services.workspace import WorkspaceGuard

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# 创建
# ---------------------------------------------------------------------------


async def create_novel(
    session: AsyncSession,
    *,
    title: str,
    author_name: str | None = None,
    genre: str | None = None,
    synopsis: str | None = None,
) -> dict[str, Any]:
    """创建小说项目：登记行 + 工作区 + 大纲模板。

    Raises:
        NamingInvalidError: 标题不合法（42201）。
    """
    clean_title = validate_novel_title(title)
    project_id = new_id()

    # ① 初始化工作区目录结构
    ws = init_workspace(project_id)
    guard = WorkspaceGuard(project_id)

    # ② 写入大纲模板文件（服务层常量，无需用户操作）
    outline_rel = outline_relpath(clean_title)
    guard.write(outline_rel, render_template(clean_title))

    # ③ 写 novel.yaml（项目元信息事实源）
    meta = {
        "id": project_id,
        "title": clean_title,
        "author_name": author_name or "",
        "genre": genre or "",
        "synopsis": synopsis or "",
        "cover_url": "",
        "status": "active",
        "llm_params": None,
        "outline_file": outline_rel,
        "created_at": utcnow(),
        "updated_at": utcnow(),
    }
    files.write_novel_meta(project_id, meta)

    # ④ 空的 volumes.yaml
    files.write_volumes(project_id, [])

    # ⑤ DB 登记行 + project_index 缓存
    novel = Novel(
        id=project_id,
        title=clean_title,
        workspace_dir=f"workspaces/{project_id}",
        outline_file=outline_rel,
        author_name=author_name,
        genre=genre,
        synopsis=synopsis,
        status="active",
    )
    session.add(novel)
    session.add(
        ProjectIndex(
            project_id=project_id,
            chapter_count=0,
            total_words=0,
            last_updated=utcnow(),
            dirty=0,
        )
    )
    await session.flush()

    await audit.record(
        session,
        "novel.create",
        entity_type="novel",
        entity_id=project_id,
        detail={"title": clean_title, "workspace": str(ws)},
    )
    logger.info("novel_created", project_id=project_id, title=clean_title)
    return to_dto(novel, chapter_count=0, total_words=0)


# ---------------------------------------------------------------------------
# 查询
# ---------------------------------------------------------------------------


def to_dto(novel: Novel, *, chapter_count: int = 0, total_words: int = 0) -> dict[str, Any]:
    return {
        "id": novel.id,
        "title": novel.title,
        "author_name": novel.author_name,
        "genre": novel.genre,
        "synopsis": novel.synopsis,
        "status": novel.status,
        "outline_file": novel.outline_file,
        "workspace_dir": novel.workspace_dir,
        "chapter_count": chapter_count,
        "total_words": total_words,
        "created_at": novel.created_at,
        "updated_at": novel.updated_at,
    }


async def list_novels(session: AsyncSession, *, include_deleted: bool = False) -> list[dict[str, Any]]:
    """项目列表（含 project_index 统计）。默认排除软删除。"""
    stmt = select(Novel)
    if not include_deleted:
        stmt = stmt.where(Novel.status == "active")
    stmt = stmt.order_by(Novel.updated_at.desc())
    novels = list((await session.execute(stmt)).scalars().all())
    if not novels:
        return []

    ids = [n.id for n in novels]
    rows = (
        await session.execute(
            select(
                ProjectIndex.project_id,
                ProjectIndex.chapter_count,
                ProjectIndex.total_words,
            ).where(ProjectIndex.project_id.in_(ids))
        )
    ).all()
    stats = {r[0]: (r[1] or 0, r[2] or 0) for r in rows}

    return [
        to_dto(n, chapter_count=stats.get(n.id, (0, 0))[0], total_words=stats.get(n.id, (0, 0))[1])
        for n in novels
    ]


async def get_novel(session: AsyncSession, novel_id: str) -> Novel:
    """取未删除的项目行；不存在或已删除抛 40401。"""
    novel = await session.get(Novel, novel_id)
    if novel is None or novel.status == "deleted":
        raise NotFoundError(f"小说项目不存在：{novel_id}")
    return novel


async def get_novel_dto(session: AsyncSession, novel_id: str) -> dict[str, Any]:
    novel = await get_novel(session, novel_id)
    idx = await session.get(ProjectIndex, novel_id)
    return to_dto(
        novel,
        chapter_count=idx.chapter_count if idx else 0,
        total_words=idx.total_words if idx else 0,
    )


# ---------------------------------------------------------------------------
# 修改
# ---------------------------------------------------------------------------


async def update_novel(
    session: AsyncSession,
    novel_id: str,
    *,
    title: str | None = None,
    author_name: str | None = None,
    genre: str | None = None,
    synopsis: str | None = None,
) -> dict[str, Any]:
    """改元信息。**重命名在事务内同步重命名大纲文件**并更新所有引用。"""
    novel = await get_novel(session, novel_id)
    guard = WorkspaceGuard(novel_id)
    changes: dict[str, Any] = {}

    if title is not None:
        new_title = validate_novel_title(title)
        if new_title != novel.title:
            old_rel = novel.outline_file
            new_rel = outline_relpath(new_title)
            _rename_outline_file(guard, old_rel, new_rel)
            novel.title = new_title
            novel.outline_file = new_rel
            changes["title"] = {"from": old_rel, "to": new_rel}

    for field_name, value in (("author_name", author_name), ("genre", genre), ("synopsis", synopsis)):
        if value is not None:
            setattr(novel, field_name, value)
            changes[field_name] = value

    novel.updated_at = utcnow()

    # 同步 novel.yaml（文件为事实源，DB 是缓存）
    meta = files.read_novel_meta(novel_id)
    meta.update(
        {
            "title": novel.title,
            "author_name": novel.author_name or "",
            "genre": novel.genre or "",
            "synopsis": novel.synopsis or "",
            "outline_file": novel.outline_file,
            "updated_at": utcnow(),
        }
    )
    files.write_novel_meta(novel_id, meta)

    await session.flush()
    if changes:
        await audit.record(
            session, "novel.update", entity_type="novel", entity_id=novel_id, detail=changes
        )
    idx = await session.get(ProjectIndex, novel_id)
    return to_dto(
        novel,
        chapter_count=idx.chapter_count if idx else 0,
        total_words=idx.total_words if idx else 0,
    )


def _rename_outline_file(guard: WorkspaceGuard, old_rel: str, new_rel: str) -> None:
    """重命名大纲文件。目标已存在则不覆盖（避免丢数据），保留旧文件并抛错。"""
    old_path = guard.resolve(old_rel)
    new_path = guard.resolve(new_rel)
    if not old_path.exists():
        # 旧文件缺失：直接写模板，保证 outline_file 寻址有效
        new_path.parent.mkdir(parents=True, exist_ok=True)
        new_path.write_text(render_template(""), encoding="utf-8")
        return
    if new_path.exists():
        from app.core.errors import VersionConflictError

        raise VersionConflictError(f"目标大纲文件已存在，无法重命名：{new_rel}")
    new_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(old_path), str(new_path))


# ---------------------------------------------------------------------------
# 软删除
# ---------------------------------------------------------------------------


async def delete_novel(session: AsyncSession, novel_id: str) -> dict[str, Any]:
    """软删除：工作区移入 `data/trash/{id}-{ts}/` + `status="deleted"`。

    保留可恢复窗口；★v1031：登记进回收站（可在回收站里恢复整本小说及其大纲）。
    """
    novel = await get_novel(session, novel_id)
    ts = datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
    trash_dir = trash_root() / f"{novel_id}-{ts}"

    ws = workspace_dir(novel_id)
    moved = False
    if ws.exists():
        trash_dir.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(ws), str(trash_dir))
        moved = True

    novel.status = "deleted"
    novel.updated_at = utcnow()
    await session.flush()

    # ★v1031：进回收站（整本被删 → 回收其中任一章节时连同小说项目与大纲一起恢复）
    recycle.archive_novel(
        novel_id, novel_title=novel.title, trash_dir=str(trash_dir) if moved else None
    )

    await audit.record(
        session,
        "novel.delete",
        entity_type="novel",
        entity_id=novel_id,
        detail={"title": novel.title, "trash": str(trash_dir) if moved else None},
    )
    logger.info("novel_soft_deleted", project_id=novel_id, trash=str(trash_dir))
    return {"id": novel_id, "status": "deleted", "trash_dir": str(trash_dir) if moved else None}


# ---------------------------------------------------------------------------
# 复制 / 统计
# ---------------------------------------------------------------------------


async def duplicate_novel(session: AsyncSession, novel_id: str, *, new_title: str) -> dict[str, Any]:
    """复制项目：深拷贝工作区 + 新 project_id + 新登记行。"""
    src = await get_novel(session, novel_id)
    clean_title = validate_novel_title(new_title)

    new_id_ = new_id()
    src_ws = workspace_dir(src.id)
    dst_ws = init_workspace(new_id_)
    if src_ws.exists():
        for item in src_ws.iterdir():
            target = dst_ws / item.name
            if item.is_dir():
                shutil.copytree(item, target, dirs_exist_ok=True)
            else:
                shutil.copy2(item, target)

    # 修正副本元信息与大纲文件名
    outline_rel = outline_relpath(clean_title)
    guard = WorkspaceGuard(new_id_)
    files.write_novel_meta(
        new_id_,
        {
            "id": new_id_,
            "title": clean_title,
            "author_name": src.author_name or "",
            "genre": src.genre or "",
            "synopsis": src.synopsis or "",
            "cover_url": "",
            "status": "active",
            "llm_params": None,
            "outline_file": outline_rel,
            "created_at": utcnow(),
            "updated_at": utcnow(),
        },
    )
    old_outline = dst_ws / (src.outline_file or "")
    new_outline = guard.resolve(outline_rel)
    if old_outline.exists() and old_outline.resolve() != new_outline:
        shutil.move(str(old_outline), str(new_outline))

    novel = Novel(
        id=new_id_,
        title=clean_title,
        workspace_dir=f"workspaces/{new_id_}",
        outline_file=outline_rel,
        author_name=src.author_name,
        genre=src.genre,
        synopsis=src.synopsis,
        status="active",
    )
    session.add(novel)
    await refresh_project_index(session, new_id_)
    await session.flush()
    await audit.record(
        session, "novel.duplicate", entity_type="novel", entity_id=new_id_, detail={"from": src.id}
    )
    return await get_novel_dto(session, new_id_)


async def refresh_project_index(session: AsyncSession, project_id: str) -> ProjectIndex:
    """从工作区文件重算统计缓存（chapter_count / total_words）。"""
    volumes = files.read_volumes(project_id)
    chapter_count = 0
    total_words = 0
    for vol in volumes:
        vol_id = str(vol.get("id") or "")
        for path in files.list_chapter_files(project_id, vol_id):
            chapter_count += 1
            try:
                meta = files.read_chapter_meta(project_id, vol_id, path.stem[1:])
                total_words += int(meta.get("word_count") or 0)
            except Exception:  # pragma: no cover - 单个坏文件不阻断统计
                logger.warning("chapter_meta_read_failed", path=str(path))

    idx = await session.get(ProjectIndex, project_id)
    if idx is None:
        idx = ProjectIndex(project_id=project_id)
        session.add(idx)
    idx.chapter_count = chapter_count
    idx.total_words = total_words
    idx.last_updated = utcnow()
    idx.dirty = 0
    await session.flush()
    return idx


async def count_words_total(session: AsyncSession, project_id: str) -> int:
    row = await session.execute(
        select(func.coalesce(func.sum(ProjectIndex.total_words), 0)).where(
            ProjectIndex.project_id == project_id
        )
    )
    return int(row.scalar() or 0)


def workspace_summary(project_id: str) -> dict[str, Any]:
    """工作区自检信息（运维/排障用）。"""
    ws = workspace_dir(project_id)
    return {
        "project_id": project_id,
        "workspace": str(ws),
        "exists": ws.exists(),
        "outline_file": files.read_novel_meta(project_id).get("outline_file"),
        "safe_name": safe_filename(files.read_novel_meta(project_id).get("title") or ""),
    }
