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
    
    注意：ID 生成和时间处理工具已迁移到 app.utils，此处保留向后兼容性导出。
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import MetaData, String, event
from sqlalchemy.ext.asyncio import AsyncEngine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# 从 utils 导入工具函数（保持向后兼容）
from app.utils.id import new_id, short_id
from app.utils.time import utcnow, parse_ts

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
