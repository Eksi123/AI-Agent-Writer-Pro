# -*- coding: utf-8 -*-
"""
模块: app.agent.harness.sandbox
文件名: safe_delete.py
作者: xionggaoqing
时间: 2026-11-08
说明: `SafeDeleteMiddleware`：把 Agent 的 `delete` 工具从**永久物理删除**改为
     与 REST 删除同语义的可恢复删除。

    为什么需要它：deepagents 的 `FilesystemBackend.delete` 是
    `resolved_path.unlink()` / `shutil.rmtree(resolved_path)`——直接物理删除，
    不进 `data/recycle/`，产品自带的回收站界面找不到、也恢复不了。而用户从 UI
    删章走的是 `services/volumes.delete_chapter`：归档回收站 + 清修订快照 +
    空卷级联删除 + 卷内重编号 + 刷新索引 + 落审计。同一个动作两条语义，
    AI 误删（或附件里的提示注入诱导删除）损失不可逆。

    做法（不子类化 Backend，避免耦合框架私有 `_resolve_path`）：
    在 `awrap_tool_call` 里拦下 `delete`，**不调用 handler**，改由本中间件执行——
    这正是 `WriteApproval` 拒绝/补充要求分支已经在用的模式。

      - 章节文件 → 调 `volumes.delete_chapter`，拿到与 UI 删除完全相同的语义，
        可在回收站界面恢复；服务层失败则返回错误 ToolMessage（**fail-closed**，
        绝不退化成物理删除）。
      - 项目内其它路径（大纲 / volumes.yaml / 附件 / 目录）→ 整体搬进
        `data/trash/agent-deleted/{ts}/`，保持相对目录结构，可人工找回。
      - 非项目区（技能区只读、conversation_history 等状态文件）→ 原样交框架处理。

    ★ 路径一律先过 `normalize_virtual_path`：框架是「先归一再执行」，
      按字面入参判定会被 `projects/{pid}/...`（相对路径）、`\\projects\\...`
      （反斜杠）绕过，那样本中间件形同不存在。

    挂载位置：`wrap_tool_call` 链中须在 `FileChangeNotify` **内层**（后者要靠
    本中间件返回的成功结果发 `file.changed`），在 `WriteApproval` **内层**
    （删除审批先于执行）。见 `loop/builder.py` 的装配顺序。
"""

from __future__ import annotations

import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from langchain.agents.middleware.types import AgentMiddleware
from langchain_core.messages import ToolMessage

from app.agent.harness.approval.write_approval import virtual_to_host
from app.agent.harness.sandbox.virtual_path import classify_change, normalize_virtual_path
from app.core.logging import get_logger
from app.core.paths import trash_root, workspaces_root
from app.db.session import session_scope

logger = get_logger(__name__)

__all__ = ["SafeDeleteMiddleware", "agent_trash_dir", "move_to_agent_trash"]

#: Agent 删除的文件在此留底（与 `data/trash/{novel_id}-{ts}` 的整本软删除并列）
_AGENT_TRASH_SUBDIR = "agent-deleted"


def agent_trash_dir() -> Path:
    """`data/trash/agent-deleted/`（Agent 删除留底根目录）。"""
    return trash_root() / _AGENT_TRASH_SUBDIR


def move_to_agent_trash(host_path: Path) -> Path | None:
    """把宿主路径整体搬进 `data/trash/agent-deleted/{ts}/{相对 workspaces 的路径}`。

    目录用 `shutil.move`（跨盘时退化为 copytree+rmtree，由 shutil 自行处理），
    **绝不** `unlink`/`rmtree` 原文件而不留底。

    Returns:
        搬移后的目标路径；源不存在或越出 workspaces 范围时返回 `None`。
    """
    src = host_path
    if not src.exists() and not src.is_symlink():
        return None

    ws_root = workspaces_root().resolve()
    try:
        resolved = src.resolve()
        rel = resolved.relative_to(ws_root)
    except (ValueError, OSError):
        # 越出 workspaces 范围（符号链接逃逸等）→ 不猜，交回调用方决定
        logger.warning("agent_trash_path_outside_workspaces", path=str(src))
        return None

    # ★ 不能用 `app.db.base.utcnow()`：它返回的是 **ISO-8601 字符串**（`-> str`），
    #   对其调 `.strftime()` 会 AttributeError；且 ISO 串里的 `:` 在 Windows 上是
    #   非法文件名字符，不能直接拿来当目录名。这里直接取 datetime 再格式化。
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S%f")
    dest = agent_trash_dir() / stamp / rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(src), str(dest))
    logger.info("agent_delete_moved_to_trash", src=str(rel), dest=str(dest))
    return dest


class SafeDeleteMiddleware(AgentMiddleware):
    """把 Agent 的 `delete` 变成可恢复删除（章节走回收站，其它走 trash 留底）。"""

    name = "SafeDelete"

    async def awrap_tool_call(self, request: Any, handler: Any) -> Any:
        tool_name = request.tool_call.get("name") or ""
        if tool_name != "delete":
            return await handler(request)

        tool_call_id = request.tool_call.get("id") or ""
        args = request.tool_call.get("args") or {}
        virtual_path = normalize_virtual_path(str(args.get("file_path") or args.get("path") or ""))
        change = classify_change(virtual_path)
        if not change.get("in_project"):
            # 技能区（只读，permissions 已拒写）/ conversation_history / large_tool_results
            # → 保持框架原有行为
            return await handler(request)

        chapter_id = change.get("chapter_id")
        if chapter_id:
            return await self._delete_chapter(
                request,
                tool_call_id,
                chapter_id,
                virtual_path,
                str(change.get("project_id") or ""),
            )

        host = virtual_to_host(virtual_path)
        if host is None:
            return await handler(request)
        moved = move_to_agent_trash(host)
        if moved is None:
            # 源不存在 → 交框架返回它自己的 not found 错误，文案保持一致
            return await handler(request)
        return ToolMessage(
            content=(
                f"已删除 {virtual_path}。\n"
                f"（该文件已移入删除留底目录，未物理销毁；如需找回请联系用户在文件管理中恢复。）"
            ),
            tool_call_id=tool_call_id,
            name=tool_name,
            status="success",
        )

    # ---- 章节：复用 REST 的删除语义 ----

    async def _delete_chapter(
        self,
        request: Any,
        tool_call_id: str,
        chapter_id: str,
        virtual_path: str,
        project_id: str,
    ) -> Any:
        """调 `volumes.delete_chapter` → 归档回收站 + 清修订 + 空卷级联 + 重编号 + 索引 + 审计。

        失败一律返回错误 ToolMessage（fail-closed），**不退化为物理删除**：
        章节删除牵涉 volumes.yaml、修订快照与目录编号，半途失败时宁可什么都不动。
        """
        from app.services import volumes as volumes_service

        try:
            async with session_scope() as db:
                result = await volumes_service.delete_chapter(
                    db, chapter_id, project_id=project_id or None
                )
        except Exception as exc:  # noqa: BLE001 - 需要把失败如实回给模型
            logger.warning(
                "agent_delete_chapter_failed", chapter_id=chapter_id, error=str(exc)
            )
            return ToolMessage(
                content=(
                    f"删除章节失败（chapter_id={chapter_id}，路径 {virtual_path}）：{exc}\n"
                    "章节删除需要同时更新卷目录、修订快照与章节编号，失败时不会做任何改动。"
                    "请先用 ls / read_file 确认该章节确实存在且路径正确，再决定下一步。"
                ),
                tool_call_id=tool_call_id,
                name=request.tool_call.get("name") or "delete",
                status="error",
            )

        title = (result or {}).get("title") or ""
        volume_deleted = bool((result or {}).get("volume_deleted"))
        note = "该卷已无章节，卷目录一并删除。" if volume_deleted else ""
        return ToolMessage(
            content=(
                f"已删除章节《{title}》（chapter_id={chapter_id}）。\n"
                f"{note}"
                "该章已进入回收站，用户可在界面恢复；修订快照与目录编号已同步更新。"
            ),
            tool_call_id=tool_call_id,
            name=request.tool_call.get("name") or "delete",
            status="success",
        )
