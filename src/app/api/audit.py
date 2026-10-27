# -*- coding: utf-8 -*-
"""
模块: app.api.audit
文件名: audit.py
作者: xionggaoqing
时间: 2026-10-27
说明: 审计日志只读查询（手册「六 → 路由树 · 配置」`GET /audit-logs`）。

    过滤：action / entity_type / from / to / page；不做后台管理 UI，只读。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_session
from app.core.response import ok
from app.db.models import AuditLog

router = APIRouter(tags=["audit"])

_PAGE_SIZE = 50


@router.get("/audit-logs")
async def list_audit_logs(
    session: AsyncSession = Depends(get_session),
    action: str | None = Query(default=None, max_length=60),
    entity_type: str | None = Query(default=None, max_length=30),
    date_from: str | None = Query(default=None, alias="from", description="起始时间（UTC ISO-8601 前缀比较）"),
    date_to: str | None = Query(default=None, alias="to", description="截止时间（UTC ISO-8601 前缀比较）"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=_PAGE_SIZE, ge=1, le=200),
) -> dict:
    """分页查询审计日志（时间倒序）。"""
    stmt = select(AuditLog)
    count_stmt = select(func.count(AuditLog.id))
    conditions: list[Any] = []
    if action:
        conditions.append(AuditLog.action == action)
    if entity_type:
        conditions.append(AuditLog.entity_type == entity_type)
    if date_from:
        conditions.append(AuditLog.created_at >= date_from)
    if date_to:
        conditions.append(AuditLog.created_at <= date_to)
    for cond in conditions:
        stmt = stmt.where(cond)
        count_stmt = count_stmt.where(cond)

    total = int((await session.execute(count_stmt)).scalar_one())
    stmt = (
        stmt.order_by(AuditLog.created_at.desc(), AuditLog.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    rows = (await session.execute(stmt)).scalars().all()

    import json

    items = []
    for r in rows:
        detail: Any = None
        if r.detail_json:
            try:
                detail = json.loads(r.detail_json)
            except ValueError:
                detail = r.detail_json
        items.append(
            {
                "id": r.id,
                "action": r.action,
                "entity_type": r.entity_type,
                "entity_id": r.entity_id,
                "detail": detail,
                "created_at": r.created_at,
            }
        )

    return ok(
        {
            "total": total,
            "page": page,
            "page_size": page_size,
            "items": items,
        }
    )
