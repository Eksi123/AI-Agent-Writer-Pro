"""命令行入口：`writer dev | migrate | rebuild-index | doctor`（手册「三、(1)」）。

    uv run writer dev             # = uvicorn app.main:app --reload
    uv run writer migrate         # = alembic upgrade head
    uv run writer rebuild-index   # 全量重建 story_fts / project_index
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from typing import Any


def _cmd_dev(args: argparse.Namespace) -> int:
    """起 uvicorn（默认 127.0.0.1:8000）。"""
    import uvicorn

    from app.core.config import get_settings

    settings = get_settings()
    host = args.host or settings.app_host
    port = args.port or settings.app_port
    uvicorn.run(
        "app.main:app",
        host=host,
        port=port,
        reload=not args.no_reload,
        log_level=settings.log_level.lower(),
    )
    return 0


def _cmd_migrate(_args: argparse.Namespace) -> int:
    """封装 `alembic upgrade head`。"""
    from alembic import command
    from alembic.config import Config

    from app.core.config import PROJECT_ROOT

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
    from sqlalchemy import select

    from app.db.bootstrap import init_database
    from app.db.models import Novel
    from app.db.session import get_sessionmaker, reset_engine
    from app.services import novels as novels_service
    from app.services import search as search_service

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
    """环境自检：FTS5 / calibre / 数据目录 / 关键依赖版本。"""
    from app.core.config import get_settings
    from app.core.paths import data_root
    from app.db.fts import bigram_tokenize, probe_fts5
    from app.services.exporters import find_calibre

    settings = get_settings()
    fts = probe_fts5()
    sample = bigram_tokenize("少年走出山村")
    calibre = find_calibre()

    print("AI Writer Pro · doctor")
    print(f"  python          : {sys.version.split()[0]}")
    print(f"  data_root       : {data_root()}")
    print(f"  app.db          : {settings.db_path} ({'存在' if settings.db_path.exists() else '未创建'})")
    print(f"  sqlite          : {fts.sqlite_version}")
    print(f"  fts5 available  : {fts.available}  ({fts.reason})")
    print(f"  bigram sample   : 少年走出山村 -> {sample}")
    print(f"  calibre         : {calibre or '未安装（mobi 导出置灰）'}")
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

    p_dev = sub.add_parser("dev", help="启动开发服务器（uvicorn --reload）")
    p_dev.add_argument("--host", default=None)
    p_dev.add_argument("--port", type=int, default=None)
    p_dev.add_argument("--no-reload", action="store_true", help="禁用热重载")
    p_dev.set_defaults(func=_cmd_dev)

    p_mig = sub.add_parser("migrate", help="执行 alembic upgrade head")
    p_mig.set_defaults(func=_cmd_migrate)

    p_re = sub.add_parser("rebuild-index", help="全量重建 story_fts / project_index")
    p_re.set_defaults(func=_cmd_rebuild_index)

    p_doc = sub.add_parser("doctor", help="环境自检（FTS5 / calibre / 依赖版本）")
    p_doc.set_defaults(func=_cmd_doctor)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
