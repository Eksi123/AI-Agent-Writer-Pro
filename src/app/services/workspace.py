# -*- coding: utf-8 -*-
"""
模块: app.services.workspace
文件名: workspace.py
作者: xionggaoqing
时间: 2026-10-27
说明: `WorkspaceGuard`：服务层唯一文件写入口（手册「二、(5)」规范 1–6）。

    规则：
    1. 入参接受「工作区相对路径」，内部换算为宿主路径；
    2. `resolve()` 归一化后必须严格落在白名单根内，否则 `PermissionError` → HTTP 403；
    3. 拒绝含 `..` 的路径、拒绝解析后越界的符号链接（逐段检查）；
    4. 跨项目写一律拒绝；
    5. `summaries/` 只允许 `summarize_chapter` 服务写；
    6. 写入成功后同步更新派生索引（M4 接入 FTS，此处更新 project_index）。
"""

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

from app.core.errors import ForbiddenError
from app.core.logging import get_logger
from app.core.paths import (
    SUMMARIES_DIR,
    data_root,
    exports_root,
    skills_root,
    workspace_dir,
)

logger = get_logger(__name__)

#: 允许写入的白名单根（相对 data/）
_WRITABLE_ROOTS = ("workspaces", "skills", "exports", "trash", "logs")


def atomic_write_text(path: Path, content: str, *, encoding: str = "utf-8") -> None:
    """原子写文本：先写**同目录**临时文件并 fsync，再 `os.replace` 覆盖目标。

    ★ 直接 `path.write_text` 不是原子的：它先把目标**截断为零长**再写入。进程在
      写入途中被杀（重启、崩溃、磁盘满、Ctrl+C）就会留下一个半截文件——
      对章节正文即**丢稿**；更糟的是 frontmatter 被截断后 `files.read_chapter`
      的 `frontmatter.load` 直接抛异常，该章在 UI 上完全打不开，只能手工修文件。
      `os.replace` 在同一文件系统内是原子重命名：读者要么看到旧内容、要么看到
      新内容，绝不会看到中间态。临时文件必须与目标同目录，否则跨设备 rename
      会退化成「复制 + 删除」，原子性失效。

    ★ 有意**不**复制原文件的权限位：Windows 上 `os.chmod` 只能切换只读标志，
      若原文件恰为只读，把临时文件也置只读会让随后的 `os.replace` 失败。

    ★ 有意**不**传 `newline=""`：被本函数替换掉的 `Path.write_text(content, encoding=...)`
      默认 `newline=None`，写模式下会把 `\n` 翻译成 `os.linesep`（Windows 即 CRLF）。
      `os.fdopen` 的默认 `newline` 与之相同，故保持不传——本次改动的目标只是
      「原子性」，不该顺手改掉既有文件的行尾。若写成 LF，`record_agent_write`
      会在每次 AI 写入后把章节文件整体翻成 LF，而 deepagents 的 `edit_file`
      靠**字面量匹配**改写文件，行尾突变可能让模型基于旧内容给出的 `old_string`
      匹配失败。

    失败时清掉临时文件再抛，不留 `.tmp` 垃圾（`list_chapter_files` 只 glob
    `c*.md`，但仍应避免污染工作区）。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding=encoding) as fh:
            fh.write(content)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def _is_within(path: Path, root: Path) -> bool:
    """path 是否严格位于 root 之内（不含 root 自身）。"""
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return path != root


def _check_symlink_escape(target: Path, root: Path) -> None:
    """逐段检查符号链接是否解析后越界。"""
    for part in target.relative_to(root).parts[:-1]:
        root = root / part
        if root.is_symlink():
            resolved = root.resolve()
            if not _is_within(resolved, data_root()):
                raise ForbiddenError(f"符号链接越界：{root}")


class WorkspaceGuard:
    """按项目实例化：`WorkspaceGuard(project_id)`。"""

    def __init__(self, project_id: str) -> None:
        self.project_id = project_id
        self.root = workspace_dir(project_id).resolve()

    # ---------- 路径换算 ----------

    def resolve(self, rel_path: str | Path) -> Path:
        """工作区相对路径 → 宿主绝对路径，并做全部安全校验。"""
        rel = str(rel_path).replace("\\", "/").strip()
        if not rel:
            raise ForbiddenError("路径不能为空")
        if rel.startswith("/") or (len(rel) > 1 and rel[1] == ":"):
            raise ForbiddenError(f"拒绝绝对路径：{rel}")
        if ".." in Path(rel).parts:
            raise ForbiddenError(f"拒绝含 .. 的路径：{rel}")

        target = (self.root / rel).resolve()
        if not _is_within(target, self.root) and target != self.root:
            raise ForbiddenError(f"路径越出项目工作区：{rel}")
        if target.exists():
            _check_symlink_escape(target, self.root)
        return target

    def resolve_shared(self, rel_path: str | Path) -> Path:
        """data/ 下共享区（skills / exports / logs）路径换算。"""
        rel = str(rel_path).replace("\\", "/").strip()
        parts = Path(rel).parts
        if not parts or ".." in parts or rel.startswith("/"):
            raise ForbiddenError(f"非法共享路径：{rel}")
        if parts[0] not in _WRITABLE_ROOTS or parts[0] == "workspaces":
            raise ForbiddenError(f"非白名单根：{parts[0]}")
        target = (data_root() / rel).resolve()
        if not _is_within(target, data_root()):
            raise ForbiddenError(f"路径越出数据根：{rel}")
        return target

    # ---------- 读 ----------

    def read(self, rel_path: str | Path) -> str:
        path = self.resolve(rel_path)
        if not path.is_file():
            raise FileNotFoundError(str(rel_path))
        return path.read_text(encoding="utf-8")

    def exists(self, rel_path: str | Path) -> bool:
        return self.resolve(rel_path).exists()

    # ---------- 写 ----------

    def write(
        self,
        rel_path: str | Path,
        content: str,
        *,
        allow_summaries: bool = False,
    ) -> Path:
        """写文件（utf-8，自动建父目录）。

        `summaries/` 仅当 `allow_summaries=True`（由 `summarize_chapter` 传入）时可写。
        """
        rel = str(rel_path).replace("\\", "/")
        if rel.startswith(f"{SUMMARIES_DIR}/") and not allow_summaries:
            raise ForbiddenError("summaries/ 只允许 summarize_chapter 服务写入")

        path = self.resolve(rel)
        atomic_write_text(path, content)
        logger.debug("workspace_write", project_id=self.project_id, path=rel)
        return path

    def delete(self, rel_path: str | Path) -> bool:
        """删除文件或目录（目录递归）。返回是否实际删除。"""
        path = self.resolve(rel_path)
        if not path.exists():
            return False
        if path.is_dir():
            shutil.rmtree(path)
        else:
            os.unlink(path)
        logger.info("workspace_delete", project_id=self.project_id, path=str(rel_path))
        return True

    def mkdir(self, rel_path: str | Path) -> Path:
        path = self.resolve(rel_path)
        path.mkdir(parents=True, exist_ok=True)
        return path

    # ---------- 共享区 ----------

    @staticmethod
    def write_shared(rel_path: str | Path, content: str) -> Path:
        path = WorkspaceGuard("").resolve_shared(rel_path)
        atomic_write_text(path, content)
        return path

    @staticmethod
    def shared_root() -> Path:
        return data_root()

    @staticmethod
    def exports_root() -> Path:
        return exports_root()

    @staticmethod
    def skills_root() -> Path:
        return skills_root()
