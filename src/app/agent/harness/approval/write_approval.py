# -*- coding: utf-8 -*-
"""
模块: app.agent.harness.approval.write_approval
文件名: write_approval.py
作者: xionggaoqing
时间: 2026-11-09
说明: `WriteApprovalMiddleware`：按**参数**判定高危写入 → `langgraph.types.interrupt()` 转人工审批。

    手册「二、(2) 人为介入」第 2 层：`interrupt_on` 只能做工具级开关、无法按参数判定，
    故高危写入由本中间件在 `awrap_tool_call` 内触发中断。

    ★ 高危判定**只有删除卷章**（`classify_risk`）：拦截 `delete_volume` / `delete_chapter`
    两个结构工具。本应用是 AI 写小说，编辑大纲 / 生成卷章正文 / 整章覆盖都属常规操作，
    一律不审批。「整章覆盖会触发审批」是**从未实现过的承诺**，相关阈值参数已于 2026-11 移除；
    AI 改稿的安全网是 `volumes.record_agent_write` 落的 `source="agent"` 修订快照
    （可在 UI 回退），不是事前审批。

    另有两处需要人为介入，但**不归本中间件**：
    - 读大纲全文 → `guard/read_scope.py`（避免一次性注入过多上下文）；
    - 调用预算超限 → `llm/call_budget.py`。

    ★ 路径判定必须先过 `normalize_virtual_path`：框架 `validate_path` 是
    「先归一再执行」，按字面入参匹配前缀会被 `projects/...`（相对路径）、
    `\\projects\\...`（反斜杠）绕过，等于把唯一的删除闸门打开。

    恢复语义（`decision`）：
    - `approve` → 原样执行；
    - `reject` → 不执行，向模型返回「用户拒绝」ToolMessage；
    - `respond` → 不执行，把用户补充说明 `message` 回给模型。
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from langchain.agents.middleware.types import AgentMiddleware
from langchain_core.messages import ToolMessage
from langgraph.types import interrupt

from app.agent.harness.sandbox.virtual_path import normalize_virtual_path
from app.core.logging import get_logger
from app.core.paths import workspaces_root
from app.db.base import new_id, utcnow

logger = get_logger(__name__)

#: 中断标记（runner 据此区分自定义中断与 langchain HITL 批量中断）
INTERRUPT_KIND = "writer_approval"


def virtual_to_host(virtual_path: str) -> Path | None:
    """`/projects/{pid}/...` → 宿主路径（仅用于**只读**探测，不做写入）。"""
    p = normalize_virtual_path(virtual_path)
    if not p.startswith("/projects/"):
        return None
    rel = p[len("/projects/") :]
    if not rel or ".." in rel.split("/"):
        return None
    return workspaces_root() / rel


_CN_DIGITS = "一二三四五六七八九"


def _cn_num(n: int) -> str:
    """1-99 → 中文数字（超出回落阿拉伯数字）。"""
    if 1 <= n <= 9:
        return _CN_DIGITS[n - 1]
    if 10 <= n <= 99:
        tens, ones = divmod(n, 10)
        head = "" if tens == 1 else _CN_DIGITS[tens - 1]
        return f"{head}十{_CN_DIGITS[ones - 1] if ones else ''}"
    return str(n)


def _project_of(virtual_path: str) -> str | None:
    """`/projects/{pid}/...` → pid；非项目路径返回 None。"""
    p = (virtual_path or "").replace("\\", "/").strip()
    if not p.startswith("/projects/"):
        return None
    rel = p[len("/projects/") :]
    return rel.split("/", 1)[0] if "/" in rel else None


def _rel_of(virtual_path: str) -> str:
    p = (virtual_path or "").replace("\\", "/").strip()
    rel = p[len("/projects/") :] if p.startswith("/projects/") else p
    return rel.split("/", 1)[1] if "/" in rel else rel


def _vol_title(project_id: str, vol_id: str) -> str:
    """卷名（volumes.yaml 事实源）；读不到返回空串。"""
    try:
        from app.services import files

        vol = files.find_volume(files.read_volumes(project_id), vol_id)
        return str((vol or {}).get("title") or "")
    except Exception:  # pragma: no cover - 工作区缺失等异常不阻断审批文案
        return ""


def _ch_title(project_id: str, vol_id: str, ch_id: str) -> str:
    """章名（章节 frontmatter 事实源）；读不到返回空串。"""
    try:
        from app.services import files

        return str(files.read_chapter_meta(project_id, vol_id, ch_id).get("title") or "")
    except Exception:  # pragma: no cover
        return ""


def _humanize_rel(project_id: str | None, rel: str) -> str:
    """项目内相对路径 → 人话（带真实序号与卷章名）。

    目录名是**短 ID**（如 `v1`/`c10`）而非序号，故序号一律取
    `volumes.yaml` 的 `sort_order` 与章节 frontmatter 的 `sort_order`；
    名称取各自 title。任一项读不到时省略该部分，绝不编造。
    """
    m = re.match(r"^chapters/v([^/]+)/c([^/]+)\.md$", rel)
    if m:
        vol_id, ch_id = m.group(1), m.group(2)
        if project_id is None:
            return "某一章的正文"
        vol_ord = _sort_order_of_volume(project_id, vol_id)
        ch_ord, ch_title = _chapter_facts(project_id, vol_id, ch_id)
        vol_part = f"第{_cn_num(vol_ord)}卷" if vol_ord else ""
        ch_part = f"第{_cn_num(ch_ord)}章" if ch_ord else "某一章"
        title_part = f"《{ch_title}》" if ch_title else ""
        return f"{vol_part}{ch_part}{title_part}的正文"
    m = re.match(r"^chapters/v([^/]+)/?$", rel)
    if m:
        if project_id is None:
            return "某一卷的全部章节"
        vol_id = m.group(1)
        vol_ord = _sort_order_of_volume(project_id, vol_id)
        vol_title = _vol_title(project_id, vol_id)
        head = f"第{_cn_num(vol_ord)}卷" if vol_ord else "某一卷"
        title_part = f"《{vol_title}》" if vol_title else ""
        return f"{head}{title_part}的全部章节"
    if rel.startswith("outline/"):
        return "小说大纲"
    if rel.startswith("summaries/"):
        return "章节摘要"
    if rel.endswith("volumes.yaml"):
        return "卷目录"
    if rel.endswith("novel.yaml"):
        return "书籍信息"
    return rel.rsplit("/", 1)[-1] or rel


def _sort_order_of_volume(project_id: str, vol_id: str) -> int:
    try:
        from app.services import files

        vol = files.find_volume(files.read_volumes(project_id), vol_id)
        return int((vol or {}).get("sort_order") or 0)
    except Exception:  # pragma: no cover
        return 0


def _chapter_facts(project_id: str, vol_id: str, ch_id: str) -> tuple[int, str]:
    """(sort_order, title)；读不到返回 (0, "")。"""
    try:
        from app.services import files

        meta = files.read_chapter_meta(project_id, vol_id, ch_id)
        return int(meta.get("sort_order") or 0), str(meta.get("title") or "")
    except Exception:  # pragma: no cover
        return 0, ""


def humanize_virtual_path(virtual_path: str) -> str:
    """把内部虚拟路径转成人话（审批卡/提示文案用，不暴露文件名与目录结构）。

    - `chapters/v2/c3.md` → 「第二卷第三章《章名》的正文」（序号/名称取事实源）
    - `chapters/v2/`（目录）→ 「第二卷《卷名》的全部章节」
    - `outline/*.md` → 「小说大纲」；`summaries/*` → 「章节摘要」
    - `volumes.yaml` → 「卷目录」；`novel.yaml` → 「书籍信息」
    - 其余 → 末段文件名
    """
    return _humanize_rel(_project_of(virtual_path), _rel_of(virtual_path))


def humanize_delete_reason(virtual_path: str) -> str:
    """删除类审批的一句话人话文案：「删除第二卷第三章《章名》的正文，删除后不可恢复」。"""
    return f"删除{humanize_virtual_path(virtual_path)}，删除后不可恢复"


def classify_risk(project_id: str, tool_name: str, args: dict[str, Any]) -> tuple[str | None, str]:
    """判定是否高危。**只有删除卷章**（`delete_volume` / `delete_chapter`）。

    本应用是 AI 写小说，编辑大纲 / 生成卷章正文 / 整章覆盖都属常规操作，
    **一律不审批**；仅当 Agent 调用 `delete_volume` / `delete_chapter` 结构工具时
    提示风险操作，由用户三选一：批准 / 拒绝 / 补充要求。

    卷/章的标识从工具参数里取（`volume` / `chapter`），用于生成人话文案。
    优先用事实源标题（volumes.yaml / frontmatter），ID 解析失败时回退为原始值。

    Returns:
        (risk_kind, reason)；`risk_kind` 为 None 表示放行。
    """
    if tool_name == "delete_volume":
        volume = str(args.get("volume") or "").strip()
        vol_title = _vol_title(project_id, volume) if volume and project_id else volume
        display = vol_title or volume or "某一卷"
        reason = f"删除卷「{display}」及其下全部章节，删除后不可恢复"
        return "delete", reason

    if tool_name == "delete_chapter":
        chapter = str(args.get("chapter") or "").strip()
        volume = str(args.get("volume") or "").strip()
        vol_title = _vol_title(project_id, volume) if volume and project_id else volume
        ch_title = _ch_title(project_id, volume, chapter) if chapter and volume and project_id else chapter
        vol_display = vol_title or volume
        ch_display = ch_title or chapter
        if vol_display:
            reason = f"删除卷「{vol_display}」中的章「{ch_display}」，删除后不可恢复" if ch_display else f"删除卷「{vol_display}」中的某一章，删除后不可恢复"
        else:
            reason = f"删除章「{ch_display}」，删除后不可恢复" if ch_display else "删除章节，删除后不可恢复"
        return "delete", reason

    return None, ""


class WriteApprovalMiddleware(AgentMiddleware):
    """参数级高危写入审批。"""

    name = "WriteApproval"

    def __init__(
        self,
        *,
        enabled: bool = True,
        allowed_decisions: tuple[str, ...] = ("approve", "reject", "respond"),
        skip_tools: tuple[str, ...] = (),
    ) -> None:
        super().__init__()
        self.enabled = enabled
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

        context = getattr(request.runtime, "context", None)
        project_id = str(getattr(context, "project_id", "") or "")

        risk_kind, reason = classify_risk(project_id, tool_name, args)
        if risk_kind is None:
            return await handler(request)

        run_id = getattr(context, "run_id", "") or ""

        # 已有决定（中断恢复重放）→ 直接执行，不重复中断
        existing = await self._find_decided(run_id, tool_call_id)
        if existing is not None:
            return await self._apply(existing, request, handler, tool_name, args)

        # 结构工具（delete_volume/delete_chapter）没有虚拟路径参数，用工具名+参数构造人话
        path = normalize_virtual_path(str(args.get("file_path") or args.get("path") or ""))
        item_label = self._humanize_structure_tool(project_id, tool_name, args) if not path else humanize_virtual_path(path)

        payload = {
            "kind": INTERRUPT_KIND,
            "risk": risk_kind,
            "reason": reason,
            "tool_name": tool_name,
            "args": _truncate_args(args),
            "tool_call_id": tool_call_id,
            "decisions": self.allowed_decisions,
            # 批量卡片用：归一化路径（结构工具为空）+ 单条人话标签
            "path": path,
            "item_label": item_label,
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
                    # 同类批量审批卡片：单条人话标签 + 原始路径（前端分组展示用）
                    "item_label": payload.get("item_label"),
                    "path": payload.get("path"),
                },
            )
        await self._set_run_status(context, "awaiting_approval")

        # 挂起：resume 时本方法会重跑，interrupt() 直接返回决定
        decision = interrupt(payload)

        record = await self._record_decision(run_id, tool_call_id, interrupt_id, decision)
        return await self._apply(record, request, handler, tool_name, args)

    def _resolve_vol_title(self, project_id: str, volume: str) -> str:
        """把卷 id 或卷名解析为卷名（优先用事实源标题）。"""
        if not volume:
            return ""
        # 先尝试作为 ID 解析
        title = _vol_title(project_id, volume)
        if title:
            return title
        # 否则当作卷名直接使用
        return volume

    def _resolve_ch_title(self, project_id: str, vol_id: str, chapter: str) -> str:
        """把章 id 或章名解析为章名（优先用事实源标题）。"""
        if not chapter:
            return ""
        # 先尝试作为 ID 解析（需要卷 ID）
        if vol_id:
            title = _ch_title(project_id, vol_id, chapter)
            if title:
                return title
        # 否则当作章名直接使用
        return chapter

    def _humanize_structure_tool(self, project_id: str, tool_name: str, args: dict[str, Any]) -> str:
        """结构工具的人话标签（审批卡展示用）。"""
        if tool_name == "delete_volume":
            volume = str(args.get("volume") or "").strip()
            vol_title = self._resolve_vol_title(project_id, volume)
            return f"卷「{vol_title}」" if vol_title else "某一卷"
        if tool_name == "delete_chapter":
            chapter = str(args.get("chapter") or "").strip()
            volume = str(args.get("volume") or "").strip()
            # 先解析卷，获取卷 ID 用于解析章
            vol_title = self._resolve_vol_title(project_id, volume)
            ch_title = self._resolve_ch_title(project_id, volume, chapter)
            if vol_title:
                return f"卷「{vol_title}」中的章「{ch_title}」" if ch_title else f"卷「{vol_title}」中的某一章"
            return f"章「{ch_title}」" if ch_title else "某一章"
        return tool_name

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
        return await find_decided(run_id, tool_call_id)

    async def _create_interrupt(self, context: Any, payload: dict[str, Any]) -> str:
        return await create_interrupt(context, payload)

    async def _record_decision(
        self, run_id: str, tool_call_id: str, interrupt_id: str, decision: Any
    ) -> dict[str, Any]:
        return await record_decision(run_id, tool_call_id, interrupt_id, decision)

    async def _set_run_status(self, context: Any, status: str) -> None:
        await set_run_status(context, status)


async def find_decided(run_id: str, tool_call_id: str) -> dict[str, Any] | None:
    """查本 run 内该 tool_call 已有决定（中断恢复重放时直接执行，不重复中断）。"""
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


async def create_interrupt(context: Any, payload: dict[str, Any]) -> str:
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


async def record_decision(
    run_id: str, tool_call_id: str, interrupt_id: str, decision: Any
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


async def set_run_status(context: Any, status: str) -> None:
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
