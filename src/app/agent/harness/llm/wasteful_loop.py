# -*- coding: utf-8 -*-
"""
模块: app.agent.harness.llm.wasteful_loop
文件名: wasteful_loop.py
作者: xionggaoqing
时间: 2026-11-05
说明: `WastefulLoopGuardMiddleware`：浪费型循环**分级熔断**（token 治理）。

    背景（2026-11-05 实测 run：718K 输入 token）：模型对同一附件反复全量重读、
    写坏后「重读→重写→再否定」死循环，历史只增不减，token 平方级膨胀。

    三级处置（针对「同一路径反复 read / 同一文件反复 write|edit」）：
    1. 达到 SOFT 阈值（3 次）→ 经 steer 队列注入纠正提醒（**每 run 每路径只推一次**，
       避免重复提醒刷屏）；
    2. 达到 HARD 阈值（5 次）→ 注入**强制换方向**熔断指令：禁止再碰该路径，
       必须改用其它手段（offset/limit 片段、outline_grep、派发子代理、直接给结论）；
    3. 工具调用层硬阻断（awrap_tool_call）：同一路径调用次数 ≥ HARD 时**不执行**，
       直接返回 ToolMessage 说明已熔断——模型只能换方向，物理上无法继续循环。

    纠正消息经 `run_manager.push_steer` 入队，由 `SteerInjectionMiddleware`
    在下一次模型调用前追加为 HumanMessage——与「运行中引导」同通道，
    不新增 state 消息类型。
"""

from __future__ import annotations

from typing import Any

from langchain.agents.middleware.types import AgentMiddleware
from langchain_core.messages import ToolMessage

from app.core.logging import get_logger

logger = get_logger(__name__)

#: 一级提醒阈值（正常分段读每段只读一次 / 正常重构每文件写 1~2 次）
READ_SOFT_LIMIT = 3
WRITE_SOFT_LIMIT = 3
#: 二级强制换方向阈值（同时是工具层硬阻断阈值）
READ_HARD_LIMIT = 5
WRITE_HARD_LIMIT = 5


def _arg_path(args: Any) -> str:
    if not isinstance(args, dict):
        return ""
    return str(args.get("file_path") or args.get("path") or "")


def _call_key(name: str, args: Any) -> str:
    """工具调用指纹：同类型（读/写）+同主参数（路径/模式）视为「同一件事」。

    write_file 与 edit_file 归为同一写类——「写坏→edit→再整篇写」同样是循环。
    """
    if not isinstance(args, dict):
        return ""
    path = _arg_path(args)
    pattern = str(args.get("pattern") or args.get("query") or "")
    target = path or pattern
    if not target:
        return ""
    kind = "read" if name == "read_file" else "write"
    return f"{kind}::{target}"


class WastefulLoopGuardMiddleware(AgentMiddleware):
    """检测「反复重读同一路径 / 反复重写同一文件」：提醒 → 强制换方向 → 硬阻断。"""

    name = "WastefulLoopGuard"

    def __init__(self, steer_sink: Any = None) -> None:
        """`steer_sink`：`(run_id, text) -> None`，通常为 `run_manager.push_steer`。"""
        super().__init__()
        self._steer_sink = steer_sink
        #: run 级已推送签名（防同一纠正重复刷屏）：{run_id: set(sig)}
        self._pushed: dict[str, set[str]] = {}
        #: run 级统计基线：{run_id: 该 run 开始时的消息条数}，见 `_scoped_messages`
        self._baseline: dict[str, int] = {}

    # ---- 统计范围 ----

    #: `_pushed` / `_baseline` 按 run_id 累积，进程长命会无界增长；只保留最近若干个 run
    _MAX_TRACKED_RUNS = 64

    def _scoped_messages(self, messages: list[Any], run_id: str) -> list[Any]:
        """把统计范围收窄到「本轮 run 期间新产生的消息」。

        ★ 必须收窄：graph 带 checkpointer 且 thread_id 按会话续接，
        `state["messages"]` 会累积**同一会话历次 run** 的全部消息。按全量统计时，
        长会话里新一轮 run 一开头就可能已达 `READ_HARD_LIMIT`，于是 Agent 本轮
        **第一次**读某文件就被 `awrap_tool_call` 直接阻断（回「已达 N 次，本次被
        阻断、未执行」），而那 N 次全是上一轮读的。表现为 AI 莫名拒绝读文件、
        绕路、答非所问，且用户无从察觉原因。

        基线取「首次见到该 run_id 时的消息条数」——`abefore_model` 每轮 run 的第一次
        调用发生在任何工具调用之前，此时消息全是历史，正好作为切分点。
        摘要/裁剪可能让消息数变短，此时重置基线（宁可少计，不可误阻断）。
        """
        if not run_id:
            return messages
        base = self._baseline.get(run_id)
        if base is None or base > len(messages):
            base = len(messages)
            self._baseline[run_id] = base
            self._forget_old_runs(run_id)
        return messages[base:]

    def _forget_old_runs(self, current_run_id: str) -> None:
        """超出 `_MAX_TRACKED_RUNS` 时丢弃最早登记的 run（dict 保持插入序）。"""
        for store in (self._baseline, self._pushed):
            while len(store) > self._MAX_TRACKED_RUNS:
                oldest = next(iter(store))
                if oldest == current_run_id:
                    break
                store.pop(oldest, None)

    # ---- 统计 ----

    @staticmethod
    def count_repeats(messages: list[Any]) -> dict[str, int]:
        """统计 state 消息里的工具调用指纹次数（read / write 两类，按路径聚合）。"""
        counts: dict[str, int] = {}
        for msg in messages:
            calls = getattr(msg, "tool_calls", None) or []
            for tc in calls:
                if not isinstance(tc, dict):
                    continue
                name = tc.get("name") or ""
                if name not in ("read_file", "write_file", "edit_file"):
                    continue
                key = _call_key(name, tc.get("args"))
                if not key:
                    continue
                counts[key] = counts.get(key, 0) + 1
        return counts

    @classmethod
    def worst_repeat(cls, messages: list[Any]) -> tuple[str, str, int]:
        """对外入口：返回 (kind, path, count)；无浪费返回 ("", "", 0)。"""
        return cls._worst(cls.count_repeats(messages))

    @staticmethod
    def _worst(counts: dict[str, int]) -> tuple[str, str, int]:
        """返回 (kind, path, count)：kind ∈ read/write；无浪费返回 ("", "", 0)。"""
        worst_key, worst_n = "", 0
        for key, n in counts.items():
            if n > worst_n:
                worst_key, worst_n = key, n
        if not worst_key or worst_n < READ_SOFT_LIMIT:
            return "", "", 0
        kind, _, path = worst_key.partition("::")
        return kind, path, worst_n

    # ---- 模型调用前：分级注入纠正 ----

    async def abefore_model(self, state: Any, runtime: Any) -> dict[str, Any] | None:
        if self._steer_sink is None:
            return None
        context = getattr(runtime, "context", None)
        run_id = str(getattr(context, "run_id", "") or "")
        if not run_id:
            return None
        try:
            kind, path, count = self.worst_repeat(
                self._scoped_messages(list(state.get("messages") or []), run_id)
            )
        except Exception as exc:  # pragma: no cover - 统计失败不阻断生成
            logger.warning("wasteful_loop_count_failed", error=str(exc))
            return None
        if not kind:
            return None

        hard = count >= (READ_HARD_LIMIT if kind == "read" else WRITE_HARD_LIMIT)
        sig = f"{kind}:{path}:{'hard' if hard else 'soft'}"
        pushed = self._pushed.setdefault(run_id, set())
        if sig in pushed:
            return None  # 同级别纠正只推一次
        pushed.add(sig)

        if kind == "read":
            if not hard:
                text = (
                    f"（系统守护）检测到同一路径已被 read_file 读取 {count} 次。"
                    "禁止再次整篇/重复读取该文件：已读内容仍在对话历史中，直接基于历史推进任务；"
                    "确需局部内容只用 offset/limit 读缺失片段，或改用 outline_grep 摘录。"
                )
            else:
                text = (
                    f"（系统守护·熔断）同一路径已读取 {count} 次且任务无进展，触发强制换方向："
                    f"本轮**禁止**再对 {path} 发起任何 read_file/grep/glob 调用。"
                    "必须换处理方向：① 直接基于对话历史中已读内容推进并给出结论；"
                    "② 改用 outline_grep 摘录或 summaries/ 摘要；③ 派发 recaller/writer 子代理在隔离上下文处理；"
                    "④ 确属缺少信息时向用户说明缺什么。继续重复读取将被工具层直接阻断。"
                )
        else:
            if not hard:
                text = (
                    f"（系统守护）检测到同一文件已被写入/修改 {count} 次，疑似「写坏→重写→再否定」循环。"
                    "禁止推倒重写：先用 read_file 读取该文件当前内容，在其基础上用 edit_file 做最小增量修正；"
                    "若源材料本身存在重复或矛盾内容，如实按原文保留并在最终回复中向用户说明，不要反复自我否定。"
                )
            else:
                text = (
                    f"（系统守护·熔断）同一文件已写入/修改 {count} 次且无进展，触发强制换方向："
                    f"本轮**禁止**再对 {path} 推倒重写。必须换处理方向：① read_file 读当前内容后只做最小 edit_file 增量；"
                    "② 保留现有内容、把修正意见写入 summaries/ 或回复中交用户定夺；③ 派发 writer 子代理重写该章。"
                    "继续整篇重写将被工具层直接阻断。"
                )
        try:
            self._steer_sink(run_id, text)
        except Exception as exc:  # pragma: no cover - 入队失败不阻断生成
            logger.warning("wasteful_loop_steer_failed", error=str(exc))
            return None
        logger.warning(
            "wasteful_loop_detected", run_id=run_id, kind=kind, path=path, count=count, hard=hard
        )
        return None

    # ---- 工具调用层：硬阻断 ----

    async def awrap_tool_call(self, request: Any, handler: Any) -> Any:
        tool_call = getattr(request, "tool_call", None) or {}
        name = tool_call.get("name") or ""
        if name not in ("read_file", "write_file", "edit_file"):
            return await handler(request)
        key = _call_key(name, tool_call.get("args"))
        if not key:
            return await handler(request)
        state = getattr(request, "state", None)
        messages = list(state.get("messages") or []) if isinstance(state, dict) else []
        # ★ 统计范围同样收窄到本轮 run：这里是**硬阻断**，按全量历史计数会让长会话里
        #   本轮第一次读文件就被拦掉（详见 `_scoped_messages`）。拿不到 run_id 时退回
        #   全量统计（宁可少拦，不可错拦的取舍在此处反过来——但实际两个调用点都能拿到）。
        runtime = getattr(request, "runtime", None)
        context = getattr(runtime, "context", None)
        run_id = str(getattr(context, "run_id", "") or "")
        try:
            counts = self.count_repeats(self._scoped_messages(messages, run_id))
        except Exception:  # pragma: no cover
            return await handler(request)
        n = counts.get(key, 0)
        kind = key.partition("::")[0]
        hard = READ_HARD_LIMIT if kind == "read" else WRITE_HARD_LIMIT
        if n < hard:
            return await handler(request)
        path = key.partition("::")[2]
        logger.warning("wasteful_loop_blocked", tool=name, path=path, count=n)
        return ToolMessage(
            content=(
                f"（系统熔断）{name} 对 {path} 的调用已达 {n} 次，本次调用被阻断、未执行。"
                "禁止继续重复同一操作：请换处理方向——基于对话历史已读内容直接推进、"
                "改用 outline_grep/summaries 摘录、用 offset/limit 只读缺失片段、"
                "或派发子代理在隔离上下文处理；确属信息缺失时向用户说明。"
            ),
            tool_call_id=tool_call.get("id") or "",
            name=name,
        )
