# -*- coding: utf-8 -*-
"""
模块: app.core.logging
文件名: logging.py
作者: xionggaoqing
时间: 2026-10-27
说明: 结构化日志：structlog 输出 JSONL 到 `data/logs/`，含敏感字段脱敏（手册「三、(1)」、铁律 11）。
"""

from __future__ import annotations

import logging
import logging.handlers
import re
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import structlog

from app.core.paths import logs_root

#: 键名匹配这些模式的字段一律脱敏（只留掩码，不记明文）。
#: 注意**不能**裸匹配 `token`：用量字段 `tokens_in` / `run_tokens_in` 会被误伤清空，
#: 只匹配凭据类键名（api_key / access_token / auth token / secret / password / authorization）。
_SENSITIVE_KEY = re.compile(
    r"(api[_-]?key|access[_-]?token|auth[_-]?token|bearer|secret|password|authorization)",
    re.IGNORECASE,
)
_CONFIGURED = False


def mask_secret(value: Any) -> str:
    """`sk-abcdefgh1234` → `sk-****1234`；过短则全掩码。"""
    if not isinstance(value, str) or not value:
        return ""
    if len(value) <= 8:
        return "****"
    return f"{value[:3]}****{value[-4:]}"


def _mask_sensitive(
    _logger: Any, _method: str, event_dict: dict[str, Any]
) -> dict[str, Any]:
    """structlog processor：对键名敏感的字段做掩码。"""
    for key in list(event_dict.keys()):
        if _SENSITIVE_KEY.search(key):
            event_dict[key] = mask_secret(event_dict.get(key))
    return event_dict


def _utc_timestamp(_logger: Any, _method: str, event_dict: dict[str, Any]) -> dict[str, Any]:
    """时间戳统一 UTC ISO-8601（铁律 11）。"""
    event_dict["timestamp"] = datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    return event_dict


def _strip_formatter_meta(
    _logger: Any, _method: str, event_dict: dict[str, Any]
) -> dict[str, Any]:
    """剔除 ProcessorFormatter 桥接产生的元字段（_record/_from_structlog/posargs）。"""
    for key in ("_record", "_from_structlog", "posargs"):
        event_dict.pop(key, None)
    return event_dict


def _log_file(name: str) -> Path:
    """按日期轮转的文件名，如 `app-2026-10-26.jsonl`。"""
    return logs_root() / f"{name}-{datetime.now(UTC).strftime('%Y-%m-%d')}.jsonl"


def _jsonl_handler(filename: str, level: int) -> logging.Handler:
    handler = logging.handlers.RotatingFileHandler(
        _log_file(filename), maxBytes=20 * 1024 * 1024, backupCount=7, encoding="utf-8"
    )
    # structlog 事件与第三方 stdlib 日志统一渲染成 JSON 行（foreign_pre_chain
    # 给第三方记录补 level/timestamp/脱敏，保证两种来源字段同构）
    handler.setFormatter(
        structlog.stdlib.ProcessorFormatter(
            processors=[
                _strip_formatter_meta,
                structlog.processors.JSONRenderer(ensure_ascii=False),
            ],
            foreign_pre_chain=_SHARED_PROCESSORS,
        )
    )
    handler.setLevel(level)
    return handler


#: structlog 与 stdlib 共用的处理器链（模块级，供 formatter 的 foreign_pre_chain 复用）
_SHARED_PROCESSORS: list[Any] = []


def configure_logging(level: str = "INFO") -> None:
    """幂等初始化：控制台可读输出 + `app-*.jsonl` / `error-*.jsonl`。

    structlog 事件经 `ProcessorFormatter` 桥接进 stdlib handler——结构化事件
    （agent_built / wasteful_loop_detected / 预算中断等）与第三方日志同盘可查。
    审计日志走独立通道（`app.core.audit` / services），同时入 SQLite。
    """
    global _CONFIGURED
    if _CONFIGURED:
        return

    log_level = getattr(logging, level.upper(), logging.INFO)

    _SHARED_PROCESSORS.clear()
    _SHARED_PROCESSORS.extend(
        [
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            _utc_timestamp,
            _mask_sensitive,
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
        ]
    )

    structlog.configure(
        processors=[
            *_SHARED_PROCESSORS,
            structlog.stdlib.PositionalArgumentsFormatter(),
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(log_level),
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )

    # stdlib logging → 控制台可读 + JSONL 文件（uvicorn / sqlalchemy / httpx 等第三方也归档）
    root = logging.getLogger()
    root.setLevel(log_level)
    root.handlers.clear()

    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(
        structlog.stdlib.ProcessorFormatter(
            processors=[
                _strip_formatter_meta,
                structlog.dev.ConsoleRenderer(colors=False),
            ],
            foreign_pre_chain=_SHARED_PROCESSORS,
        )
    )
    root.addHandler(console)

    root.addHandler(_jsonl_handler("app", log_level))
    root.addHandler(_jsonl_handler("error", logging.ERROR))

    for noisy in ("uvicorn.access", "httpx", "httpcore", "aiosqlite"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    _CONFIGURED = True


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    """取一个结构化 logger。"""
    return structlog.get_logger(name) if name else structlog.get_logger()
