# -*- coding: utf-8 -*-
"""
模块: app.utils.time
文件名: time.py
说明: 时间处理工具函数

    - utcnow: 生成 UTC ISO-8601 时间戳
    - parse_ts: 解析时间戳字符串为 datetime
"""

from __future__ import annotations

from datetime import UTC, datetime


def utcnow() -> str:
    """生成 UTC ISO-8601 时间戳字符串。
    
    Returns:
        格式: '2026-10-27T10:30:45.123Z'
    """
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def parse_ts(value: str | None) -> datetime | None:
    """解析时间戳字符串为 datetime 对象。
    
    Args:
        value: ISO-8601 时间戳字符串（如 '2026-10-27T10:30:45.123Z'）
        
    Returns:
        datetime 对象，解析失败返回 None
    """
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
