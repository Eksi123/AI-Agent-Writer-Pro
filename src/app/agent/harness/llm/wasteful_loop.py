# -*- coding: utf-8 -*-
"""
模块: app.agent.harness.llm.wasteful_loop
文件名: wasteful_loop.py
作者: xionggaoqing
时间: 2026-11-05
说明: `WastefulLoopGuardMiddleware`：浪费型循环熔断（token 治理）。

    背景（2026-11-05 实测 run：718K 输入 token）：模型对同一附件反复全量重读、
    写坏后「重读→重写→再否定」死循环，历史只增不减，token 平方级膨胀。

    本中间件在**每次模型调用前**统计 state 里的工具调用，命中两类浪费模式即
    通过 steer 队列注入一条纠正消息（下一次模型调用可见），只做提醒不阻断：
    1. 同一路径 `read_file` 次数 ≥ 阈值（附件/大纲反复全量重读）；
    2. 同一文件 `write_file`/`edit_file` 次数 ≥ 阈值（写坏→重写死循环）。

    纠正消息经 `run_manager.push_steer` 入队，由 `SteerInjectionMiddleware`
    在下一次模型调用前追加为 HumanMessage——与「运行中引导」同通道，
    不新增 state 消息类型、不落消息树。
"""

from __future__ import annotations

from typing import Any

from langchain.agents.middleware.types import AgentMiddleware

from app.core.logging import get_logger

logger = get_logger(__name__)

#: 同一路径读取次数达到该值即注入纠正（正常分段读每段只读一次）
READ_REPEAT_LIMIT = 3
#: 同一文件写入次数达到该值即注入纠正（正常重构每文件写 1~2 次）
WRITE_REPEAT_LIMIT = 3


def _arg_path(args: Any) -> str:
    if not isinstance(args, dict):
        return ""
    return str(args.get("file_path") or args.get("path") or "")


class WastefulLoopGuardMiddleware(AgentMiddleware):
    """检测「反复重读同一路径 / 反复重写同一文件」并注入纠正引导。"""

    name = "WastefulLoopGuard"

    def __init__(self, steer_sink: Any = None) -> None:
        """`steer_sink`：`(run_id, text) -> None`，通常为 `run_manager.push_steer`。"""
        super().__init__()
        self._steer_sink = steer_sink

    def count_repeats(self, messages: list[Any]) -> tuple[str, int]:
        """统计 state 消息里的重复工具调用，返回 (模式描述, 次数)；无浪费返回 ("", 0)。"""
        reads: dict[str, int] = {}
        writes: dict[str, int] = {}
        for msg in messages:
            calls = getattr(msg, "tool_calls", None) or []
            for tc in calls:
                if not isinstance(tc, dict):
                    continue
                name = tc.get("name") or ""
                path = _arg_path(tc.get("args"))
                if not path:
                    continue
                if name == "read_file":
                    reads[path] = reads.get(path, 0) + 1
                elif name in ("write_file", "edit_file"):
                    writes[path] = writes.get(path, 0) + 1
        worst_read = max(reads.items(), key=lambda kv: kv[1], default=("", 0))
        worst_write = max(writes.items(), key=lambda kv: kv[1], default=("", 0))
        if worst_read[1] >= READ_REPEAT_LIMIT and worst_read[1] >= worst_write[1]:
            return "read", worst_read[1]
        if worst_write[1] >= WRITE_REPEAT_LIMIT:
            return "write", worst_write[1]
        return "", 0

    async def abefore_model(self, state: Any, runtime: Any) -> dict[str, Any] | None:
        if self._steer_sink is None:
            return None
        context = getattr(runtime, "context", None)
        run_id = str(getattr(context, "run_id", "") or "")
        if not run_id:
            return None
        try:
            kind, count = self.count_repeats(list(state.get("messages") or []))
        except Exception as exc:  # pragma: no cover - 统计失败不阻断生成
            logger.warning("wasteful_loop_count_failed", error=str(exc))
            return None
        if not kind:
            return None
        if kind == "read":
            text = (
                f"（系统守护）检测到同一路径已被 read_file 读取 {count} 次。"
                "禁止再次整篇/重复读取该文件：已读内容仍在对话历史中，直接基于历史推进任务；"
                "确需局部内容只用 offset/limit 读缺失片段，或改用 outline_grep 摘录。"
            )
        else:
            text = (
                f"（系统守护）检测到同一文件已被写入/修改 {count} 次，疑似「写坏→重写→再否定」循环。"
                "禁止推倒重写：先用 read_file 读取该文件当前内容，在其基础上用 edit_file 做最小增量修正；"
                "若源材料本身存在重复或矛盾内容，如实按原文保留并在最终回复中向用户说明，不要反复自我否定。"
            )
        try:
            self._steer_sink(run_id, text)
        except Exception as exc:  # pragma: no cover - 入队失败不阻断生成
            logger.warning("wasteful_loop_steer_failed", error=str(exc))
            return None
        logger.warning("wasteful_loop_detected", run_id=run_id, kind=kind, count=count)
        return None
