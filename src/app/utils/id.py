# -*- coding: utf-8 -*-
"""
模块: app.utils.id
文件名: id.py
说明: ID 生成工具函数

    - new_id: 生成 UUID4 主键
    - short_id: 生成卷/章短 ID（8 位 hex）
"""

from __future__ import annotations

import uuid


def new_id() -> str:
    """生成 UUID4 字符串主键（36 位，含连字符）。"""
    return str(uuid.uuid4())


def short_id(existing: set[str] | None = None) -> str:
    """生成卷/章短 ID：UUID 前 8 位 hex 字符串。
    
    Args:
        existing: 已存在的 ID 集合，用于冲突检测
        
    Returns:
        8 位 hex 字符串（如 'a1b2c3d4'）
        
    Note:
        短 ID 一旦生成永不改变（铁律 10）
    """
    taken = existing or set()
    while True:
        candidate = uuid.uuid4().hex[:8]
        if candidate not in taken:
            return candidate
