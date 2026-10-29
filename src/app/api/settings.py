# -*- coding: utf-8 -*-
"""
模块: app.api.settings
文件名: settings.py
作者: xionggaoqing
时间: 2026-10-27
说明: 通用设置（单行表，id 固定 'default'）。手册「三、(2) 设置范围」。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_session
from app.core.response import ok
from app.db.base import utcnow
from app.db.models import UserSettings
from app.schemas import SettingsIn
from app.services import audit

router = APIRouter(tags=["settings"])

_DEFAULTS: dict[str, Any] = {
    "theme": "light",
    "primary_color": "#409EFF",
    "font_size": 18,
    "font_family": "serif",
    "reading_width": 42,
    "autosave_interval_sec": 30,
    "city": "北京",
    "widget_enabled_json": None,
    "pet_idle_minutes": 30,
}

#: 对外暴露的字段（widget_enabled_json 暂不开放编辑，M6 再接）
_PUBLIC_FIELDS = (
    "theme",
    "primary_color",
    "font_size",
    "font_family",
    "reading_width",
    "autosave_interval_sec",
    "city",
    "pet_idle_minutes",
)


def _to_dto(row: UserSettings) -> dict[str, Any]:
    return {**{f: getattr(row, f) for f in _PUBLIC_FIELDS}, "updated_at": row.updated_at}


async def _get_or_create(session: AsyncSession) -> UserSettings:
    row = await session.get(UserSettings, "default")
    if row is None:
        row = UserSettings(id="default", **_DEFAULTS, updated_at=utcnow())
        session.add(row)
        await session.flush()
    return row


@router.get("/settings")
async def get_settings_api(session: AsyncSession = Depends(get_session)) -> dict:
    """读通用设置（不存在时按默认值惰性创建单行）。"""
    return ok(_to_dto(await _get_or_create(session)))


@router.put("/settings")
async def put_settings(body: SettingsIn, session: AsyncSession = Depends(get_session)) -> dict:
    """写通用设置（单行）。"""
    row = await _get_or_create(session)
    changes: dict[str, Any] = {}
    for field in _PUBLIC_FIELDS:
        value = getattr(body, field)
        if value is None:
            continue
        if getattr(row, field) != value:
            changes[field] = {"from": getattr(row, field), "to": value}
            setattr(row, field, value)

    if changes:
        row.updated_at = utcnow()
        await session.flush()
        await audit.record(session, "settings.update", entity_type="settings", entity_id="default", detail=changes)
        await session.commit()
    return ok(_to_dto(row))


@router.post("/settings/reset")
async def reset_settings(session: AsyncSession = Depends(get_session)) -> dict:
    """恢复默认设置（v1028：保存按钮旁的「恢复默认设置」）。"""
    row = await _get_or_create(session)
    changes: dict[str, Any] = {}
    for field in _PUBLIC_FIELDS:
        default = _DEFAULTS[field]
        if getattr(row, field) != default:
            changes[field] = {"from": getattr(row, field), "to": default}
            setattr(row, field, default)

    if changes:
        row.updated_at = utcnow()
        await session.flush()
        await audit.record(session, "settings.reset", entity_type="settings", entity_id="default", detail=changes)
        await session.commit()
    return ok(_to_dto(row))
