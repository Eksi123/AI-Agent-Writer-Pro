# -*- coding: utf-8 -*-
"""
模块: app.cli
文件名: cli.py
作者: xionggaoqing
时间: 2026-11-10
说明: 命令行入口：`writer dev | migrate | rebuild-index | doctor`（手册「三、(1)」）。

        uv run writer dev             # 开发模式：后端（reload）+ 前端（Vite HMR）双进程
        uv run writer dev --backend-only  # 只启动后端
        uv run writer migrate         # = alembic upgrade head
        uv run writer rebuild-index   # 全量重建 story_fts / project_index
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from typing import Any

from alembic import command
from alembic.config import Config
from sqlalchemy import select

from app.core.config import PROJECT_ROOT, get_settings
from app.core.paths import data_root
from app.db.bootstrap import init_database
from app.db.fts import bigram_tokenize, probe_fts5
from app.db.models import Novel
from app.db.session import get_sessionmaker, reset_engine
from app.services import novels as novels_service
from app.services import search as search_service
from app.services.exporters import pdf_available


def _cmd_dev(args: argparse.Namespace) -> int:
    """开发模式：后端（reload）+ 前端（Vite HMR）双进程。

    默认同时启动前后端；`--backend-only` 只启动后端。
    """
    settings = get_settings()
    host = args.host or settings.app_host
    port = args.port or settings.app_port

    if args.backend_only:
        # 只启动后端
        import uvicorn
        uvicorn.run(
            "app.main:app",
            host=host,
            port=port,
            reload=True,
            log_level=settings.log_level.lower(),
        )
        return 0

    # 启动前后端双进程
    import importlib.util
    spec = importlib.util.spec_from_file_location("_main_entry", PROJECT_ROOT / "main.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.run_dev(port, open_browser=not args.no_browser)


def _cmd_migrate(_args: argparse.Namespace) -> int:
    """封装 `alembic upgrade head`。"""


    ini = PROJECT_ROOT / "alembic.ini"
    if not ini.exists():
        print(f"[migrate] 未找到 {ini}，回落到 create_all 建表", file=sys.stderr)
        from app.db.bootstrap import init_database

        asyncio.run(init_database())
        return 0

    cfg = Config(str(ini))
    cfg.set_main_option("script_location", str(PROJECT_ROOT / "src" / "app" / "db" / "migrations"))
    command.upgrade(cfg, "head")
    print("[migrate] alembic upgrade head 完成")
    return 0


def _cmd_rebuild_index(_args: argparse.Namespace) -> int:
    """全量重建派生索引（`story_fts` + `project_index`），清 dirty。"""


    async def _run() -> dict[str, Any]:
        await init_database()
        summary: dict[str, Any] = {"projects": 0, "indexed": 0, "fts5": True}
        async with get_sessionmaker()() as session:
            rows = (
                await session.execute(select(Novel.id, Novel.title).where(Novel.status == "active"))
            ).all()
            for project_id, title in rows:
                res = await search_service.reindex_project(session, project_id)
                await novels_service.refresh_project_index(session, project_id)
                summary["projects"] += 1
                summary["indexed"] += int(res.get("indexed") or 0)
                summary["fts5"] = bool(res.get("fts5"))
                print(f"  · {title} ({project_id[:8]}) indexed={res.get('indexed')}")
            await session.commit()
        await reset_engine()
        return summary

    result = asyncio.run(_run())
    print(f"[rebuild-index] 完成：{result}")
    return 0


def _cmd_doctor(_args: argparse.Namespace) -> int:
    """环境自检：FTS5 / PDF 依赖 / 数据目录 / 关键依赖版本。"""

    settings = get_settings()
    fts = probe_fts5()
    sample = bigram_tokenize("少年走出山村")
    pdf_ok = pdf_available()

    print("AI Writer Pro · doctor")
    print(f"  python          : {sys.version.split()[0]}")
    print(f"  data_root       : {data_root()}")
    print(f"  app.db          : {settings.db_path} ({'存在' if settings.db_path.exists() else '未创建'})")
    print(f"  sqlite          : {fts.sqlite_version}")
    print(f"  fts5 available  : {fts.available}  ({fts.reason})")
    print(f"  bigram sample   : 少年走出山村 -> {sample}")
    print(f"  pdf (reportlab) : {'可用' if pdf_ok else '未安装（uv pip install reportlab）'}")
    print(f"  langfuse        : {'已配置' if settings.langfuse_enabled else '未配置（追踪降级为本地日志）'}")

    try:
        import fastapi
        import sqlalchemy
        from deepagents import __version__ as da_version

        print(f"  fastapi         : {fastapi.__version__}")
        print(f"  sqlalchemy      : {sqlalchemy.__version__}")
        print(f"  deepagents      : {da_version}")
    except Exception as exc:  # pragma: no cover
        print(f"  [warn] 依赖导入失败: {exc}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="writer", description="AI Writer Pro 命令行工具"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_dev = sub.add_parser("dev", help="启动开发服务器（后端 + 前端双进程）")
    p_dev.add_argument("--host", default=None, help="后端监听地址（默认 127.0.0.1）")
    p_dev.add_argument("--port", type=int, default=None, help="后端端口（默认 8000）")
    p_dev.add_argument("--backend-only", action="store_true", help="只启动后端（不启动前端 Vite）")
    p_dev.add_argument("--no-browser", action="store_true", help="启动后不自动打开浏览器")
    p_dev.set_defaults(func=_cmd_dev)

    p_mig = sub.add_parser("migrate", help="执行 alembic upgrade head")
    p_mig.set_defaults(func=_cmd_migrate)

    p_re = sub.add_parser("rebuild-index", help="全量重建 story_fts / project_index")
    p_re.set_defaults(func=_cmd_rebuild_index)

    p_doc = sub.add_parser("doctor", help="环境自检（FTS5 / PDF 依赖 / 依赖版本）")
    p_doc.set_defaults(func=_cmd_doctor)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
