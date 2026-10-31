# -*- coding: utf-8 -*-
"""
模块: app.main
文件名: main.py
作者: xionggaoqing
时间: 2026-10-27
说明: 应用装配：lifespan、路由挂载、CORS、统一异常处理、静态资源（手册「三、(1)」）。
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from app import __version__
from app.api import api_router
from app.core.config import PROJECT_ROOT, get_settings
from app.core.errors import CODE_TO_HTTP, CODE_TO_MESSAGE, ApiError, ErrorCode
from app.core.logging import configure_logging, get_logger
from app.core.paths import data_root
from app.db.bootstrap import init_database
from app.db.fts import probe_fts5
from app.db.session import reset_engine

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    configure_logging(settings.log_level)
    data_root()

    fts = probe_fts5()
    await init_database()
    await _seed_agent_config()

    logger.info(
        "startup",
        version=__version__,
        data_root=str(settings.data_root),
        fts5_available=fts.available,
        fts5_reason=fts.reason,
        sqlite_version=fts.sqlite_version,
        langfuse=settings.langfuse_enabled,
    )
    app.state.fts_status = fts

    try:
        yield
    finally:
        from app.agent.loop.checkpointer import reset_checkpointer

        await reset_checkpointer()
        await reset_engine()
        logger.info("shutdown")


async def _seed_agent_config() -> None:
    """播种内置子代理 + 按 DB 状态重新物化用户技能（幂等，失败不阻断启动）。"""
    try:
        from app.db.session import session_scope
        from app.services import skills as skills_service
        from app.services import subagents as subagents_service

        async with session_scope() as session:
            await subagents_service.seed_builtins(session)
            await skills_service.resync_materialization(session)
    except Exception as exc:  # pragma: no cover - 播种失败不影响写作业务
        logger.warning("agent_config_seed_failed", error=str(exc))


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="AI Writer Pro",
        version=__version__,
        description="大纲驱动的 AI 小说写作工作台（FastAPI + Vue 3）",
        lifespan=lifespan,
        docs_url="/api/docs",
        redoc_url="/api/redoc",
        openapi_url="/api/openapi.json",
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["Last-Event-ID"],
    )

    _register_exception_handlers(app)
    app.include_router(api_router)
    _mount_frontend(app)
    return app


def _register_exception_handlers(app: FastAPI) -> None:
    """统一响应包装 `{code, message, data}`，HTTP 状态码与 code 对齐。"""
    @app.exception_handler(ApiError)
    async def _api_error(_request: Request, exc: ApiError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.http_status,
            content={"code": exc.code, "message": exc.message, "data": exc.data},
        )

    @app.exception_handler(RequestValidationError)
    async def _validation_error(_request: Request, exc: RequestValidationError) -> JSONResponse:
        # 命名类校验失败已在服务层转 42201；这里兜住 pydantic 层面的参数错误
        details = exc.errors()
        message = "参数格式错误"
        if details:
            first = details[0]
            loc = ".".join(str(x) for x in first.get("loc", []) if x != "body")
            message = f"参数格式错误：{loc or 'body'} {first.get('msg', '')}".strip()
        return JSONResponse(
            status_code=400,
            content={
                "code": ErrorCode.BAD_REQUEST,
                "message": message,
                "data": {"errors": _jsonable(details)},
            },
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(_request: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = {404: ErrorCode.NOT_FOUND, 403: ErrorCode.FORBIDDEN, 409: ErrorCode.RUN_STATE_CONFLICT}.get(
            exc.status_code, ErrorCode.BAD_REQUEST if exc.status_code < 500 else ErrorCode.INTERNAL_ERROR
        )
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "code": code,
                "message": str(exc.detail) if exc.detail else CODE_TO_MESSAGE.get(code, "error"),
                "data": None,
            },
        )

    @app.exception_handler(FileNotFoundError)
    async def _file_not_found(_request: Request, exc: FileNotFoundError) -> JSONResponse:
        return JSONResponse(
            status_code=CODE_TO_HTTP[ErrorCode.NOT_FOUND],
            content={
                "code": ErrorCode.NOT_FOUND,
                "message": f"资源不存在：{exc}",
                "data": None,
            },
        )

    @app.exception_handler(PermissionError)
    async def _permission_error(_request: Request, exc: PermissionError) -> JSONResponse:
        return JSONResponse(
            status_code=CODE_TO_HTTP[ErrorCode.FORBIDDEN],
            content={
                "code": ErrorCode.FORBIDDEN,
                "message": f"越权访问被拒绝：{exc}",
                "data": None,
            },
        )

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        logging.getLogger("app.error").exception("unhandled_error", exc_info=exc)
        logger.error("unhandled_error", path=request.url.path, error=f"{type(exc).__name__}: {exc}")
        return JSONResponse(
            status_code=500,
            content={
                "code": ErrorCode.INTERNAL_ERROR,
                "message": CODE_TO_MESSAGE[ErrorCode.INTERNAL_ERROR],
                "data": {"type": type(exc).__name__},
            },
        )


def _jsonable(value: Any) -> Any:
    """把 pydantic 错误详情转成可 JSON 序列化结构。"""
    try:
        import json

        return json.loads(json.dumps(value, default=str, ensure_ascii=False))
    except (TypeError, ValueError):  # pragma: no cover
        return str(value)


def _mount_frontend(app: FastAPI) -> None:
    """挂载前端产物（若存在）。

    - M0 迁移前：`src/web` 是无构建骨架，用 `python -m http.server` 直开；
    - M0 迁移后：`src/web/dist` 存在则单体部署（API 与静态资源同进程）。
    """

    dist = PROJECT_ROOT / "src" / "web" / "dist"
    if dist.is_dir():
        app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")

        @app.get("/{full_path:path}", include_in_schema=False)
        async def spa_fallback(full_path: str) -> Any:
            """SPA history 回落：非 API 路径一律返回 index.html。"""
            candidate = dist / full_path
            if full_path and candidate.is_file():
                return FileResponse(candidate)
            return FileResponse(dist / "index.html")

        logger.info("frontend_mounted", path=str(dist))
    else:
        logger.info("frontend_dist_missing_run_npm_build", expected=str(dist))


app = create_app()
