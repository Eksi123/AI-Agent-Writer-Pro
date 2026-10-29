# -*- coding: utf-8 -*-
"""
模块: app.db.bootstrap
文件名: bootstrap.py
作者: xionggaoqing
时间: 2026-10-27
说明: 数据库初始化：建表 + 打 alembic 版本戳 + FTS5 探测建表。

    为什么建表后要 stamp：`create_all` 建的库若没有 `alembic_version` 行，后续
    `writer migrate`（alembic upgrade head）会因「表已存在」而失败。因此这里在
    `create_all` 之后把版本戳打到 head，使两条初始化路径（应用启动 / 手工 migrate）
    互相幂等——先 `writer dev` 再 `writer migrate` 不会报错，反之亦然。
"""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from app.core.logging import get_logger
from app.db.base import Base
from app.db.fts import CREATE_FTS_SQL, fts_available, probe_fts5
from app.db.models import ALL_MODELS  # noqa: F401 - 导入以注册全部表
from app.db.session import get_engine

logger = get_logger(__name__)

MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"


def _head_revision() -> str | None:
    """读取迁移脚本的 head revision；无迁移文件时返回 None。"""
    if not (MIGRATIONS_DIR / "versions").is_dir():
        return None
    try:
        from alembic.config import Config
        from alembic.script import ScriptDirectory

        cfg = Config()
        cfg.set_main_option("script_location", str(MIGRATIONS_DIR))
        return ScriptDirectory.from_config(cfg).get_current_head()
    except Exception as exc:  # pragma: no cover - alembic 缺失/脚本损坏时降级
        logger.warning("alembic_head_lookup_failed", error=str(exc))
        return None


async def _stamp_head(conn: AsyncConnection) -> None:
    """把 `alembic_version` 置为 head（表不存在则建），使 create_all 库可被 migrate 识别。"""
    head = _head_revision()
    if head is None:
        return
    await conn.execute(
        text("CREATE TABLE IF NOT EXISTS alembic_version ("
             "version_num VARCHAR(32) NOT NULL, "
             "CONSTRAINT alembic_version_pkc PRIMARY KEY (version_num))")
    )
    existing = (await conn.execute(text("SELECT version_num FROM alembic_version"))).first()
    if existing is None:
        await conn.execute(
            text("INSERT INTO alembic_version (version_num) VALUES (:rev)"), {"rev": head}
        )
        logger.info("alembic_stamped", revision=head)


async def init_database() -> None:
    """幂等初始化：业务表 + alembic 迁移 + `story_fts`（FTS5 可用时）。

    两条路径互相幂等：
    - **全新库**：`create_all` 建表 → 无 `alembic_version` 行 → 打版本戳到 head；
    - **既有库落后于 head**：`create_all` 只补新表、**不会给已存在的表加列**，
      因此这里再跑一次 `alembic upgrade head` 应用增量（如 v1028 给 llm_providers
      加 timeout_sec / temperature / max_tokens），避免「模型加了列但旧库没列」导致 500。
    """
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await _init_fts(conn)

    # 迁移在 begin() 之外执行：alembic 用自己的连接，避免与启动连接争 SQLite 写锁
    await _upgrade_if_behind()

    async with engine.begin() as conn:
        await _stamp_head(conn)
    logger.info("database_initialized", fts5=fts_available())


async def _current_revision() -> str | None:
    """读 `alembic_version` 当前版本号；表不存在返回 None。"""
    engine = get_engine()
    async with engine.connect() as conn:
        row = (await conn.execute(text("SELECT name FROM sqlite_master WHERE type='table' AND name='alembic_version'"))).first()
        if row is None:
            return None
        current = (await conn.execute(text("SELECT version_num FROM alembic_version"))).first()
    return str(current[0]) if current else None


async def _upgrade_if_behind() -> None:
    """既有库落后于迁移 head 时执行 `alembic upgrade head`（失败不阻断启动）。"""
    head = _head_revision()
    if head is None:
        return
    try:
        current = await _current_revision()
    except Exception as exc:  # pragma: no cover - 版本探测失败不影响建表
        logger.info("alembic_current_lookup_failed", error=str(exc))
        return
    if current is None or current == head:
        return

    import asyncio

    from alembic import command
    from alembic.config import Config

    def _run_upgrade() -> None:
        cfg = Config()
        cfg.set_main_option("script_location", str(MIGRATIONS_DIR))
        command.upgrade(cfg, "head")

    try:
        # alembic 的 env.py 内部用 asyncio.run()，必须在**无事件循环的线程**里跑
        await asyncio.to_thread(_run_upgrade)
        logger.info("alembic_upgraded", revision_from=current, revision_to=head)
    except Exception as exc:  # pragma: no cover - 迁移失败降级为告警，交由 `writer migrate`
        logger.warning("alembic_upgrade_failed_run_writer_migrate", error=str(exc))


async def _init_fts(conn: AsyncConnection) -> None:
    """两步探测后建 `story_fts`；不可用则跳过（检索走 LIKE 降级）。"""
    status = probe_fts5()
    if not status.available:
        logger.warning("fts5_disabled_like_fallback", reason=status.reason)
        return
    await conn.execute(text(CREATE_FTS_SQL))


async def drop_database() -> None:
    """仅测试用：删除全部表（含 alembic 版本戳与 FTS 虚拟表）。"""
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.execute(text("DROP TABLE IF EXISTS story_fts"))
        await conn.execute(text("DROP TABLE IF EXISTS story_fts_meta"))
        await conn.execute(text("DROP TABLE IF EXISTS alembic_version"))
