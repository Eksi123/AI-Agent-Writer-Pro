# -*- coding: utf-8 -*-
"""
模块: app.agent.harness.guard.workspace_guard
文件名: workspace_guard.py
作者: xionggaoqing
时间: 2026-10-27
说明: `WorkspaceGuardMiddleware`：`awrap_tool_call` 做路径校验兜底、审计、耗时/结果大小统计、
    **错误指纹记忆与阻断**（重复错误避免，手册「二、(1)」——内置中间件无此能力，自研）。

    与 deepagents `permissions` 的关系：permissions 只作用于内置文件系统工具，是**第一道**；
    本中间件是**第二道**（自定义工具/MCP 不在 permissions 覆盖范围），并负责可观测与重复错误阻断。
"""

from __future__ import annotations

import hashlib
import json
import time
from collections import defaultdict
from typing import Any

from langchain.agents.middleware.types import AgentMiddleware
from langchain_core.messages import ToolMessage
from langgraph.errors import GraphBubbleUp

from app.core.logging import get_logger

logger = get_logger(__name__)

#: 读操作 → read 权限；写/删 → write 权限（delete 归入 write，见手册「二、(3)」）
_TOOL_OPERATION = {
    "ls": "read",
    "read_file": "read",
    "glob": "read",
    "grep": "read",
    "write_file": "write",
    "edit_file": "write",
    "delete": "write",
}

#: 同一 (tool, args_hash, error) 组合的阈值
_WARN_AFTER = 2
_BLOCK_AFTER = 3

#: 大工具结果截断（对话历史瘦身）：read_file 返回超过该字符数时截断并卸载全文
_READ_TRIM_LIMIT = 8000
_READ_HEAD = 3000
_READ_TAIL = 1000


def _args_path(args: dict[str, Any]) -> str:
    """从工具参数取路径（各工具参数名不同）。"""
    for key in ("file_path", "path"):
        value = args.get(key)
        if isinstance(value, str) and value:
            return value
    pattern = args.get("pattern")
    return pattern if isinstance(pattern, str) else ""


class WorkspaceGuardMiddleware(AgentMiddleware):
    """路径校验 + 审计埋点 + 错误指纹记忆。"""

    name = "WorkspaceGuard"

    def __init__(self, *, result_excerpt_len: int = 500) -> None:
        super().__init__()
        self._excerpt_len = result_excerpt_len
        #: (tool_name, args_hash, error_fingerprint) → 次数
        self._error_counts: dict[tuple[str, str, str], int] = defaultdict(int)
        self._blocked: set[tuple[str, str, str]] = set()
        self.stats: dict[str, int] = defaultdict(int)

    # ---- 路径校验（permissions 之外的第二道）----

    def check_path(self, virtual_path: str, operation: str, project_id: str) -> str | None:
        """校验虚拟路径。返回错误文案（None = 放行）。"""
        if not virtual_path:
            return None  # 无路径参数（如 web_search）
        p = virtual_path.replace("\\", "/")
        if ".." in p.split("/"):
            return f"路径包含 ..，拒绝访问：{virtual_path}"
        if not p.startswith("/"):
            return None  # 相对路径由 backend 解析，permissions 已覆盖

        # 项目区：写操作只允许当前项目
        if p.startswith("/projects/"):
            parts = p.split("/")
            pid = parts[2] if len(parts) > 2 else ""
            if operation == "write" and pid != project_id:
                return f"跨项目写入被拒绝：{virtual_path}（当前项目 {project_id}）"
            return None
        # 技能区只读
        if p.startswith("/skills/"):
            if operation == "write":
                return f"技能目录只读，拒绝写入：{virtual_path}"
            return None
        # 卸载区可读写
        if p.startswith("/conversation_history/") or p.startswith("/large_tool_results/"):
            return None
        return f"路径越出授权范围，拒绝{operation}：{virtual_path}"

    # ---- 错误指纹 ----

    @staticmethod
    def _fingerprint(tool_name: str, args: dict[str, Any], error: str) -> tuple[str, str, str]:
        try:
            args_repr = json.dumps(args, ensure_ascii=False, sort_keys=True, default=str)
        except (TypeError, ValueError):
            args_repr = repr(args)
        args_hash = hashlib.sha1(args_repr.encode("utf-8")).hexdigest()[:16]
        err_hash = hashlib.sha1(error.strip().encode("utf-8")).hexdigest()[:16]
        return tool_name, args_hash, err_hash

    def note_error(self, tool_name: str, args: dict[str, Any], error: str) -> str | None:
        """记录一次失败。返回 `None` 放行、字符串则为**阻断提示**（注入到工具结果）。"""
        key = self._fingerprint(tool_name, args, error)
        if key in self._blocked:
            return (
                f"该调用已连续失败 {_BLOCK_AFTER} 次并被阻断（tool={tool_name}）。"
                f"请改变参数或换用其它工具，不要重复同一调用。原始错误：{error[:200]}"
            )
        self._error_counts[key] += 1
        count = self._error_counts[key]
        if count >= _BLOCK_AFTER:
            self._blocked.add(key)
            return (
                f"该调用已连续失败 {count} 次，现予以阻断（tool={tool_name}）。"
                f"请改变参数或换用其它工具。原始错误：{error[:200]}"
            )
        if count >= _WARN_AFTER:
            return (
                f"[警告] 该调用已失败 {count} 次且错误相同（tool={tool_name}）。"
                f"再失败一次将被阻断，请先检查参数。原始错误：{error[:200]}"
            )
        return None

    # ---- 主钩子 ----

    async def awrap_tool_call(self, request: Any, handler: Any) -> Any:
        tool_name = request.tool_call.get("name") or ""
        args = request.tool_call.get("args") or {}
        context = getattr(request.runtime, "context", None)
        project_id = getattr(context, "project_id", "") or ""
        emit = getattr(context, "emit", None)
        operation = _TOOL_OPERATION.get(tool_name, "read")

        # 取消标志（协作式取消：当前工具返回后停）
        cancel_flag = getattr(context, "cancel_flag", None) or {}
        if cancel_flag.get("cancelled"):
            return ToolMessage(
                content="运行已被用户取消，本次工具调用未执行。",
                tool_call_id=request.tool_call.get("id") or "",
                name=tool_name,
                status="error",
            )

        # 路径校验
        guard_error = self.check_path(_args_path(args), operation, project_id)
        if guard_error is not None:
            self.stats["denied"] += 1
            logger.warning("agent_path_denied", tool=tool_name, project_id=project_id, reason=guard_error)
            if emit is not None:
                await emit.publish(
                    "tool.result",
                    {"tool_name": tool_name, "status": "denied", "error": guard_error},
                )
            return ToolMessage(
                content=f"权限拒绝：{guard_error}",
                tool_call_id=request.tool_call.get("id") or "",
                name=tool_name,
                status="error",
            )

        started = time.perf_counter()
        await self._record_audit_start(context, tool_name, args)
        try:
            result = await handler(request)
        except GraphBubbleUp:
            # langgraph 控制流（interrupt() 的审批/咨询/ask_user 暂停）不是工具失败——
            # 必须原样上抛，否则中断会被吞成「工具执行失败」导致 run 无法挂起
            raise
        except Exception as exc:
            duration_ms = int((time.perf_counter() - started) * 1000)
            self.stats["error"] += 1
            blocked_note = self.note_error(tool_name, args, str(exc))
            await self._log_tool_call(context, tool_name, args, "", "error", str(exc), duration_ms)
            if emit is not None:
                await emit.publish(
                    "tool.result",
                    {
                        "tool_name": tool_name,
                        "status": "error",
                        "error": str(exc)[:500],
                        "duration_ms": duration_ms,
                    },
                )
            message = blocked_note or f"工具执行失败：{exc}"
            return ToolMessage(
                content=message,
                tool_call_id=request.tool_call.get("id") or "",
                name=tool_name,
                status="error",
            )

        duration_ms = int((time.perf_counter() - started) * 1000)
        result = self._trim_large_read(request, result)
        excerpt, size, status, error_text = self._inspect_result(result)
        self.stats["success" if status == "success" else "error"] += 1

        if status == "error" and error_text:
            blocked_note = self.note_error(tool_name, args, error_text)
            if blocked_note and isinstance(result, ToolMessage):
                result = ToolMessage(
                    content=blocked_note,
                    tool_call_id=result.tool_call_id,
                    name=tool_name,
                    status="error",
                )
        else:
            # 成功即清空该工具的失败计数键（参数不同则键不同，天然隔离）
            pass

        await self._log_tool_call(context, tool_name, args, excerpt, status, error_text, duration_ms)
        if emit is not None:
            await emit.publish(
                "tool.result",
                {
                    "tool_name": tool_name,
                    "status": status,
                    "result_excerpt": excerpt[:300],
                    "duration_ms": duration_ms,
                    "size": size,
                },
            )
        return result

    # ---- 内部 ----

    def _trim_large_read(self, request: Any, result: Any) -> Any:
        """read_file 结果过大时截断进历史：头 + 尾 + 省略提示（对话历史瘦身）。

        提示中引导用 read_file 的 offset/limit 分段重读原文件（deepagents 原生分页），
        不做额外卸载文件，避免状态区写入的脆弱性。
        """
        tool_name = request.tool_call.get("name") or ""
        if tool_name != "read_file" or not isinstance(result, ToolMessage):
            return result
        content = result.content
        if not isinstance(content, str) or len(content) <= _READ_TRIM_LIMIT:
            return result
        path = _args_path(request.tool_call.get("args") or {})
        omitted = len(content) - _READ_HEAD - _READ_TAIL
        hint = f"…（省略 {omitted} 字）如需全文请用 read_file 的 offset/limit 分段读取{('：' + path) if path else ''}…"
        trimmed = content[:_READ_HEAD] + "\n" + hint + "\n" + content[-_READ_TAIL:]
        logger.info("large_read_trimmed", size=len(content), trimmed_to=len(trimmed))
        return ToolMessage(
            content=trimmed,
            tool_call_id=result.tool_call_id,
            name=result.name,
            status=getattr(result, "status", None),
        )

    def _inspect_result(self, result: Any) -> tuple[str, int, str, str | None]:
        """返回 (excerpt, size, status, error)。"""
        content = getattr(result, "content", result)
        if not isinstance(content, str):
            try:
                content = json.dumps(content, ensure_ascii=False, default=str)
            except (TypeError, ValueError):
                content = str(content)
        status_attr = getattr(result, "status", None)
        error_text = None
        status = "success"
        if status_attr == "error":
            status = "error"
            error_text = content[:500]
        return content[: self._excerpt_len], len(content), status, error_text

    async def _record_audit_start(self, context: Any, tool_name: str, args: dict[str, Any]) -> None:
        """工具调用前发 `tool.start`（审计入库在 runner 收尾统一做，避免并发写）。"""
        emit = getattr(context, "emit", None)
        if emit is None:
            return
        safe_args = {k: (v[:200] + "…" if isinstance(v, str) and len(v) > 200 else v) for k, v in args.items()}
        await emit.publish("tool.start", {"tool_name": tool_name, "args": safe_args})

    async def _log_tool_call(
        self,
        context: Any,
        tool_name: str,
        args: dict[str, Any],
        excerpt: str,
        status: str,
        error: str | None,
        duration_ms: int,
    ) -> None:
        """落 `tool_call_logs`（独立 session，失败不阻断）。"""
        session_id = getattr(context, "session_id", "") or ""
        run_id = getattr(context, "run_id", "") or ""
        if not session_id:
            return
        try:
            from app.db.base import new_id, utcnow
            from app.db.models import ToolCallLog
            from app.db.session import session_scope

            async with session_scope() as db:
                db.add(
                    ToolCallLog(
                        id=new_id(),
                        session_id=session_id,
                        run_id=run_id or None,
                        tool_name=tool_name,
                        args_json=json.dumps(args, ensure_ascii=False, default=str)[:8000],
                        result_excerpt=excerpt,
                        status=status,
                        error=error,
                        duration_ms=duration_ms,
                        created_at=utcnow(),
                    )
                )
        except Exception as exc:  # pragma: no cover - 日志失败不阻断运行
            logger.warning("tool_call_log_failed", tool=tool_name, error=str(exc))
