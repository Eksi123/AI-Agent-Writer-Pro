#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
模块: app.api.widgets
文件名: widgets.py
作者: xionggaoqing
时间: 2026-10-27
说明: 左上角动态组件数据 API（手册「六 → 路由树 · 配置 / widgets」）。

    GET /widgets/weather?latitude=&longitude=|?city=   ★动态组件天气
    GET /widgets/city?latitude=&longitude=             ★反向地理编码（失败回落 settings.city）
    GET /widgets/pet-state?novel_id=                   ★宠物状态（writing|idle）

    天气/地理编码逻辑在 `services/weather.py`（与内置天气 MCP 共用，避免两份实现漂移）。
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.deps import get_session
from app.core.response import ok
from app.db.base import parse_ts
from app.db.models import ProjectIndex, UserSettings
from app.services import weather as weather_service

router = APIRouter(tags=["widgets"])


async def _fallback_city(session: AsyncSession) -> str:
    """回落城市：通用设置的 `city`（★语义是定位失败时的兜底，不是定位结果）。"""
    row = await session.get(UserSettings, "default")
    if row is not None and row.city:
        return row.city
    return "北京"


@router.get("/widgets/weather")
async def widget_weather(
    session: AsyncSession = Depends(get_session),
    latitude: float | None = Query(default=None, ge=-90, le=90),
    longitude: float | None = Query(default=None, ge=-180, le=180),
    city: str | None = Query(default=None, max_length=60),
) -> dict:
    """组件天气：优先坐标（浏览器定位）→ 反向地理编码取城市名；否则按城市名；都无则回落设置城市。"""
    if latitude is not None and longitude is not None:
        located = await weather_service.reverse_geocode(latitude, longitude)
        label = (located or {}).get("city") or city or await _fallback_city(session)
        return ok(await weather_service.current_weather_by_coords(latitude, longitude, city_label=label))

    target = (city or "").strip() or await _fallback_city(session)
    return ok(await weather_service.current_weather(target))


@router.get("/widgets/city")
async def widget_city(
    session: AsyncSession = Depends(get_session),
    latitude: float = Query(ge=-90, le=90),
    longitude: float = Query(ge=-180, le=180),
) -> dict:
    """反向地理编码；失败回落 `settings.city`（`source` 标明来源）。"""
    located = await weather_service.reverse_geocode(latitude, longitude)
    if located is None:
        return ok({"city": await _fallback_city(session), "source": "fallback"})
    return ok({**located, "source": "reverse_geocode"})


@router.get("/widgets/pet-state")
async def widget_pet_state(
    session: AsyncSession = Depends(get_session),
    novel_id: str | None = Query(default=None),
) -> dict:
    """宠物**两态**：writing（近期有正文变更）| idle（超过 pet_idle_minutes 无进展）。

    判定依据 `project_index.last_updated`（正文写入时刷新）；不设「每日目标字数」，故无达成态。
    """
    settings_row = await session.get(UserSettings, "default")
    idle_minutes = settings_row.pet_idle_minutes if settings_row else get_settings().pet_idle_minutes

    state = "idle"
    last_updated: str | None = None
    if novel_id:
        index = await session.get(ProjectIndex, novel_id)
        if index is not None:
            last_updated = index.last_updated
            ts = parse_ts(index.last_updated)
            if ts is not None:
                age_min = (datetime.now(UTC) - ts).total_seconds() / 60.0
                state = "writing" if age_min <= idle_minutes else "idle"

    return ok(
        {
            "state": state,
            "idle_minutes_threshold": idle_minutes,
            "novel_id": novel_id,
            "last_updated": last_updated,
        }
    )
