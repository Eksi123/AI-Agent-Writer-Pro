#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
模块: app.services.files
文件名: files.py
作者: xionggaoqing
时间: 2026-10-27
说明: 工作区 YAML 读写：novel.yaml / volumes.yaml / 章节 frontmatter（python-frontmatter）。

    文件为内容事实源（铁律 3）；DB 的 `novels` 行只是缓存，可随时从文件重建。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import frontmatter
import yaml

from app.core.logging import get_logger
from app.core.paths import chapter_file, novel_meta_file, volumes_file
from app.services.workspace import WorkspaceGuard

logger = get_logger(__name__)


def _dump_yaml(data: dict[str, Any]) -> str:
    """统一 YAML 输出：utf-8 友好、保留键序、块状列表。"""
    return yaml.safe_dump(
        data,
        allow_unicode=True,
        sort_keys=False,
        default_flow_style=False,
        width=100,
    )


# ---------------------------------------------------------------------------
# novel.yaml
# ---------------------------------------------------------------------------


def read_novel_meta(project_id: str) -> dict[str, Any]:
    path = novel_meta_file(project_id)
    if not path.exists():
        return {}
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def write_novel_meta(project_id: str, meta: dict[str, Any]) -> None:
    guard = WorkspaceGuard(project_id)
    guard.write("novel.yaml", _dump_yaml(meta))


# ---------------------------------------------------------------------------
# volumes.yaml —— 结构：{"volumes": [{id,title,summary,sort_order}]}
# ---------------------------------------------------------------------------


def read_volumes(project_id: str) -> list[dict[str, Any]]:
    path = volumes_file(project_id)
    if not path.exists():
        return []
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    vols = data.get("volumes") or []
    return [v for v in vols if isinstance(v, dict)]


def write_volumes(project_id: str, volumes: list[dict[str, Any]]) -> None:
    guard = WorkspaceGuard(project_id)
    guard.write("volumes.yaml", _dump_yaml({"volumes": volumes}))


def find_volume(volumes: list[dict[str, Any]], vol_id: str) -> dict[str, Any] | None:
    return next((v for v in volumes if v.get("id") == vol_id), None)


def next_volume_sort(volumes: list[dict[str, Any]]) -> int:
    """新建卷追加到末尾：sort_order = max+1。"""
    return max((int(v.get("sort_order") or 0) for v in volumes), default=0) + 1


# ---------------------------------------------------------------------------
# 章节 frontmatter
# ---------------------------------------------------------------------------

#: 章节 frontmatter 字段（手册「五、文件存储」）。
#: ★必须列全：`read_chapter` 按此白名单过滤，漏列的字段会在「读→改→写」时被丢弃。
CHAPTER_META_KEYS = (
    "title",
    "sort_order",
    "word_count",
    "status",
    "version",
    "updated_at",
    "created_at",
    "last_writer",
    "volume_id",
)


def read_chapter(project_id: str, vol_id: str, chapter_id: str) -> tuple[dict[str, Any], str]:
    """返回 `(meta, content)`。文件不存在抛 FileNotFoundError。

    ★返回**完整** frontmatter（不按白名单过滤），保证「读 → 改 → 写」不丢字段。
    需要定型 DTO 时用 `read_chapter_meta()`。
    """
    path: Path = chapter_file(project_id, vol_id, chapter_id)
    if not path.exists():
        raise FileNotFoundError(str(path))
    post = frontmatter.load(str(path))
    return dict(post.metadata), post.content


def read_chapter_meta(project_id: str, vol_id: str, chapter_id: str) -> dict[str, Any]:
    """只取已知字段（用于列表 DTO / 统计），缺字段不报错。"""
    path = chapter_file(project_id, vol_id, chapter_id)
    if not path.exists():
        raise FileNotFoundError(str(path))
    post = frontmatter.load(str(path))
    return {k: post.metadata.get(k) for k in CHAPTER_META_KEYS if k in post.metadata}


def write_chapter(
    project_id: str,
    vol_id: str,
    chapter_id: str,
    meta: dict[str, Any],
    content: str,
) -> Path:
    """写章节文件：frontmatter + 正文。父目录自动创建。"""
    path = chapter_file(project_id, vol_id, chapter_id)
    post = frontmatter.Post(content, **meta)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(frontmatter.dumps(post) + "\n", encoding="utf-8")
    return path


def list_chapter_files(project_id: str, vol_id: str) -> list[Path]:
    d = chapter_file(project_id, vol_id, "x").parent
    if not d.exists():
        return []
    return sorted(d.glob("c*.md"))


def count_words(text: str) -> int:
    """中文字数统计：去除 markdown 标记与空白后按字符计。"""
    import re

    cleaned = re.sub(r"[#>*`\-\[\]!|]", "", text)
    cleaned = re.sub(r"\s+", "", cleaned)
    return len(cleaned)
