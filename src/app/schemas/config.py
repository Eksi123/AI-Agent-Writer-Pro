# -*- coding: utf-8 -*-
"""
模块: app.schemas.config
文件名: config.py
作者: xionggaoqing
时间: 2026-10-27
说明: 设置 / 导出 / 检索 / 健康检查 DTO（手册「六 → 路由树 · 设置/导出/检索/健康」）。

    响应统一由 `core.response.ok()` 包成 `{code,message,data}`，此处只定义 `data` 部分。
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

# ---------------------------------------------------------------------------
# 设置 / 导出 / 检索
# ---------------------------------------------------------------------------


class SettingsIn(BaseModel):
    theme: Literal["light", "dark", "system"] | None = None
    primary_color: str | None = None
    font_size: int | None = Field(default=None, ge=10, le=48)
    font_family: str | None = None
    reading_width: int | None = Field(default=None, ge=20, le=80)
    autosave_interval_sec: int | None = Field(default=None, ge=5, le=600)
    city: str | None = Field(default=None, max_length=60)
    pet_idle_minutes: int | None = Field(default=None, ge=1, le=1440)


class SettingsOut(BaseModel):
    model_config = ConfigDict(extra="allow")

    theme: str = "system"
    primary_color: str = "#409EFF"
    font_size: int = 18
    font_family: str = "serif"
    reading_width: int = 42
    autosave_interval_sec: int = 30
    city: str = "北京"
    pet_idle_minutes: int = 30


class ExportTaskOut(BaseModel):
    task_id: str
    novel_id: str
    format: str
    status: Literal["pending", "running", "done", "failed"] = "pending"
    progress: int = 0
    download_url: str | None = None
    filename: str | None = None
    error: str | None = None
    created_at: str | None = None


class SearchResultItem(BaseModel):
    doc_ref: str
    kind: str
    title: str
    snippet: str = ""
    score: float = 0.0


class SearchOut(BaseModel):
    query: str
    scope: str = "all"
    total: int = 0
    degraded: bool = False
    items: list[SearchResultItem] = Field(default_factory=list)


class HealthOut(BaseModel):
    status: str = "ok"
    version: str
    fts5_available: bool
    fts5_reason: str = ""
    sqlite_version: str = ""
    calibre_available: bool = False
    data_root: str
