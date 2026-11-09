# -*- coding: utf-8 -*-
"""
模块: app.agent.harness.sandbox
文件名: virtual_path.py
作者: xionggaoqing
时间: 2026-11-02
说明: 虚拟路径解析（安全域公共件）：把 `/projects/{pid}/...` 解析为章节/大纲变更信息，
    供 loop 的 FileChangeNotify（变更通知）与 harness/lock 的 ContentLock（编辑锁）共用。

    从原 `loop/middleware/file_change.py` 拆出：路径分类是沙箱域语义，
    harness 不应反向依赖 loop。

    ★ 本模块同时是**路径归一化的唯一口径**（`normalize_virtual_path`）：
    审批（approval/write_approval）、编辑锁（lock/content_lock）、变更通知
    （loop/middleware/file_change）、读取范围（guard/read_scope）、工作区审计
    （guard/workspace_guard）五处判定**必须**先经它归一再匹配前缀。
"""

from __future__ import annotations

import os
import re
from typing import Any

_CHAPTER_PATH = re.compile(r"^/projects/[^/]+/chapters/v(?P<vol>[^/]+)/c(?P<ch>[^/]+)\.md$")
_OUTLINE_PATH = re.compile(r"^/projects/[^/]+/outline/.+\.md$")


def normalize_virtual_path(virtual_path: str) -> str:
    """把工具入参里的路径归一化为**框架实际执行时使用的**虚拟路径。

    口径与 `deepagents.backends.utils.validate_path` 逐字一致
    （`os.path.normpath` → 反斜杠转正斜杠 → 补前导 `/`）。

    为什么必须归一：框架是「先归一，再判权限、再执行」，而守卫拿到的是
    **模型给出的原始入参**。若按字面匹配 `/projects/` 前缀，下列写法都能
    绕过审批 / 编辑锁 / 变更通知 / 读取范围四道防线，而框架照常执行：

        projects/{pid}/chapters/v1/c1.md      # 相对路径 → 归一后带前导 /
        \\projects\\{pid}\\chapters\\v1\\c1.md  # 前导反斜杠
        ./projects/{pid}/outline/x.md         # 带 ./ 前缀

    `delete` 更是 `unlink`/`shutil.rmtree` 级别的不可逆操作，故此处不留侥幸。
    """
    raw = virtual_path or ""
    if not raw:
        return ""
    normalized = os.path.normpath(raw)
    normalized = normalized.replace("\\", "/")
    if not normalized.startswith("/"):
        normalized = f"/{normalized}"
    return normalized


def classify_change(virtual_path: str) -> dict[str, Any]:
    """把虚拟路径解析为变更信息（供事件与索引同步）。"""
    p = normalize_virtual_path(virtual_path)
    info: dict[str, Any] = {"virtual_path": p, "chapter_id": None, "is_outline": False, "in_project": False}
    if p.startswith("/projects/"):
        info["in_project"] = True
        parts = p.split("/")
        info["project_id"] = parts[2] if len(parts) > 2 else ""
        m = _CHAPTER_PATH.match(p)
        if m:
            info["chapter_id"] = m.group("ch")
            info["volume_id"] = m.group("vol")
        info["is_outline"] = bool(_OUTLINE_PATH.match(p))
    return info
