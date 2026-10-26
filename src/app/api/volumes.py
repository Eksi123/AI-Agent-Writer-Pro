"""卷 / 章 CRUD（手册「六 → 路由树 · 卷章」）。

★**无 reorder / move 接口**：卷章顺序是大纲派生值（铁律 10 / 手册「四」）。
前端卷章树只读渲染 `sort_order`，顺序调整一律回到大纲节点。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_session
from app.core.response import ok
from app.schemas import ChapterCreate, ChapterUpdate, VolumeCreate, VolumeUpdate
from app.services import volumes

router = APIRouter(tags=["volumes"])


# ---------------------------------------------------------------------------
# 卷
# ---------------------------------------------------------------------------


@router.get("/novels/{novel_id}/volumes")
async def list_volumes(novel_id: str, session: AsyncSession = Depends(get_session)) -> dict:
    """卷列表（含各卷章节），按 `sort_order` 只读排序。"""
    return ok(await volumes.list_volumes(session, novel_id))


@router.post("/novels/{novel_id}/volumes")
async def create_volume(
    novel_id: str, body: VolumeCreate, session: AsyncSession = Depends(get_session)
) -> dict:
    """新建卷：显式命名（违规 42201），追加到末尾。"""
    dto = await volumes.create_volume(session, novel_id, title=body.title, summary=body.summary)
    await session.commit()
    return ok(dto)


@router.patch("/volumes/{vol_id}")
async def update_volume(
    vol_id: str, body: VolumeUpdate, session: AsyncSession = Depends(get_session)
) -> dict:
    """改卷名 / 简介（不改 ID、目录名、sort_order）。"""
    dto = await volumes.update_volume(session, vol_id, title=body.title, summary=body.summary)
    await session.commit()
    return ok(dto)


@router.delete("/volumes/{vol_id}")
async def delete_volume(vol_id: str, session: AsyncSession = Depends(get_session)) -> dict:
    """删除卷：级联删除其下章节文件（前端二次确认）。"""
    dto = await volumes.delete_volume(session, vol_id)
    await session.commit()
    return ok(dto)


# ---------------------------------------------------------------------------
# 章
# ---------------------------------------------------------------------------


@router.get("/volumes/{vol_id}/chapters")
async def list_chapters(vol_id: str, session: AsyncSession = Depends(get_session)) -> dict:
    project_id, _vols, _vol = volumes.locate_volume(vol_id)
    return ok(await volumes.list_chapters(session, project_id, vol_id))


@router.post("/volumes/{vol_id}/chapters")
async def create_chapter(
    vol_id: str, body: ChapterCreate, session: AsyncSession = Depends(get_session)
) -> dict:
    """新建章：显式命名（违规 42201），追加到卷末尾。"""
    dto = await volumes.create_chapter(session, vol_id, title=body.title, content=body.content)
    await session.commit()
    return ok(dto)


@router.patch("/chapters/{chapter_id}")
async def update_chapter(
    chapter_id: str, body: ChapterUpdate, session: AsyncSession = Depends(get_session)
) -> dict:
    """改章名 / 状态（draft|finalized）。定稿触发一次修订快照。"""
    dto = await volumes.update_chapter_meta(
        session, chapter_id, title=body.title, status=body.status
    )
    await session.commit()
    return ok(dto)


@router.delete("/chapters/{chapter_id}")
async def delete_chapter(chapter_id: str, session: AsyncSession = Depends(get_session)) -> dict:
    """删除章文件。★不提供跨卷移动。"""
    dto = await volumes.delete_chapter(session, chapter_id)
    await session.commit()
    return ok(dto)
