# -*- coding: utf-8 -*-
"""
模块: app.db.base
文件名: base.py
作者: xionggaoqing
时间: 2026-10-27
说明: SQLAlchemy 2.0 async 基础设施（手册「五、数据模型 → 全局约定」）。

    约定：
    - 所有 `id` 为 TEXT(uuid4)，应用层生成，不用自增整数；
    - 时间戳一律 UTC ISO-8601 文本，应用层写入，**不使用 SQLite 触发器 / CURRENT_TIMESTAMP**；
    - JSON 列命名统一 `*_json`，存 TEXT；
    - 枚举以 TEXT + 应用层校验实现；
    - 连接建立时 `PRAGMA foreign_keys=ON`。
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import MetaData, String, event
from sqlalchemy.ext.asyncio import AsyncEngine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

#: 命名约定（Alembic autogenerate 稳定生成约束名）
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


def new_id() -> str:
    """应用层生成主键（uuid4 字符串）。"""
    return str(uuid.uuid4())


def short_id(existing: set[str] | None = None) -> str:
    """卷 / 章短 ID：uuid 前 8 位；冲突时重取。一旦生成**永不改变**（铁律 10）。"""
    taken = existing or set()
    while True:
        candidate = uuid.uuid4().hex[:8]
        if candidate not in taken:
            return candidate


def utcnow() -> str:
    """UTC ISO-8601 文本（`YYYY-MM-DDTHH:MM:SS.sssZ`）。"""
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def parse_ts(value: str | None) -> datetime | None:
    """把库内时间戳解析回 datetime（容错：解析失败返回 None 而不抛）。"""
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


class TimestampMixin:
    """公共列：created_at / updated_at（应用层写入）。"""

    created_at: Mapped[str] = mapped_column(String(32), default=utcnow, nullable=False)
    updated_at: Mapped[str] = mapped_column(
        String(32), default=utcnow, onupdate=utcnow, nullable=False
    )


def enable_sqlite_pragmas(engine: AsyncEngine) -> None:
    """每个新连接开启外键 + WAL（外键是「全局约定」的硬性要求）。"""

    @event.listens_for(engine.sync_engine, "connect")
    def _on_connect(dbapi_connection: Any, _record: Any) -> None:  # pragma: no cover - 事件回调
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA synchronous=NORMAL")
        finally:
            cursor.close()
