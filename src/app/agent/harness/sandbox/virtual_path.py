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
"""

from __future__ import annotations

import re
from typing import Any

_CHAPTER_PATH = re.compile(r"^/projects/[^/]+/chapters/v(?P<vol>[^/]+)/c(?P<ch>[^/]+)\.md$")
_OUTLINE_PATH = re.compile(r"^/projects/[^/]+/outline/.+\.md$")


def classify_change(virtual_path: str) -> dict[str, Any]:
    """把虚拟路径解析为变更信息（供事件与索引同步）。"""
    p = (virtual_path or "").replace("\\", "/")
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
