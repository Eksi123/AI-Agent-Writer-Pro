# -*- coding: utf-8 -*-
"""
模块: app.agent.harness.approval.write_approval
文件名: write_approval.py
作者: xionggaoqing
时间: 2026-10-27
说明: `WriteApprovalMiddleware`：按**参数**判定高危写入 → `langgraph.types.interrupt()` 转人工审批。

    手册「二、(2) 人为介入」第 2 层：`interrupt_on` 只能做工具级开关、无法按参数判定，
    故「整章覆盖 / 大范围 edit / 批量 delete」由本中间件在 `awrap_tool_call` 内触发中断。

    高危判定（任一命中即中断，阈值可配）：
    - `write_file` 目标是**已存在的非空章节文件**且新内容与旧内容不同 → 整章覆盖；
    - `edit_file` 的 `old_string` 或 `new_string` 超过 `edit_threshold` 字符 → 大范围改写；
    - `delete` 任何项目内文件/目录 → 不可逆删除。

    恢复语义（`decision`）：
    - `approve` → 原样执行；`edit` → 用 `edited_args` 替换参数后执行；
    - `reject` → 不执行，向模型返回「用户拒绝」ToolMessage；
    - `respond` → 不执行，把用户补充说明 `message` 回给模型。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from langchain.agents.middleware.types import AgentMiddleware
from langchain_core.messages import ToolMessage
from langgraph.types import interrupt

from app.core.logging import get_logger
from app.core.paths import workspaces_root
from app.db.base import new_id, utcnow

logger = get_logger(__name__)

#: 中断标记（runner 据此区分自定义中断与 langchain HITL 批量中断）
INTERRUPT_KIND = "writer_approval"

#: 默认阈值
DEFAULT_EDIT_THRESHOLD = 800
DEFAULT_OVERWRITE_THRESHOLD = 200


def virtual_to_host(virtual_path: str) -> Path | None:
    """`/projects/{pid}/...` → 宿主路径（仅用于**只读**探测，不做写入）。"""
    p = (virtual_path or "").replace("\\", "/")
    if not p.startswith("/projects/"):
        return None
    rel = p[len("/projects/") :]
    if not rel or ".." in rel.split("/"):
        return None
    return workspaces_root() / rel


def classify_risk(
    tool_name: str,
    args: dict[str, Any],
    *,
    edit_threshold: int = DEFAULT_EDIT_THRESHOLD,
    overwrite_threshold: int = DEFAULT_OVERWRITE_THRESHOLD,
) -> tuple[str | None, str]:
    """判定是否高危。

    Returns:
        (risk_kind, reason)；`risk_kind` 为 None 表示放行。
    """
    if tool_name == "delete":
        path = str(args.get("file_path") or args.get("path") or "")
        if path.startswith("/projects/"):
            host = virtual_to_host(path)
            is_dir = bool(host and host.is_dir())
            return "delete", f"删除{'目录' if is_dir else '文件'}不可逆：{path}"

    if tool_name == "write_file":
        path = str(args.get("file_path") or "")
        content = str(args.get("content") or "")
        if not path.startswith("/projects/"):
            return None, ""
        host = virtual_to_host(path)
        if host is not None and host.is_file():
            try:
                old = host.read_text(encoding="utf-8")
            except OSError:
                return None, ""
            if len(old.strip()) >= overwrite_threshold and old != content:
                return "overwrite", (
                    f"整章覆盖：{path} 已有 {len(old)} 字符正文，将被 {len(content)} 字符替换"
                )
        return None, ""

    if tool_name == "edit_file":
        old_string = str(args.get("old_string") or "")
        new_string = str(args.get("new_string") or "")
        path = str(args.get("file_path") or "")
        if not path.startswith("/projects/"):
            return None, ""
        if len(old_string) >= edit_threshold or len(new_string) >= edit_threshold:
            return "large_edit", (
                f"大范围改写：{path}（old {len(old_string)} 字符 / new {len(new_string)} 字符）"
            )
        return None, ""

    return None, ""


class WriteApprovalMiddleware(AgentMiddleware):
    """参数级高危写入审批。"""

    name = "WriteApproval"

    def __init__(
        self,
        *,
        enabled: bool = True,
        edit_threshold: int = DEFAULT_EDIT_THRESHOLD,
        overwrite_threshold: int = DEFAULT_OVERWRITE_THRESHOLD,
        allowed_decisions: tuple[str, ...] = ("approve", "edit", "reject", "respond"),
        skip_tools: tuple[str, ...] = ("delete",),
    ) -> None:
        super().__init__()
        self.enabled = enabled
        self.edit_threshold = edit_threshold
        self.overwrite_threshold = overwrite_threshold
        self.allowed_decisions = list(allowed_decisions)
        #: 已由工具级 `interrupt_on` 覆盖的工具，避免同一调用双重审批
        self.skip_tools = set(skip_tools)

    async def awrap_tool_call(self, request: Any, handler: Any) -> Any:
        if not self.enabled:
            return await handler(request)

        tool_name = request.tool_call.get("name") or ""
        if tool_name in self.skip_tools:
            return await handler(request)

        args = dict(request.tool_call.get("args") or {})
        tool_call_id = request.tool_call.get("id") or ""

        risk_kind, reason = classify_risk(
            tool_name, args, edit_threshold=self.edit_threshold, overwrite_threshold=self.overwrite_threshold
        )
        if risk_kind is None:
            return await handler(request)

        context = getattr(request.runtime, "context", None)
        run_id = getattr(context, "run_id", "") or ""

        # 已有决定（中断恢复重放）→ 直接执行，不重复中断
        existing = await self._find_decided(run_id, tool_call_id)
        if existing is not None:
            return await self._apply(existing, request, handler, tool_name, args)

        payload = {
            "kind": INTERRUPT_KIND,
            "risk": risk_kind,
            "reason": reason,
            "tool_name": tool_name,
            "args": _truncate_args(args),
            "tool_call_id": tool_call_id,
            "decisions": self.allowed_decisions,
        }

        interrupt_id = await self._create_interrupt(context, payload)
        emit = getattr(context, "emit", None)
        if emit is not None:
            await emit.publish(
                "interrupt.request",
                {
                    "interrupt_id": interrupt_id,
                    "tool_name": tool_name,
                    "args": payload["args"],
                    "decisions": self.allowed_decisions,
                    "risk": risk_kind,
                    "reason": reason,
                },
            )
        await self._set_run_status(context, "awaiting_approval")

        # ★挂起：resume 时本方法会重跑，interrupt() 直接返回决定
        decision = interrupt(payload)

        record = await self._record_decision(run_id, tool_call_id, interrupt_id, decision)
        return await self._apply(record, request, handler, tool_name, args)

    # ---- 决定应用 ----

    async def _apply(self, record: dict[str, Any], request: Any, handler: Any, tool_name: str, args: dict[str, Any]) -> Any:
        decision = record.get("decision") or "approve"
        tool_call_id = request.tool_call.get("id") or ""

        if decision == "approve":
            return await handler(request)

        if decision == "edit":
            edited = record.get("edited_args") or args
            new_request = request.override(tool_call={**request.tool_call, "args": edited})
            logger.info("agent_write_approved_with_edit", tool=tool_name)
            return await handler(new_request)

        if decision == "reject":
            return ToolMessage(
                content=(
                    f"用户拒绝了本次「{tool_name}」操作（{record.get('reason') or '高危写入'}），"
                    "未执行任何改动。请询问用户希望如何调整，不要重复提交相同调用。"
                ),
                tool_call_id=tool_call_id,
                name=tool_name,
            )

        # respond：把用户补充说明回给模型
        message = record.get("message") or "（用户未填写补充说明）"
        return ToolMessage(
            content=(
                f"用户未批准本次「{tool_name}」操作，并补充说明：{message}\n"
                "请按说明调整方案后再试，不要重复提交相同的调用。"
            ),
            tool_call_id=tool_call_id,
            name=tool_name,
        )

    # ---- DB ----

    async def _find_decided(self, run_id: str, tool_call_id: str) -> dict[str, Any] | None:
        if not run_id or not tool_call_id:
            return None
        try:
            from sqlalchemy import select

            from app.db.models import AgentInterrupt
            from app.db.session import session_scope

            async with session_scope() as db:
                rows = (
                    await db.execute(
                        select(AgentInterrupt).where(
                            AgentInterrupt.run_id == run_id,
                            AgentInterrupt.type == "approval",
                            AgentInterrupt.decision.is_not(None),
                        )
                    )
                ).scalars().all()
                for row in rows:
                    payload = _loads(row.payload_json)
                    if payload.get("tool_call_id") == tool_call_id:
                        return _interrupt_record(row, payload)
        except Exception as exc:  # pragma: no cover
            logger.warning("interrupt_lookup_failed", run_id=run_id, error=str(exc))
        return None

    async def _create_interrupt(self, context: Any, payload: dict[str, Any]) -> str:
        """幂等创建中断行：同 (run_id, tool_call_id) 未决行直接复用。"""

        run_id = getattr(context, "run_id", "") or ""
        session_id = getattr(context, "session_id", "") or ""
        try:
            from sqlalchemy import select

            from app.db.models import AgentInterrupt
            from app.db.session import session_scope

            async with session_scope() as db:
                existing = (
                    await db.execute(
                        select(AgentInterrupt).where(
                            AgentInterrupt.run_id == run_id,
                            AgentInterrupt.type == "approval",
                            AgentInterrupt.decision.is_(None),
                        )
                    )
                ).scalars().all()
                for row in existing:
                    if _loads(row.payload_json).get("tool_call_id") == payload.get("tool_call_id"):
                        return row.id

                interrupt_id = new_id()
                db.add(
                    AgentInterrupt(
                        id=interrupt_id,
                        run_id=run_id,
                        session_id=session_id,
                        type="approval",
                        tool_name=payload.get("tool_name"),
                        payload_json=json.dumps(payload, ensure_ascii=False, default=str),
                        created_at=utcnow(),
                    )
                )
                return interrupt_id
        except Exception as exc:  # pragma: no cover - DB 失败时仍继续中断（内存态可恢复）
            logger.warning("interrupt_create_failed", run_id=run_id, error=str(exc))
            return new_id()

    async def _record_decision(
        self, run_id: str, tool_call_id: str, interrupt_id: str, decision: Any
    ) -> dict[str, Any]:
        """把 resume 值落库（resolve API 通常已写，此处兜底幂等）。"""
        record = _normalize_decision(decision)
        try:
            from app.db.base import utcnow
            from app.db.models import AgentInterrupt
            from app.db.session import session_scope

            async with session_scope() as db:
                row = await db.get(AgentInterrupt, interrupt_id)
                if row is not None and row.decision is None:
                    row.decision = record["decision"]
                    row.response_json = json.dumps(record, ensure_ascii=False, default=str)
                    row.decided_by = "user"
                    row.decided_at = utcnow()
        except Exception as exc:  # pragma: no cover
            logger.warning("interrupt_record_failed", run_id=run_id, error=str(exc))
        record["tool_call_id"] = tool_call_id
        return record

    async def _set_run_status(self, context: Any, status: str) -> None:
        run_id = getattr(context, "run_id", "") or ""
        if not run_id:
            return
        try:
            from app.db.models import AgentRun
            from app.db.session import session_scope

            async with session_scope() as db:
                row = await db.get(AgentRun, run_id)
                if row is not None and row.status in ("running", "pending"):
                    row.status = status
        except Exception:  # pragma: no cover
            pass


# ---- 决策归一化 ----


def _normalize_decision(decision: Any) -> dict[str, Any]:
    """把 resume 值统一成 `{decision, edited_args?, message?}`。

    兼容三种形状：
    - langchain HITL batched：`{"decisions": [{"type": ..., ...}]}` → 取第一条
    - 单条：`{"type": ...}` 或 `{"decision": ...}`
    - 裸字符串：`"approve"`
    """
    if isinstance(decision, str):
        return {"decision": decision}
    if not isinstance(decision, dict):
        return {"decision": "approve"}

    # 解包 batched 形状
    decisions = decision.get("decisions")
    if isinstance(decisions, list) and decisions and isinstance(decisions[0], dict):
        decision = decisions[0]

    kind = decision.get("type") or decision.get("decision") or "approve"
    record: dict[str, Any] = {"decision": kind}

    edited_args = decision.get("edited_args")
    edited_action = decision.get("edited_action")
    if edited_args is not None:
        record["edited_args"] = edited_args
    elif isinstance(edited_action, dict) and edited_action.get("args") is not None:
        record["edited_args"] = edited_action["args"]
    if decision.get("message") is not None:
        record["message"] = str(decision["message"])
    return record


def _interrupt_record(row: Any, payload: dict[str, Any]) -> dict[str, Any]:
    response = _loads(row.response_json)
    record = {
        "decision": row.decision,
        "reason": payload.get("reason"),
        "tool_call_id": payload.get("tool_call_id"),
    }
    if isinstance(response, dict):
        if response.get("edited_args") is not None:
            record["edited_args"] = response["edited_args"]
        if response.get("message") is not None:
            record["message"] = response["message"]
    return record


def _loads(raw: str | None) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        value = json.loads(raw)
    except ValueError:
        return {}
    return value if isinstance(value, dict) else {}


def _truncate_args(args: dict[str, Any], limit: int = 2000) -> dict[str, Any]:
    """审批卡片展示用：长文本截断，避免事件过大。"""
    out: dict[str, Any] = {}
    for key, value in args.items():
        if isinstance(value, str) and len(value) > limit:
            out[key] = value[:limit] + f"…（已截断，原长 {len(value)} 字符）"
        else:
            out[key] = value
    return out
