#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
模块: app.services.weather
文件名: weather.py
作者: xionggaoqing
时间: 2026-10-27
说明: 天气服务：Open-Meteo（免费、无 Key、覆盖中国城市含 Geocoding）。

    手册「二、(3) MCP」：天气逻辑抽到本模块，供 ① 左上角动态组件 API（widgets）
    ② 内置天气 MCP ③ Agent 工具 共用，避免多份实现漂移。

    远程 API 约定（手册「二、(5)」）：统一 httpx 异步客户端 + 超时 + 简单内存缓存。
    失败一律抛 `DependencyUnavailableError`（503/50301），调用方给可读提示。
"""

from __future__ import annotations

import time
from typing import Any

import httpx

from app.core.errors import DependencyUnavailableError
from app.core.logging import get_logger

logger = get_logger(__name__)

_GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"
_FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
_TIMEOUT = 10.0
_CACHE_TTL_SEC = 600  # 天气缓存 10 分钟（组件轮询友好）

#: WMO 天气代码 → 中文描述与图标（open-meteo weathercode）
_WMO: dict[int, tuple[str, str]] = {
    0: ("晴", "☀️"),
    1: ("大部晴朗", "🌤️"),
    2: ("多云", "⛅"),
    3: ("阴", "☁️"),
    45: ("雾", "🌫️"),
    48: ("雾凇", "🌫️"),
    51: ("毛毛雨", "🌦️"),
    53: ("毛毛雨", "🌦️"),
    55: ("毛毛雨", "🌧️"),
    61: ("小雨", "🌧️"),
    63: ("中雨", "🌧️"),
    65: ("大雨", "🌧️"),
    66: ("冻雨", "🌧️"),
    67: ("冻雨", "🌧️"),
    71: ("小雪", "🌨️"),
    73: ("中雪", "🌨️"),
    75: ("大雪", "❄️"),
    77: ("雪粒", "❄️"),
    80: ("阵雨", "🌦️"),
    81: ("阵雨", "🌧️"),
    82: ("强阵雨", "⛈️"),
    85: ("阵雪", "🌨️"),
    86: ("阵雪", "❄️"),
    95: ("雷暴", "⛈️"),
    96: ("雷暴伴冰雹", "⛈️"),
    99: ("雷暴伴冰雹", "⛈️"),
}

_cache: dict[str, tuple[float, Any]] = {}


def _cache_get(key: str) -> Any | None:
    hit = _cache.get(key)
    if hit is None:
        return None
    ts, value = hit
    if time.time() - ts > _CACHE_TTL_SEC:
        _cache.pop(key, None)
        return None
    return value


def _cache_set(key: str, value: Any) -> None:
    if len(_cache) > 256:  # 防膨胀
        _cache.clear()
    _cache[key] = (time.time(), value)


def describe_weathercode(code: int | None) -> tuple[str, str]:
    """WMO 代码 → (中文描述, emoji 图标)。"""
    if code is None:
        return ("未知", "❓")
    return _WMO.get(int(code), (f"代码{code}", "❓"))


async def geocode(city: str) -> dict[str, Any]:
    """城市名 → 坐标（Open-Meteo Geocoding，支持中文）。失败抛 503。"""
    city = (city or "").strip()
    if not city:
        raise DependencyUnavailableError("城市名不能为空")

    cached = _cache_get(f"geo:{city}")
    if cached is not None:
        return cached

    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.get(
                _GEOCODE_URL, params={"name": city, "count": 1, "language": "zh", "format": "json"}
            )
            resp.raise_for_status()
            payload = resp.json()
    except httpx.HTTPError as exc:
        logger.warning("geocode_failed", city=city, error=str(exc))
        raise DependencyUnavailableError(f"地理编码失败：{city}（{type(exc).__name__}）") from exc

    results = payload.get("results") or []
    if not results:
        raise DependencyUnavailableError(f"未找到城市：{city}")
    hit = results[0]
    out = {
        "city": hit.get("name") or city,
        "country": hit.get("country") or "",
        "admin1": hit.get("admin1") or "",
        "latitude": float(hit["latitude"]),
        "longitude": float(hit["longitude"]),
        "timezone": hit.get("timezone") or "auto",
    }
    _cache_set(f"geo:{city}", out)
    return out


async def reverse_geocode(latitude: float, longitude: float) -> dict[str, Any] | None:
    """坐标 → 城市名（BigDataCloud 免费反向地理编码，中文支持）。

    失败返回 None（调用方回落 settings.city），不抛异常——定位是增强功能非关键路径。
    """
    key = f"rgeo:{round(latitude, 3)}:{round(longitude, 3)}"
    cached = _cache_get(key)
    if cached is not None:
        return cached
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.get(
                "https://api.bigdatacloud.net/data/reverse-geocode-client",
                params={"latitude": latitude, "longitude": longitude, "localityLanguage": "zh"},
            )
            resp.raise_for_status()
            payload = resp.json()
    except (httpx.HTTPError, ValueError) as exc:
        logger.info("reverse_geocode_failed", error=str(exc))
        return None

    city = payload.get("city") or payload.get("locality") or payload.get("principalSubdivision")
    if not city:
        return None
    out = {"city": str(city), "latitude": latitude, "longitude": longitude}
    _cache_set(key, out)
    return out


async def current_weather(city: str) -> dict[str, Any]:
    """按城市名取当前天气（geocode → forecast）。"""
    geo = await geocode(city)
    return await current_weather_by_coords(geo["latitude"], geo["longitude"], city_label=geo["city"])


async def current_weather_by_coords(
    latitude: float, longitude: float, *, city_label: str = ""
) -> dict[str, Any]:
    """按坐标取当前天气。"""
    key = f"cw:{round(latitude, 2)}:{round(longitude, 2)}"
    cached = _cache_get(key)
    if cached is not None:
        return cached

    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.get(
                _FORECAST_URL,
                params={
                    "latitude": latitude,
                    "longitude": longitude,
                    "current": "temperature_2m,relative_humidity_2m,apparent_temperature,"
                    "weather_code,wind_speed_10m",
                    "timezone": "auto",
                },
            )
            resp.raise_for_status()
            payload = resp.json()
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning("weather_failed", error=str(exc))
        raise DependencyUnavailableError(f"天气服务不可用：{type(exc).__name__}") from exc

    current = payload.get("current") or {}
    desc, icon = describe_weathercode(current.get("weather_code"))
    out = {
        "city": city_label,
        "latitude": latitude,
        "longitude": longitude,
        "temperature": current.get("temperature_2m"),
        "apparent_temperature": current.get("apparent_temperature"),
        "humidity": current.get("relative_humidity_2m"),
        "wind_speed": current.get("wind_speed_10m"),
        "weather_code": current.get("weather_code"),
        "description": desc,
        "icon": icon,
        "observed_at": current.get("time"),
    }
    _cache_set(key, out)
    return out


async def forecast(city: str, days: int = 3) -> dict[str, Any]:
    """按城市名取未来 N 天预报（MCP `get_forecast` 用）。"""
    days = max(1, min(int(days or 3), 7))
    geo = await geocode(city)
    key = f"fc:{geo['latitude']:.2f}:{geo['longitude']:.2f}:{days}"
    cached = _cache_get(key)
    if cached is not None:
        return cached

    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.get(
                _FORECAST_URL,
                params={
                    "latitude": geo["latitude"],
                    "longitude": geo["longitude"],
                    "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max",
                    "forecast_days": days,
                    "timezone": "auto",
                },
            )
            resp.raise_for_status()
            payload = resp.json()
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning("forecast_failed", error=str(exc))
        raise DependencyUnavailableError(f"预报服务不可用：{type(exc).__name__}") from exc

    daily = payload.get("daily") or {}
    items = []
    dates = daily.get("time") or []
    for i, date in enumerate(dates):
        code = (daily.get("weather_code") or [None])[i] if i < len(daily.get("weather_code") or []) else None
        desc, icon = describe_weathercode(code)
        items.append(
            {
                "date": date,
                "description": desc,
                "icon": icon,
                "temp_max": _nth(daily.get("temperature_2m_max"), i),
                "temp_min": _nth(daily.get("temperature_2m_min"), i),
                "precipitation_probability": _nth(daily.get("precipitation_probability_max"), i),
            }
        )
    out = {"city": geo["city"], "days": items}
    _cache_set(key, out)
    return out


def _nth(values: Any, i: int) -> Any:
    if isinstance(values, list) and i < len(values):
        return values[i]
    return None
