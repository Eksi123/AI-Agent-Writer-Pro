# -*- coding: utf-8 -*-
"""
模块: app.agent.loop.knowledge.chapters
文件名: chapters.py
作者: xionggaoqing
时间: 2026-10-27
说明: 按大纲派生顺序列出章节元信息（知识库各函数的公共取数）。
"""

from __future__ import annotations

from typing import Any

from app.services import files


async def list_chapters_ordered(project_id: str) -> list[dict[str, Any]]:
    """按大纲派生顺序列出全部章节元信息（含 volume_id / title / sort_order / status）。"""
    volumes = files.read_volumes(project_id)
    volumes.sort(key=lambda v: int(v.get("sort_order") or 0))
    out: list[dict[str, Any]] = []
    for vol in volumes:
        vol_id = str(vol.get("id"))
        vol_sort = int(vol.get("sort_order") or 0)
        metas: list[dict[str, Any]] = []
        for path in files.list_chapter_files(project_id, vol_id):
            chapter_id = path.stem[1:]
            try:
                meta, _content = files.read_chapter(project_id, vol_id, chapter_id)
            except (FileNotFoundError, OSError):
                continue
            meta = dict(meta)
            meta["id"] = chapter_id
            meta["volume_id"] = vol_id
            meta["volume_title"] = vol.get("title")
            meta["_vol_sort"] = vol_sort
            metas.append(meta)
        metas.sort(key=lambda m: int(m.get("sort_order") or 0))
        out.extend(metas)
    return out
