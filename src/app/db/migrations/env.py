#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
模块: app.db.migrations.env
文件名: env.py
作者: xionggaoqing
时间: 2026-10-27
说明: Alembic 迁移环境（async engine，URL 从 app.core.config 动态注入）。
"""

from __future__ import annotations

import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from app.core.config import get_settings
from app.db.base import Base
from app.db.models import ALL_MODELS  # noqa: F401 - 注册全部表供 autogenerate

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# URL 以应用配置为准（data/app.db），不写死在 alembic.ini
_settings = get_settings()
# SQLite 不会自动创建父目录，迁移前必须先确保 data/ 存在
_settings.db_path.parent.mkdir(parents=True, exist_ok=True)
config.set_main_option("sqlalchemy.url", f"sqlite+aiosqlite:///{_settings.db_path.as_posix()}")

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """离线模式：仅生成 SQL，不连库。"""
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=True,  # SQLite ALTER 需要 batch mode
    )
    with context.begin_transaction():
        context.run_migrations()


def _do_run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        render_as_batch=True,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    engine = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with engine.connect() as connection:
        await connection.run_sync(_do_run_migrations)
    await engine.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
