# -*- coding: utf-8 -*-
"""
模块: app.agent.harness.llm.call_budget
文件名: call_budget.py
作者: xionggaoqing
时间: 2026-11-06
说明: `ModelCallBudgetMiddleware`：单次任务（run / 子代理一次委派）的模型调用预算。

    取代 langchain `ModelCallLimitMiddleware` 的「静默 end」收尾：
    - 计数口径 = state 里的 AIMessage 条数（跨 resume 不丢，checkpoint 持久化）；
    - 即将超限（下一次调用会超出预算）时**先总结已完成的思考/处理结果**
      （独立一次摘要调用，不占预算），再经 consult 中断弹窗询问用户：
      「继续思考」（追加预算续跑）/「结束并输出总结」/自由文本补充指示；
    - 结束 → 直接把总结作为最终 AIMessage 返回，run 正常收尾；
    - 子代理同机制（isolated 图内 interrupt 会冒泡到主图，前端同一张咨询卡片）。

    咨询落库/发射复用 consult 契约（agent_interrupts.type=consult +
    SSE `consult.request`），resolve 后经 `runner.resume` 续跑同一 run。
"""

from __future__ import annotations

import json
from typing import Any

from langchain.agents.middleware.types import AgentMiddleware, ModelRequest, ModelResponse
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langgraph.types import interrupt

from app.core.logging import get_logger

logger = get_logger(__name__)

#: 单 run 最多弹几次预算咨询（防反复打扰；超出后强制总结收尾）
#: 3 → 10：调用上限从 60 翻倍到 120，咨询次数同步放宽；
#: 每次「继续思考」追加 20 次预算，10 次咨询 = 最多 200 次调用仍可控。
MAX_BUDGET_CONSULTS = 10
#: 每次「继续思考」追加的调用预算
CONTINUE_GRANT = 20
#: 送进摘要模型的历史渲染上限（字符）
_SUMMARY_RENDER_CAP = 24_000
#: 摘要取最近的消息条数
_SUMMARY_TAIL = 12

_SUMMARY_PROMPT = (
    "你是任务进度总结助手。下面是本次任务最近的思考与处理记录，请输出一份简洁的中文阶段总结："
    "① 已完成的事项与结论（含已写入/修改的文件）；② 当前进展到哪一步；"
    "③ 尚未完成的事项与建议的下一步。只输出总结本身，不要多余解释。"
)


class ModelCallBudgetMiddleware(AgentMiddleware):
    """模型调用预算：超限前先总结，再 consult 询问继续/结束。"""

    name = "ModelCallBudget"

    def __init__(
        self,
        *,
        limit: int,
        summary_model: Any = None,
        steer_sink: Any = None,
    ) -> None:
        """`limit`：本实例（一次 run 或一次子代理委派）的调用预算。

        `summary_model`：构建阶段总结用的模型（None 时回落 request.model）。
        `steer_sink`：`(run_id, text) -> None`，用户自由文本指示转入引导队列。
        """
        super().__init__()
        self._limit = max(1, int(limit))
        self._summary_model = summary_model
        self._steer_sink = steer_sink

    # ---- 计数 ----

    @staticmethod
    def _call_count(messages: list[Any]) -> int:
        """本次任务（run）已发生的模型调用次数。

        口径 = 当前 run 起点（最后一条「普通用户消息」：非 steer、非系统守护 nudge）
        之后的 AIMessage 条数。checkpoint 按会话线程持久化，必须按 run 起点切片，
        否则历史 run 的调用会吃掉本次预算；resume 不新增用户消息，切片稳定。
        """
        start = 0
        for i, m in enumerate(messages):
            if (
                isinstance(m, HumanMessage)
                and getattr(m, "name", None) is None
                and not str(getattr(m, "content", "")).startswith("（系统守护）")
            ):
                start = i
        return sum(1 for m in messages[start:] if isinstance(m, AIMessage))

    # ---- 主流程 ----

    async def awrap_model_call(self, request: ModelRequest, handler: Any) -> ModelResponse:
        state = getattr(request, "state", None)
        state_msgs = state.get("messages") if isinstance(state, dict) else None
        messages = list(state_msgs) if state_msgs else list(request.messages)
        count = self._call_count(messages)
        if count + 1 <= self._limit:
            return await handler(request)

        context = getattr(request.runtime, "context", None)
        run_id = str(getattr(context, "run_id", "") or "")

        # 咨询次数封顶 / 无 run 上下文（子代理未继承 context）→ 直接总结收尾，不弹窗
        prior = await self._count_rows(run_id)
        if prior >= MAX_BUDGET_CONSULTS or not run_id:
            summary = await self._summarize(request, messages)
            logger.warning(
                "model_call_budget_forced_end",
                run_id=run_id,
                count=count,
                limit=self._limit,
                consults=prior,
            )
            return ModelResponse(
                result=[
                    AIMessage(
                        content=(
                            f"{summary}\n\n（模型调用已达本次任务预算上限 {self._limit} 次，"
                            "本次思考处理结束；以上为阶段总结。）"
                        )
                    )
                ]
            )

        # resume 重放判定：同 count 的已决咨询行存在 → 直接应用其决定（不重复弹窗）。
        # count 在一次执行内确定、重放不变，且两次不同超限事件的 count 必然不同，
        # 故可无歧义区分「重放」与「新一轮超限」。
        decision = await self._find_decision(run_id, count)
        if decision is None:
            summary = await self._summarize(request, messages)
            decision = await self._ask(context, run_id, count, summary)
        choice = str((decision or {}).get("choice") or "")
        text = str((decision or {}).get("text") or "")

        if choice == "end":
            summary = str((decision or {}).get("summary") or "") or await self._summarize(
                request, messages
            )
            logger.warning("model_call_budget_ended", run_id=run_id, count=count, limit=self._limit)
            return ModelResponse(
                result=[
                    AIMessage(
                        content=(
                            f"{summary}\n\n（模型调用已达本次任务预算上限 {self._limit} 次，"
                            "按你的选择结束思考处理；以上为阶段总结。）"
                        )
                    )
                ]
            )

        # continue（含自由文本指示）：追加预算；文本转引导队列下次调用可见
        self._limit += CONTINUE_GRANT
        if text and self._steer_sink is not None and run_id:
            try:
                self._steer_sink(run_id, f"（用户补充指示）{text}")
            except Exception as exc:  # pragma: no cover
                logger.warning("budget_steer_failed", error=str(exc))
        logger.info("model_call_budget_extended", run_id=run_id, count=count, new_limit=self._limit)
        return await handler(request)

    # ---- 总结 ----

    async def _summarize(self, request: ModelRequest, messages: list[Any]) -> str:
        tail = messages[-_SUMMARY_TAIL:]
        rendered: list[str] = []
        total = 0
        for m in tail:
            content = getattr(m, "content", "")
            if isinstance(content, list):
                content = " ".join(
                    str(p.get("text", "")) for p in content if isinstance(p, dict)
                )
            line = f"{getattr(m, 'type', 'message')}: {str(content)[:1500]}"
            if total + len(line) > _SUMMARY_RENDER_CAP:
                break
            rendered.append(line)
            total += len(line)
        model = self._summary_model or request.model
        try:
            reply = await model.ainvoke(
                [
                    SystemMessage(content=_SUMMARY_PROMPT),
                    HumanMessage(content="\n".join(rendered) or "（无历史记录）"),
                ]
            )
            content = getattr(reply, "content", "")
            if isinstance(content, list):
                content = " ".join(str(p.get("text", "")) for p in content if isinstance(p, dict))
            return str(content).strip() or "（总结生成失败）"
        except Exception as exc:  # 摘要失败不阻断收尾
            logger.warning("budget_summarize_failed", error=str(exc))
            return "（阶段总结生成失败；调用预算已用尽。）"

    # ---- consult 中断 ----

    async def _ask(self, context: Any, run_id: str, count: int, summary: str) -> dict[str, Any]:
        payload = {
            "kind": "call_budget",
            "consult_kind": "call_budget",
            "seq": count,  # = 超限时的调用计数（重放不变，可作幂等键）
            "question": (
                f"模型调用即将达到本次任务的预算上限（已 {count} 次 / 上限 {self._limit} 次）。\n"
                f"【阶段总结】\n{summary}\n\n是否继续思考处理？"
            ),
            "options": ["继续思考", "结束并输出总结"],
            "allow_text": True,
            "summary": summary,
        }
        interrupt_id = await self._create_interrupt(context, payload)
        emit = getattr(context, "emit", None)
        if emit is not None:
            await emit.publish(
                "consult.request",
                {
                    "interrupt_id": interrupt_id,
                    "question": payload["question"],
                    "options": payload["options"],
                    "allow_text": True,
                    "workflow_stage": getattr(context, "workflow_stage", "") or None,
                },
            )
        await self._set_run_status(context, "awaiting_consult")
        decision = interrupt(payload)
        await self._record_decision(run_id, interrupt_id, decision)
        return self._normalize(decision, summary)

    @staticmethod
    def _normalize(decision: Any, summary: str) -> dict[str, Any]:
        if isinstance(decision, dict):
            choice = decision.get("choice") or decision.get("decision") or ""
            text = decision.get("text") or ""
        else:
            choice, text = str(decision or ""), ""
        choice = str(choice)
        if choice not in ("continue", "end"):
            choice = "end" if ("结束" in choice or choice == "end") else "continue"
        return {"choice": choice, "text": str(text or ""), "summary": summary}

    # ---- DB（consult 契约复用） ----

    async def _find_decision(self, run_id: str, count: int) -> dict[str, Any] | None:
        """取本 run 内「超限计数=count」的预算咨询决定（resume 重放匹配）。"""
        if not run_id:
            return None
        try:
            from sqlalchemy import select

            from app.db.models import AgentInterrupt
            from app.db.session import session_scope

            async with session_scope() as db:
                rows = (
                    await db.execute(
                        select(AgentInterrupt).where(
                            AgentInterrupt.run_id == run_id, AgentInterrupt.type == "consult"
                        )
                    )
                ).scalars().all()
            for row in rows:
                payload = json.loads(row.payload_json or "{}")
                if payload.get("kind") != "call_budget" or int(payload.get("seq") or -1) != count:
                    continue
                if row.decision is None:
                    return None  # 未决（不应出现在重放路径）
                resp = json.loads(row.response_json or "{}")
                return self._normalize(resp, str(payload.get("summary") or ""))
        except Exception as exc:  # pragma: no cover
            logger.warning("budget_decision_lookup_failed", run_id=run_id, error=str(exc))
        return None

    async def _count_rows(self, run_id: str) -> int:
        """本 run 已发起的预算咨询次数（含未决行，用于封顶）。"""
        if not run_id:
            return 0
        try:
            from sqlalchemy import select

            from app.db.models import AgentInterrupt
            from app.db.session import session_scope

            async with session_scope() as db:
                rows = (
                    await db.execute(
                        select(AgentInterrupt.payload_json).where(
                            AgentInterrupt.run_id == run_id, AgentInterrupt.type == "consult"
                        )
                    )
                ).scalars().all()
            return sum(
                1
                for raw in rows
                if json.loads(raw or "{}").get("kind") == "call_budget"
            )
        except Exception as exc:  # pragma: no cover
            logger.warning("budget_row_count_failed", run_id=run_id, error=str(exc))
            return 0

    async def _create_interrupt(self, context: Any, payload: dict[str, Any]) -> str:
        run_id = str(getattr(context, "run_id", "") or "")
        session_id = str(getattr(context, "session_id", "") or "")
        try:
            from sqlalchemy import select

            from app.db.base import new_id, utcnow
            from app.db.models import AgentInterrupt
            from app.db.session import session_scope

            async with session_scope() as db:
                existing = (
                    await db.execute(
                        select(AgentInterrupt).where(
                            AgentInterrupt.run_id == run_id,
                            AgentInterrupt.type == "consult",
                            AgentInterrupt.decision.is_(None),
                        )
                    )
                ).scalars().all()
                for row in existing:
                    if json.loads(row.payload_json or "{}").get("kind") == "call_budget":
                        return row.id
                interrupt_id = new_id()
                db.add(
                    AgentInterrupt(
                        id=interrupt_id,
                        run_id=run_id,
                        session_id=session_id,
                        type="consult",
                        tool_name=None,
                        payload_json=json.dumps(payload, ensure_ascii=False, default=str)[:20000],
                        created_at=utcnow(),
                    )
                )
                return interrupt_id
        except Exception as exc:  # pragma: no cover
            logger.warning("budget_interrupt_create_failed", error=str(exc))
            from app.db.base import new_id

            return new_id()

    async def _record_decision(self, run_id: str, interrupt_id: str, decision: Any) -> None:
        try:
            from app.db.base import utcnow
            from app.db.models import AgentInterrupt
            from app.db.session import session_scope

            record = decision if isinstance(decision, dict) else {"choice": decision}
            async with session_scope() as db:
                row = await db.get(AgentInterrupt, interrupt_id)
                if row is not None and row.decision is None:
                    row.decision = str(record.get("choice") or record.get("decision") or "end")
                    row.response_json = json.dumps(record, ensure_ascii=False, default=str)
                    row.decided_at = utcnow()
        except Exception as exc:  # pragma: no cover
            logger.warning("budget_decision_record_failed", error=str(exc))

    async def _set_run_status(self, context: Any, status: str) -> None:
        run_id = str(getattr(context, "run_id", "") or "")
        session_id = str(getattr(context, "session_id", "") or "")
        if not run_id:
            return
        try:
            from app.db.models import AgentRun, AgentSession
            from app.db.session import session_scope

            async with session_scope() as db:
                run = await db.get(AgentRun, run_id)
                if run is not None:
                    run.status = status
                sess = await db.get(AgentSession, session_id)
                if sess is not None:
                    sess.status = status
        except Exception as exc:  # pragma: no cover
            logger.warning("budget_run_status_failed", error=str(exc))
