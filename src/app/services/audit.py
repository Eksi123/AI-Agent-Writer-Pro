#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
模块: app.services.audit
文件名: audit.py
作者: xionggaoqing
时间: 2026-10-27
说明: 审计日志：SQLite `audit_logs` + JSONL 备份（手册「三、(2)」）。
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from app.core.logging import get_logger
from app.core.paths import logs_root
from app.db.base import new_id, utcnow
from app.db.models import AuditLog

logger = get_logger(__name__)

_audit_logger = logger.bind(channel="audit")


async def record(
    session: Any,
    action: str,
    *,
    entity_type: str | None = None,
    entity_id: str | None = None,
    detail: dict[str, Any] | None = None,
    commit: bool = False,
) -> None:
    """写一条审计：入表 + JSONL 备份。失败不阻断业务（记 error 日志）。"""
    detail_json = json.dumps(detail, ensure_ascii=False) if detail else None
    try:
        session.add(
            AuditLog(
                id=new_id(),
                action=action,
                entity_type=entity_type,
                entity_id=entity_id,
                detail_json=detail_json,
                created_at=utcnow(),
            )
        )
        if commit:
            await session.commit()
    except Exception as exc:  # pragma: no cover - 审计失败不阻断
        logger.error("audit_db_failed", action=action, error=str(exc))

    try:
        entry = {
            "timestamp": datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
            "action": action,
            "entity_type": entity_type,
            "entity_id": entity_id,
            "detail": detail or {},
        }
        path = logs_root() / f"audit-{datetime.now(UTC).strftime('%Y-%m-%d')}.jsonl"
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError as exc:  # pragma: no cover
        logger.error("audit_jsonl_failed", action=action, error=str(exc))

    _audit_logger.info(
        "audit",
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        **(detail or {}),
    )
