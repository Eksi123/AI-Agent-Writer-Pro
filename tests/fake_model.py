# -*- coding: utf-8 -*-
"""
模块: tests
文件名: fake_model.py
作者: xionggaoqing
时间: 2026-10-27
说明: 假模型（手册「十一、测试与验收策略」：Agent 测试用假模型驱动，不打真实 LLM）。

    langchain 自带的 `FakeMessagesListChatModel` / `GenericFakeChatModel` 的 `bind_tools`
    抛 `NotImplementedError`，而 deepagents 装配必然会 `bind_tools`，故自建可绑工具的假模型。

    `ScriptedFakeChatModel`：
    - 按脚本依次返回预设 AIMessage（含 tool_calls），脚本耗尽后循环最后一条；
    - `bind_tools` 返回自身（记录已绑工具名，便于断言）；
    - 支持流式（按空白切分产出 chunk），使 `stream_mode="messages"` 也能拿到 delta；
    - `sleep` 可模拟耗时，用于测试取消与超时。
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Iterator
from typing import Any

from langchain_core.callbacks import AsyncCallbackManagerForLLMRun, CallbackManagerForLLMRun
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, AIMessageChunk, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult


class ScriptedFakeChatModel(BaseChatModel):
    """脚本化假模型：按顺序返回预设消息，支持 bind_tools 与流式。"""

    responses: list[BaseMessage] = []
    calls: list[list[BaseMessage]] = []
    bound_tools: list[str] = []
    sleep: float | None = None
    cursor: int = 0
    #: 抛异常的轮次（1-based）；用于测试重试与降级
    fail_on_call: int | None = None
    fail_error: str = "simulated provider failure"

    @property
    def _llm_type(self) -> str:
        return "scripted-fake-chat-model"

    def bind_tools(self, tools: Any, **kwargs: Any) -> ScriptedFakeChatModel:
        names: list[str] = []
        for t in tools or []:
            name = getattr(t, "name", None)
            if name is None and isinstance(t, dict):
                name = (t.get("function") or {}).get("name") or t.get("name")
            if name:
                names.append(str(name))
        # 返回 self 而非副本：langchain 在**每次**模型调用前都会 bind_tools，
        # 若返回 model_copy() 则 cursor 每次归零、永远重放 responses[0]。
        # 脚本模型本就忽略 tools（直接回放预设消息），记录工具名仅供断言。
        self.bound_tools = names
        return self

    def _next_response(self, messages: list[BaseMessage]) -> BaseMessage:
        self.calls.append(list(messages))
        self.cursor += 1
        if self.fail_on_call is not None and self.cursor == self.fail_on_call:
            raise RuntimeError(self.fail_error)
        if not self.responses:
            return AIMessage(content="(假模型无脚本)")
        idx = min(self.cursor - 1, len(self.responses) - 1)
        return self.responses[idx]

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        message = self._next_response(messages)
        if not isinstance(message, AIMessage):
            message = AIMessage(content=str(message.content))
        return ChatResult(generations=[ChatGeneration(message=message)])

    async def _agenerate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: AsyncCallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        if self.sleep:
            await asyncio.sleep(self.sleep)
        return self._generate(messages, stop=stop, **kwargs)

    def _stream(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> Iterator[ChatGenerationChunk]:
        message = self._next_response(messages)
        yield from self._to_chunks(message)

    async def _astream(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: AsyncCallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[ChatGenerationChunk]:
        if self.sleep:
            await asyncio.sleep(self.sleep)
        message = self._next_response(messages)
        for chunk in self._to_chunks(message):
            yield chunk

    @staticmethod
    def _to_chunks(message: BaseMessage) -> Iterator[ChatGenerationChunk]:
        """把整条消息切成块：先文本（按空白切分），再 tool_calls（整块）。"""
        content = message.content
        text = content if isinstance(content, str) else json.dumps(content, ensure_ascii=False)
        tool_calls = list(getattr(message, "tool_calls", None) or [])

        if not text and not tool_calls:
            yield ChatGenerationChunk(message=AIMessageChunk(content=""))
            return

        if text:
            import re

            parts = re.split(r"(\s+)", text)
            for part in parts:
                if not part:
                    continue
                yield ChatGenerationChunk(message=AIMessageChunk(content=part))

        if tool_calls:
            yield ChatGenerationChunk(
                message=AIMessageChunk(
                    content="",
                    tool_call_chunks=[
                        {
                            "name": tc.get("name"),
                            "args": json.dumps(tc.get("args") or {}, ensure_ascii=False),
                            "id": tc.get("id"),
                            "index": i,
                            "type": "tool_call_chunk",
                        }
                        for i, tc in enumerate(tool_calls)
                    ],
                )
            )


def ai_text(text: str) -> AIMessage:
    """便捷构造：纯文本回复。"""
    return AIMessage(content=text)


def ai_tool_call(tool: str, args: dict[str, Any], call_id: str = "call_1") -> AIMessage:
    """便捷构造：工具调用。"""
    return AIMessage(content="", tool_calls=[{"name": tool, "args": args, "id": call_id}])
