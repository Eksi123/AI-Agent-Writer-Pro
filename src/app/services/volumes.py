"""卷 / 章服务：CRUD、正文读写（乐观锁）、修订快照与回滚。

手册约束：
- **短 ID 永不变**（`v{vol_id}` / `c{chapter_id}`），文件名与目录名含 ID；
- **顺序只按创建先后追加**（`sort_order = max+1`），★无重排接口、无跨卷移动；
  顺序的唯一派生写入口是 `sync_order_from_outline`（M4）；
- 显式命名（42201）；
- 正文写入乐观锁：`base_version` 不符 → 409 / `40901` 并回传最新内容；
- 修订快照触发：手动保存 / Agent 写章前 / 章节定稿；**自动保存不快照**；
- 删除卷级联删除其下章节文件。
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.errors import NotFoundError, VersionConflictError
from app.core.logging import get_logger
from app.core.paths import chapter_file, volume_dir
from app.db.base import short_id, utcnow
from app.db.models import ChapterRevision
from app.services import audit, files, novels
from app.services.naming import validate_chapter_title, validate_volume_title
from app.services.workspace import WorkspaceGuard

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# 卷
# ---------------------------------------------------------------------------


def _vol_dto(vol: dict[str, Any], chapters: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    dto = {
        "id": vol.get("id"),
        "title": vol.get("title"),
        "summary": vol.get("summary") or "",
        "sort_order": int(vol.get("sort_order") or 0),
        "created_at": vol.get("created_at"),
        "updated_at": vol.get("updated_at"),
    }
    if chapters is not None:
        dto["chapters"] = chapters
        dto["chapter_count"] = len(chapters)
        dto["word_count"] = sum(int(c.get("word_count") or 0) for c in chapters)
    return dto


def _chapter_dto(meta: dict[str, Any], chapter_id: str, vol_id: str) -> dict[str, Any]:
    return {
        "id": chapter_id,
        "volume_id": vol_id,
        "title": meta.get("title"),
        "sort_order": int(meta.get("sort_order") or 0),
        "word_count": int(meta.get("word_count") or 0),
        "status": meta.get("status") or "draft",
        "version": int(meta.get("version") or 1),
        "updated_at": meta.get("updated_at"),
    }


async def list_volumes(session: AsyncSession, project_id: str) -> list[dict[str, Any]]:
    """卷列表（含各卷章节）。按 `sort_order` 只读排序渲染。"""
    await novels.get_novel(session, project_id)  # 404 校验
    volumes = sorted(
        files.read_volumes(project_id),
        key=lambda v: (int(v.get("sort_order") or 0), str(v.get("title") or "")),
    )
    result = []
    for vol in volumes:
        vol_id = str(vol.get("id"))
        chapters = await list_chapters(session, project_id, vol_id)
        result.append(_vol_dto(vol, chapters))
    return result


async def create_volume(
    session: AsyncSession, project_id: str, *, title: str, summary: str = ""
) -> dict[str, Any]:
    """新建卷：**显式命名**，追加到末尾（`sort_order = max+1`）。"""
    await novels.get_novel(session, project_id)
    clean = validate_volume_title(title)
    volumes = files.read_volumes(project_id)

    vol_id = short_id({str(v.get("id")) for v in volumes})
    now = utcnow()
    vol = {
        "id": vol_id,
        "title": clean,
        "summary": summary or "",
        "sort_order": files.next_volume_sort(volumes),
        "created_at": now,
        "updated_at": now,
    }
    volumes.append(vol)
    files.write_volumes(project_id, volumes)
    volume_dir(project_id, vol_id).mkdir(parents=True, exist_ok=True)

    await audit.record(
        session,
        "volume.create",
        entity_type="volume",
        entity_id=f"{project_id}:{vol_id}",
        detail={"title": clean, "sort_order": vol["sort_order"]},
        commit=True,
    )
    logger.info("volume_created", project_id=project_id, volume_id=vol_id, title=clean)
    return _vol_dto(vol, [])


async def update_volume(
    session: AsyncSession, vol_id: str, *, title: str | None = None, summary: str | None = None
) -> dict[str, Any]:
    """改卷名 / 简介。**不改 ID、不改目录名、不改 sort_order**。"""
    project_id, volumes, vol = _locate_volume(vol_id)
    changes: dict[str, Any] = {}

    if title is not None:
        clean = validate_volume_title(title)
        if clean != vol.get("title"):
            changes["title"] = {"from": vol.get("title"), "to": clean}
            vol["title"] = clean
    if summary is not None and summary != (vol.get("summary") or ""):
        changes["summary"] = summary
        vol["summary"] = summary

    if not changes:
        return _vol_dto(vol, await list_chapters(session, project_id, vol_id))

    vol["updated_at"] = utcnow()
    files.write_volumes(project_id, volumes)
    await audit.record(
        session,
        "volume.update",
        entity_type="volume",
        entity_id=f"{project_id}:{vol_id}",
        detail=changes,
        commit=True,
    )
    return _vol_dto(vol, await list_chapters(session, project_id, vol_id))


async def delete_volume(session: AsyncSession, vol_id: str) -> dict[str, Any]:
    """删除卷：**级联删除其下章节文件**（前端二次确认列出章名与字数）。"""
    project_id, volumes, vol = _locate_volume(vol_id)
    guard = WorkspaceGuard(project_id)

    chapters = await list_chapters(session, project_id, vol_id)
    removed_files: list[str] = []
    for ch in chapters:
        rel = f"chapters/v{vol_id}/c{ch['id']}.md"
        if guard.exists(rel):
            guard.delete(rel)
            removed_files.append(rel)

    # 卷目录本身
    vdir = volume_dir(project_id, vol_id)
    if vdir.exists():
        guard.delete(f"chapters/v{vol_id}")

    volumes = [v for v in volumes if str(v.get("id")) != vol_id]
    files.write_volumes(project_id, volumes)

    await novels.refresh_project_index(session, project_id)
    await audit.record(
        session,
        "volume.delete",
        entity_type="volume",
        entity_id=f"{project_id}:{vol_id}",
        detail={"title": vol.get("title"), "chapters_deleted": len(removed_files)},
        commit=True,
    )
    logger.info(
        "volume_deleted", project_id=project_id, volume_id=vol_id, chapters=len(removed_files)
    )
    return {"id": vol_id, "deleted": True, "chapters_deleted": len(removed_files)}


def _locate_volume(vol_id: str) -> tuple[str, list[dict[str, Any]], dict[str, Any]]:
    """由 vol_id 反查 project_id 与卷记录。

    卷 ID 全局唯一（uuid 前 8 位），扫描各工作区的 volumes.yaml 定位。
    """
    from app.core.paths import workspaces_root

    for ws in workspaces_root().iterdir() if workspaces_root().exists() else []:
        if not ws.is_dir():
            continue
        vfile = ws / "volumes.yaml"
        if not vfile.exists():
            continue
        volumes = files.read_volumes(ws.name)
        vol = files.find_volume(volumes, vol_id)
        if vol is not None:
            return ws.name, volumes, vol
    raise NotFoundError(f"卷不存在：{vol_id}")


def locate_volume(vol_id: str) -> tuple[str, list[dict[str, Any]], dict[str, Any]]:
    """公开入口：由 vol_id 反查 `(project_id, volumes, volume)`。"""
    return _locate_volume(vol_id)


def locate_chapter(chapter_id: str) -> tuple[str, str, dict[str, Any], str]:
    """公开入口：由 chapter_id 反查 `(project_id, vol_id, meta, content)`。"""
    return _locate_chapter(chapter_id)


# ---------------------------------------------------------------------------
# 章
# ---------------------------------------------------------------------------


async def list_chapters(session: AsyncSession, project_id: str, vol_id: str) -> list[dict[str, Any]]:
    """章列表：以 volumes.yaml 所属卷目录下的文件为准，按 `sort_order` 只读排序。"""
    result: list[dict[str, Any]] = []
    for path in files.list_chapter_files(project_id, vol_id):
        chapter_id = path.stem[1:]  # 去掉前缀 'c'
        try:
            meta = files.read_chapter_meta(project_id, vol_id, chapter_id)
        except Exception as exc:  # pragma: no cover - 坏文件跳过并告警
            logger.warning("chapter_meta_failed", path=str(path), error=str(exc))
            continue
        result.append(_chapter_dto(meta, chapter_id, vol_id))
    result.sort(key=lambda c: (c["sort_order"], c["title"] or ""))
    return result


async def create_chapter(
    session: AsyncSession, vol_id: str, *, title: str, content: str = ""
) -> dict[str, Any]:
    """新建章：**显式命名**，追加到卷末尾（`sort_order = max+1`）。"""
    project_id, _volumes, vol = _locate_volume(vol_id)
    clean = validate_chapter_title(title)

    existing = await list_chapters(session, project_id, vol_id)
    taken = {c["id"] for c in existing}
    chapter_id = short_id(taken)
    sort_order = max((c["sort_order"] for c in existing), default=0) + 1

    now = utcnow()
    meta = {
        "title": clean,
        "sort_order": sort_order,
        "word_count": files.count_words(content),
        "status": "draft",
        "version": 1,
        "updated_at": now,
        "created_at": now,
        "volume_id": vol_id,
    }
    files.write_chapter(project_id, vol_id, chapter_id, meta, content)

    await novels.refresh_project_index(session, project_id)
    await audit.record(
        session,
        "chapter.create",
        entity_type="chapter",
        entity_id=f"{project_id}:{chapter_id}",
        detail={"title": clean, "volume_id": vol_id, "sort_order": sort_order},
        commit=True,
    )
    logger.info("chapter_created", project_id=project_id, chapter_id=chapter_id, title=clean)
    return _chapter_dto(meta, chapter_id, vol_id)


def _locate_chapter(chapter_id: str) -> tuple[str, str, dict[str, Any], str]:
    """由 chapter_id 反查 `(project_id, vol_id, meta, content)`。"""
    from app.core.paths import workspaces_root

    root = workspaces_root()
    if not root.exists():
        raise NotFoundError(f"章节不存在：{chapter_id}")

    for ws in root.iterdir():
        if not ws.is_dir():
            continue
        chapters_root = ws / "chapters"
        if not chapters_root.exists():
            continue
        for vdir in chapters_root.iterdir():
            if not vdir.is_dir() or not vdir.name.startswith("v"):
                continue
            path = vdir / f"c{chapter_id}.md"
            if path.exists():
                vol_id = vdir.name[1:]
                meta, content = files.read_chapter(ws.name, vol_id, chapter_id)
                return ws.name, vol_id, meta, content
    raise NotFoundError(f"章节不存在：{chapter_id}")


async def update_chapter_meta(
    session: AsyncSession,
    chapter_id: str,
    *,
    title: str | None = None,
    status: str | None = None,
) -> dict[str, Any]:
    """改章名 / 状态（draft|finalized）。**不改 ID、文件名、sort_order**。

    定稿（status → finalized）会触发一次修订快照（手册「五」触发时机）。
    """
    project_id, vol_id, meta, content = _locate_chapter(chapter_id)
    changes: dict[str, Any] = {}

    if title is not None:
        clean = validate_chapter_title(title)
        if clean != meta.get("title"):
            changes["title"] = {"from": meta.get("title"), "to": clean}
            meta["title"] = clean

    finalize = False
    if status is not None:
        if status not in ("draft", "finalized"):
            from app.core.errors import BadRequestError

            raise BadRequestError(f"非法章节状态：{status}（只允许 draft|finalized）")
        if status != meta.get("status"):
            changes["status"] = {"from": meta.get("status"), "to": status}
            finalize = status == "finalized" and meta.get("status") != "finalized"
            meta["status"] = status

    if not changes:
        return _chapter_dto(meta, chapter_id, vol_id)

    if finalize:
        await _snapshot(session, project_id, chapter_id, meta, content, source="manual")

    meta["updated_at"] = utcnow()
    files.write_chapter(project_id, vol_id, chapter_id, meta, content)

    await audit.record(
        session,
        "chapter.update",
        entity_type="chapter",
        entity_id=f"{project_id}:{chapter_id}",
        detail=changes,
        commit=True,
    )
    return _chapter_dto(meta, chapter_id, vol_id)


async def delete_chapter(session: AsyncSession, chapter_id: str) -> dict[str, Any]:
    """删除章文件。★不提供跨卷移动——换卷需新建同名章复制正文再删旧章。"""
    project_id, vol_id, meta, _content = _locate_chapter(chapter_id)
    guard = WorkspaceGuard(project_id)
    rel = f"chapters/v{vol_id}/c{chapter_id}.md"
    guard.delete(rel)

    # 该章的修订快照一并清理（硬删除，仅项目有软删除）
    rows = (
        await session.execute(
            select(ChapterRevision).where(ChapterRevision.chapter_id == chapter_id)
        )
    ).scalars().all()
    for r in rows:
        await session.delete(r)

    await novels.refresh_project_index(session, project_id)
    await audit.record(
        session,
        "chapter.delete",
        entity_type="chapter",
        entity_id=f"{project_id}:{chapter_id}",
        detail={"title": meta.get("title"), "volume_id": vol_id},
        commit=True,
    )
    return {"id": chapter_id, "deleted": True}


# ---------------------------------------------------------------------------
# 正文读写 + 乐观锁
# ---------------------------------------------------------------------------


async def read_content(session: AsyncSession, chapter_id: str) -> dict[str, Any]:
    """读正文。返回内容 + `version`（前端保存时须回传为 `base_version`）。"""
    project_id, vol_id, meta, content = _locate_chapter(chapter_id)
    return {
        "id": chapter_id,
        "volume_id": vol_id,
        "project_id": project_id,
        "title": meta.get("title"),
        "content": content,
        "word_count": int(meta.get("word_count") or 0),
        "status": meta.get("status") or "draft",
        "version": int(meta.get("version") or 1),
        "updated_at": meta.get("updated_at"),
        "last_writer": meta.get("last_writer") or "manual",
        "path": str(chapter_file(project_id, vol_id, chapter_id)),
    }


async def write_content(
    session: AsyncSession,
    chapter_id: str,
    *,
    content: str,
    base_version: int,
    source: str = "manual",
    is_autosave: bool = False,
    run_id: str | None = None,
) -> dict[str, Any]:
    """写正文（乐观锁）。

    - `base_version` 与文件 frontmatter `version` 不一致 → 409 / `40901`，回传最新内容；
    - 成功则 `version += 1`；**手动保存 / Agent 写入 / 定稿**先快照旧内容，自动保存不快照；
    - `source`: manual | agent。
    """
    project_id, vol_id, meta, old_content = _locate_chapter(chapter_id)
    current_version = int(meta.get("version") or 1)

    if base_version != current_version:
        raise VersionConflictError(
            "版本冲突：AI 或其它端已修改本章，请选择合并或覆盖",
            data={
                "id": chapter_id,
                "content": old_content,
                "version": current_version,
                "updated_at": meta.get("updated_at"),
                "last_writer": meta.get("last_writer") or "manual",
                "base_version": base_version,
            },
        )

    # 快照旧内容（自动保存不快照，防版本爆炸）
    if not is_autosave:
        await _snapshot(session, project_id, chapter_id, meta, old_content, source=source, run_id=run_id)

    new_version = current_version + 1
    meta.update(
        {
            "version": new_version,
            "word_count": files.count_words(content),
            "updated_at": utcnow(),
            "last_writer": source,
        }
    )
    files.write_chapter(project_id, vol_id, chapter_id, meta, content)

    await novels.refresh_project_index(session, project_id)
    await audit.record(
        session,
        "chapter.content.write",
        entity_type="chapter",
        entity_id=f"{project_id}:{chapter_id}",
        detail={
            "version": new_version,
            "source": source,
            "autosave": is_autosave,
            "word_count": meta["word_count"],
            "run_id": run_id,
        },
        commit=True,
    )
    return {
        "id": chapter_id,
        "volume_id": vol_id,
        "version": new_version,
        "word_count": meta["word_count"],
        "updated_at": meta["updated_at"],
        "last_writer": source,
        "snapshotted": not is_autosave,
    }


# ---------------------------------------------------------------------------
# 修订快照
# ---------------------------------------------------------------------------


async def _snapshot(
    session: AsyncSession,
    project_id: str,
    chapter_id: str,
    meta: dict[str, Any],
    content: str,
    *,
    source: str,
    run_id: str | None = None,
) -> ChapterRevision:
    """把当前内容快照进 `chapter_revisions`，并按保留策略清理最旧的非定稿快照。"""
    from app.db.base import new_id

    rev = ChapterRevision(
        id=new_id(),
        novel_id=project_id,
        chapter_id=chapter_id,
        version=int(meta.get("version") or 1),
        content_md=content,
        word_count=files.count_words(content),
        source=source,
        run_id=run_id,
        created_at=utcnow(),
    )
    session.add(rev)
    await session.flush()
    await _prune_revisions(session, chapter_id)
    return rev


async def _prune_revisions(session: AsyncSession, chapter_id: str) -> None:
    """超过保留上限时清理**最旧的非定稿**快照（定稿快照永不清理）。"""
    keep = max(1, get_settings().chapter_revision_keep)
    rows = (
        await session.execute(
            select(ChapterRevision)
            .where(ChapterRevision.chapter_id == chapter_id)
            .order_by(ChapterRevision.created_at.desc())
        )
    ).scalars().all()
    if len(rows) <= keep:
        return
    # 保留最新 keep 条；其余中，非定稿（source != finalized 标记）者删除
    for rev in rows[keep:]:
        if (rev.source or "") == "finalized":
            continue
        await session.delete(rev)


async def list_revisions(session: AsyncSession, chapter_id: str) -> list[dict[str, Any]]:
    """修订快照列表（不含正文全文，减小载荷；正文由 restore / detail 取）。"""
    project_id, vol_id, _meta, _content = _locate_chapter(chapter_id)
    rows = (
        await session.execute(
            select(ChapterRevision)
            .where(ChapterRevision.chapter_id == chapter_id)
            .order_by(ChapterRevision.created_at.desc())
        )
    ).scalars().all()
    return [
        {
            "id": r.id,
            "chapter_id": r.chapter_id,
            "project_id": project_id,
            "volume_id": vol_id,
            "version": r.version,
            "word_count": r.word_count,
            "source": r.source,
            "run_id": r.run_id,
            "created_at": r.created_at,
            "preview": (r.content_md or "")[:120],
        }
        for r in rows
    ]


async def restore_revision(
    session: AsyncSession, chapter_id: str, revision_id: str
) -> dict[str, Any]:
    """回滚到指定快照：先把当前内容快照一份，再用快照内容覆盖，`version` 继续自增。"""
    project_id, vol_id, meta, current = _locate_chapter(chapter_id)

    rev = await session.get(ChapterRevision, revision_id)
    if rev is None or rev.chapter_id != chapter_id:
        raise NotFoundError(f"修订快照不存在：{revision_id}")

    await _snapshot(session, project_id, chapter_id, meta, current, source="manual")

    new_version = int(meta.get("version") or 1) + 1
    meta.update(
        {
            "version": new_version,
            "word_count": files.count_words(rev.content_md),
            "updated_at": utcnow(),
            "last_writer": "manual",
        }
    )
    files.write_chapter(project_id, vol_id, chapter_id, meta, rev.content_md)

    await novels.refresh_project_index(session, project_id)
    await audit.record(
        session,
        "chapter.revision.restore",
        entity_type="chapter",
        entity_id=f"{project_id}:{chapter_id}",
        detail={"revision_id": revision_id, "from_version": rev.version, "to_version": new_version},
        commit=True,
    )
    return {
        "id": chapter_id,
        "volume_id": vol_id,
        "version": new_version,
        "content": rev.content_md,
        "word_count": meta["word_count"],
        "restored_from": revision_id,
    }


# ---------------------------------------------------------------------------
# 大纲 → 顺序派生（M4 接线；M1 保持顺序为创建先后）
# ---------------------------------------------------------------------------


def assert_no_manual_reorder() -> None:
    """★设计守卫：本模块**不提供**任何手工重排 / 跨卷移动能力。

    顺序只能由 `sync_order_from_outline` 从大纲派生（见手册「四」）。
    该函数供测试断言「无 reorder/move 入口」时调用，也作为文档化契约。
    """
    forbidden = {"reorder_volumes", "reorder_chapters", "move_chapter", "move_volume"}
    leaked = forbidden & set(globals())
    if leaked:  # pragma: no cover - 契约破坏时立即失败
        raise AssertionError(f"违反铁律 10：出现了手工重排函数 {leaked}")
