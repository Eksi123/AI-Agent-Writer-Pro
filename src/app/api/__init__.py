# -*- coding: utf-8 -*-
"""
模块: app.api
文件名: __init__.py
作者: xionggaoqing
时间: 2026-10-27
说明: API 路由（统一前缀 `/api/v1`，路由树见手册「六」）。

    ★卷章**无 reorder / move 接口**：顺序是大纲派生值（铁律 10）。
"""

from __future__ import annotations

from fastapi import APIRouter

from app.api import (
    agent,
    audit,
    chapters,
    health,
    llm,
    mcp,
    novels,
    outline,
    recycle,
    settings,
    skills,
    story,
    subagents,
    volumes,
    widgets,
)

api_router = APIRouter(prefix="/api/v1")

api_router.include_router(health.router)
api_router.include_router(novels.router)
api_router.include_router(volumes.router)
api_router.include_router(chapters.router)
api_router.include_router(outline.router)
api_router.include_router(story.router)
api_router.include_router(settings.router)
api_router.include_router(audit.router)
api_router.include_router(llm.router)
api_router.include_router(agent.router)
api_router.include_router(subagents.router)
api_router.include_router(skills.router)
api_router.include_router(mcp.router)
api_router.include_router(widgets.router)
#: v1031：回收站（被删章节的归档 / 回收 / 彻底删除）
api_router.include_router(recycle.router)
