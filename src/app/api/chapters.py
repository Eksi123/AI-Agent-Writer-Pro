"""正文读写（乐观锁）、修订快照与回滚（手册「六 → 路由树 · 卷章」）。

★`PUT content` 必带 `base_version`：不符返回 409 / `40901` 并回传最新内容。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_session
from app.core.response import ok
from app.schemas import ContentIn
from app.services import volumes

router = APIRouter(prefix="/chapters", tags=["chapters"])


@router.get("/{chapter_id}/content")
async def get_content(chapter_id: str, session: AsyncSession = Depends(get_session)) -> dict:
    """读正文，返回 `version` 供前端保存时回传为 `base_version`。"""
    return ok(await volumes.read_content(session, chapter_id))


@router.put("/{chapter_id}/content")
async def put_content(
    chapter_id: str, body: ContentIn, session: AsyncSession = Depends(get_session)
) -> dict:
    """写正文（乐观锁）。

    - `base_version` 与文件 `version` 不一致 → 409 / `40901`，`data` 内含最新内容；
    - 成功则 `version += 1`，手动保存产生 `chapter_revisions` 快照，`autosave` 不快照。
    """
    dto = await volumes.write_content(
        session,
        chapter_id,
        content=body.content,
        base_version=body.base_version,
        source="manual",
        is_autosave=body.autosave,
    )
    await session.commit()
    return ok(dto)


@router.get("/{chapter_id}/revisions")
async def list_revisions(chapter_id: str, session: AsyncSession = Depends(get_session)) -> dict:
    """修订快照列表（不含正文全文，只给 preview）。"""
    return ok(await volumes.list_revisions(session, chapter_id))


@router.post("/{chapter_id}/revisions/{revision_id}/restore")
async def restore_revision(
    chapter_id: str, revision_id: str, session: AsyncSession = Depends(get_session)
) -> dict:
    """回滚到指定快照：先快照当前内容，再覆盖，`version` 继续自增。"""
    dto = await volumes.restore_revision(session, chapter_id, revision_id)
    await session.commit()
    return ok(dto)
