"""API 路由（统一前缀 `/api/v1`，路由树见手册「六」）。

★卷章**无 reorder / move 接口**：顺序是大纲派生值（铁律 10）。
"""

from __future__ import annotations

from fastapi import APIRouter

from app.api import chapters, health, novels, outline, settings, story, volumes

api_router = APIRouter(prefix="/api/v1")

api_router.include_router(health.router)
api_router.include_router(novels.router)
api_router.include_router(volumes.router)
api_router.include_router(chapters.router)
api_router.include_router(outline.router)
api_router.include_router(story.router)
api_router.include_router(settings.router)
