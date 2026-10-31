# -*- coding: utf-8 -*-
"""
模块: app.core.paths
文件名: paths.py
作者: xionggaoqing
时间: 2026-10-27
说明: 路径解析：统一 pathlib，所有文件读写显式 `encoding="utf-8"`（手册铁律 11）。

    数据根布局见手册「五 → 文件存储」：
        data/
        ├── workspaces/{project_id}/{novel.yaml,volumes.yaml,chapters/,outline/,summaries/}
        ├── skills/{name}/SKILL.md
        ├── exports/
        ├── trash/{project_id}-{ts}/
        ├── recycle/{project_id}/{index.yaml,chapters/}   ← v1031 回收站（最多 20 本）
        └── logs/
"""

from __future__ import annotations

import re
from pathlib import Path

from app.core.config import get_settings

#: Windows 文件名非法字符，小说名派生大纲文件名时替换为 `_`
_ILLEGAL_FILENAME_CHARS = re.compile(r'[\\/:*?"<>|\x00-\x1f]')

# 工作区内的固定子结构
CHAPTERS_DIR = "chapters"
OUTLINE_DIR = "outline"
SUMMARIES_DIR = "summaries"
NOVEL_META_FILE = "novel.yaml"
VOLUMES_FILE = "volumes.yaml"

#: 内置技能目录（随包只读，物理位置在 `agent/skills/`）。
#: 放这里而不是 agent 内：它只是个**路径常量**（目录本体仍在 agent/skills/），
#: services 与 agent 都要用，下沉到 core 可消除 services → agent 的反向依赖。
BUILTIN_SKILLS_DIR = Path(__file__).resolve().parents[1] / "agent" / "skills"


def data_root() -> Path:
    root = Path(get_settings().data_root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


def workspaces_root() -> Path:
    p = data_root() / "workspaces"
    p.mkdir(parents=True, exist_ok=True)
    return p


def workspace_dir(project_id: str) -> Path:
    """某个小说项目的工作区根目录。"""
    return workspaces_root() / project_id


def chapters_dir(project_id: str) -> Path:
    return workspace_dir(project_id) / CHAPTERS_DIR


def volume_dir(project_id: str, vol_id: str) -> Path:
    """`chapters/v{vol_id}/`"""
    return chapters_dir(project_id) / f"v{vol_id}"


def chapter_file(project_id: str, vol_id: str, chapter_id: str) -> Path:
    """`chapters/v{vol_id}/c{chapter_id}.md`（文件名含 ID，ID 永不改变）"""
    return volume_dir(project_id, vol_id) / f"c{chapter_id}.md"


def outline_dir(project_id: str) -> Path:
    return workspace_dir(project_id) / OUTLINE_DIR


def summaries_dir(project_id: str) -> Path:
    return workspace_dir(project_id) / SUMMARIES_DIR


def summary_file(project_id: str, chapter_id: str) -> Path:
    """`summaries/ch-{chapter_id}.md`"""
    return summaries_dir(project_id) / f"ch-{chapter_id}.md"


def novel_meta_file(project_id: str) -> Path:
    return workspace_dir(project_id) / NOVEL_META_FILE


def volumes_file(project_id: str) -> Path:
    return workspace_dir(project_id) / VOLUMES_FILE


def skills_root() -> Path:
    p = data_root() / "skills"
    p.mkdir(parents=True, exist_ok=True)
    return p


def session_skills_root() -> Path:
    """会话级技能物化根：`data/session-skills/`。"""
    p = data_root() / "session-skills"
    p.mkdir(parents=True, exist_ok=True)
    return p


def session_skills_dir(session_id: str) -> Path:
    """某会话选中的技能物化目录：`data/session-skills/{session_id}/`。"""
    p = session_skills_root() / session_id
    p.mkdir(parents=True, exist_ok=True)
    return p


def exports_root() -> Path:
    p = data_root() / "exports"
    p.mkdir(parents=True, exist_ok=True)
    return p


def trash_root() -> Path:
    p = data_root() / "trash"
    p.mkdir(parents=True, exist_ok=True)
    return p


#: v1031：回收站根目录（被删除的**章节**按小说归档在这里）
RECYCLE_DIR = "recycle"


def recycle_root() -> Path:
    """回收站根：`data/recycle/`。

    布局（v1031）：
        data/recycle/{project_id}/index.yaml        ← 该小说被删章节清单（含书名/卷名快照）
        data/recycle/{project_id}/chapters/c{id}.md ← 被删章节原文件（frontmatter + 正文）
    """
    p = data_root() / RECYCLE_DIR
    p.mkdir(parents=True, exist_ok=True)
    return p


def recycle_novel_dir(project_id: str) -> Path:
    """某本小说在回收站里的归档目录。"""
    return recycle_root() / project_id


def recycle_chapters_dir(project_id: str) -> Path:
    return recycle_novel_dir(project_id) / CHAPTERS_DIR


def recycle_index_file(project_id: str) -> Path:
    return recycle_novel_dir(project_id) / "index.yaml"


def logs_root() -> Path:
    p = data_root() / "logs"
    p.mkdir(parents=True, exist_ok=True)
    return p


def safe_filename(title: str, *, fallback: str = "untitled") -> str:
    """由小说标题派生安全文件名主体（替换 Windows 非法字符为 `_`）。

    大纲文件名 = `{safe_filename(title)}-大纲.md`。
    """
    cleaned = _ILLEGAL_FILENAME_CHARS.sub("_", title).strip().strip(".")
    # 压缩连续空白为单个下划线，避免文件名过长或含空格歧义
    cleaned = re.sub(r"\s+", "_", cleaned)
    return cleaned or fallback


def outline_filename(title: str) -> str:
    """`{小说名称}-大纲.md`"""
    return f"{safe_filename(title)}-大纲.md"


def outline_relpath(title: str) -> str:
    """novel.yaml.outline_file 存放的相对路径（正斜杠，跨平台稳定）。"""
    return f"{OUTLINE_DIR}/{outline_filename(title)}"


def init_workspace(project_id: str) -> Path:
    """初始化工作区目录结构（创建项目时调用）。"""
    ws = workspace_dir(project_id)
    for d in (ws, chapters_dir(project_id), outline_dir(project_id), summaries_dir(project_id)):
        d.mkdir(parents=True, exist_ok=True)
    return ws


def write_text(path: Path, content: str) -> None:
    """统一写入口：确保父目录存在 + utf-8。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")
