# -*- coding: utf-8 -*-
"""
模块: app.db.models.config
文件名: config.py
作者: xionggaoqing
时间: 2026-10-27
说明: 配置域 ORM：user_settings / audit_logs（手册「五、数据模型」M1）。

    ★`user_settings` 是单行表（id 固定 'default'）；
    ★审计日志只读查询，不做后台管理 UI，同时 JSONL 备份。
"""

from __future__ import annotations

from sqlalchemy import Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, new_id, utcnow


class AuditLog(Base):
    """审计日志：数据表 + 只读查询接口，不做后台管理 UI；同时 JSONL 备份。"""

    __tablename__ = "audit_logs"
    __table_args__ = (Index("ix_audit_logs_entity", "entity_type", "entity_id"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    action: Mapped[str] = mapped_column(String(60), nullable=False)
    entity_type: Mapped[str | None] = mapped_column(String(30), nullable=True)
    entity_id: Mapped[str | None] = mapped_column(String(60), nullable=True)
    #: JSON 文本（detail_json）
    detail_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[str] = mapped_column(String(32), nullable=False, default=utcnow)


class UserSettings(Base):
    """通用设置：单行表（id 固定 'default'）。"""

    __tablename__ = "user_settings"

    id: Mapped[str] = mapped_column(String(20), primary_key=True, default="default")
    #: v1028：light | dark | eye（护眼）；system 仅为兼容旧数据保留读取
    theme: Mapped[str] = mapped_column(String(16), nullable=False, default="light")
    primary_color: Mapped[str] = mapped_column(String(20), nullable=False, default="#409EFF")
    font_size: Mapped[int] = mapped_column(Integer, nullable=False, default=18)
    font_family: Mapped[str] = mapped_column(String(50), nullable=False, default="serif")
    reading_width: Mapped[int] = mapped_column(Integer, nullable=False, default=42)
    autosave_interval_sec: Mapped[int] = mapped_column(Integer, nullable=False, default=30)
    #: ★回落城市（浏览器定位失败/被拒时使用），不是定位结果
    city: Mapped[str] = mapped_column(String(60), nullable=False, default="北京")
    widget_enabled_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    pet_idle_minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=30)
    updated_at: Mapped[str] = mapped_column(String(32), nullable=False, default=utcnow)
