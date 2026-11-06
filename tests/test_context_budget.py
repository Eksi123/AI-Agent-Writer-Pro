# -*- coding: utf-8 -*-
"""上下文三级定位 + 两层保险 + 调用预算计数/插入位置 的聚焦测试（2026-11-06）。"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from app.agent.harness.llm.call_budget import ModelCallBudgetMiddleware
from app.agent.loop.middleware.recap import append_injections
from app.agent.workflows.writing_steps.intent import (
    context_demand_kind,
    resolve_chapter_in_volume,
    resolve_mentioned_appendix,
    resolve_mentioned_volume,
)

VOLS = [
    {"title": "第一卷 谪凡间", "index": 1},
    {"title": "第二卷 醉锦城", "index": 2},
]
APPS = [{"title": "附录A 世界观"}, {"title": "附录B 人物角色"}, {"title": "附录D 写作要求"}]
CHS = [
    {"id": "c1", "title": "第一章 锦城花事", "volume_title": "第二卷 醉锦城", "sort_order": 1},
    {"id": "c2", "title": "第二章 花朝寒诗", "volume_title": "第二卷 醉锦城", "sort_order": 2},
    {"id": "c3", "title": "第三章 艳彩梦境", "volume_title": "第二卷 醉锦城", "sort_order": 3},
]


class TestOutlineTargets:
    def test_volume_by_name(self):
        vol = resolve_mentioned_volume("基于大纲中第二卷的内容和附录信息，完成第二卷章节的生成", VOLS)
        assert vol is not None and vol["title"] == "第二卷 醉锦城"

    def test_volume_by_ordinal(self):
        vol = resolve_mentioned_volume("生成第二卷全部章节", VOLS)
        assert vol is not None and vol["index"] == 2

    def test_volume_none(self):
        assert resolve_mentioned_volume("今天写点什么", VOLS) is None

    def test_appendix_named(self):
        app = resolve_mentioned_appendix("参考附录D写作要求生成第二章", APPS)
        assert app is not None and app["title"] == "附录D 写作要求" and not app["all"]

    def test_appendix_bare_all(self):
        app = resolve_mentioned_appendix("把附录内容也考虑进去", APPS)
        assert app is not None and app["all"] is True

    def test_chapter_in_volume(self):
        cid = resolve_chapter_in_volume("大纲第二卷第三章", CHS, "第二卷 醉锦城")
        assert cid == "c3"

    def test_demand_kinds(self):
        assert context_demand_kind("基于大纲中第二卷的内容生成小说第二卷") == "outline_gen"
        assert context_demand_kind("回顾一下前面已写章节的剧情") == "review"
        assert context_demand_kind("今天天气怎么样") == ""


class TestInsertAfterSystem:
    def test_appends_at_tail(self):
        base = SystemMessage(content="base")
        msgs = [base, HumanMessage("hi"), AIMessage("yo")]
        out = append_injections(msgs, [SystemMessage(content="ctx", name="outline_ctx")])
        assert out[0] is base  # 基础系统提示保持最前（跨 run 稳定前缀）
        assert out[-1].name == "outline_ctx"  # 注入在尾部（易变内容不切断前缀）

    def test_no_base_system_still_appends(self):
        msgs = [HumanMessage("hi")]
        out = append_injections(msgs, [SystemMessage(content="ctx", name="recap")])
        assert out[0] is msgs[0]
        assert out[-1].name == "recap"


class TestCallBudgetCount:
    def test_scoped_to_current_run(self):
        msgs = [
            HumanMessage("旧问题"),
            AIMessage("旧回答"),
            AIMessage("旧回答2"),
            HumanMessage("新问题"),
            AIMessage("新回答"),
        ]
        assert ModelCallBudgetMiddleware._call_count(msgs) == 1

    def test_ignores_steer_and_nudge(self):
        msgs = [
            HumanMessage("问题"),
            HumanMessage(content="引导", name="steer"),
            HumanMessage("（系统守护）你上一轮没有给出最终回复…"),
            AIMessage("a1"),
        ]
        assert ModelCallBudgetMiddleware._call_count(msgs) == 1

    def test_normalize_choices(self):
        assert ModelCallBudgetMiddleware._normalize({"choice": "继续思考", "text": "x"}, "s")["choice"] == "continue"
        assert ModelCallBudgetMiddleware._normalize({"choice": "结束并输出总结"}, "s")["choice"] == "end"


class TestContextGuardGate:
    async def test_no_demand_no_inject(self):
        from app.agent.loop.middleware.context_guard import ContextGuardMiddleware

        mw = ContextGuardMiddleware(injected_slots_provider=lambda rid: set())
        called = {}

        async def handler(request):
            called["messages"] = list(request.messages)
            return "ok"

        request = SimpleNamespace(
            messages=[SystemMessage(content="base"), HumanMessage("今天天气怎么样")],
            runtime=SimpleNamespace(context=SimpleNamespace(run_id="r1")),
            override=lambda **kw: SimpleNamespace(messages=kw["messages"]),
        )
        # 用户消息从 run_manager 取：此处 monkeypatch
        from app.agent.loop import runner as runner_mod

        runner_mod.run_manager.note_user_message("r1", "今天天气怎么样")
        result = await mw.awrap_model_call(request, handler)
        assert result == "ok"  # 未 override → 原样 handler
        assert "messages" in called

    async def test_demand_without_slots_overrides(self):
        from app.agent.loop.middleware.context_guard import ContextGuardMiddleware

        mw = ContextGuardMiddleware(injected_slots_provider=lambda rid: set())

        async def handler(request):
            return request  # 返回收到的（override 后的）请求供断言

        request = SimpleNamespace(
            messages=[SystemMessage(content="base"), HumanMessage("基于大纲第二卷内容生成正文")],
            runtime=SimpleNamespace(context=SimpleNamespace(run_id="r2")),
            override=lambda **kw: SimpleNamespace(messages=kw["messages"]),
        )
        from app.agent.loop import runner as runner_mod

        runner_mod.run_manager.note_user_message("r2", "基于大纲第二卷内容生成正文")
        result = await mw.awrap_model_call(request, handler)
        names = [getattr(m, "name", None) for m in result.messages]
        assert "context_guard" in names

    async def test_slots_present_skips(self):
        from app.agent.loop.middleware.context_guard import ContextGuardMiddleware

        mw = ContextGuardMiddleware(injected_slots_provider=lambda rid: {"outline_ctx"})
        called = {}

        async def handler(request):
            called["ok"] = True
            return "ok"

        request = SimpleNamespace(
            messages=[SystemMessage(content="base"), HumanMessage("基于大纲第二卷内容生成正文")],
            runtime=SimpleNamespace(context=SimpleNamespace(run_id="r3")),
            override=lambda **kw: SimpleNamespace(messages=kw["messages"]),
        )
        assert await mw.awrap_model_call(request, handler) == "ok"
        assert called.get("ok")


class TestPromptCacheBreakpoints:
    """显式缓存断点：system + 本轮 user 打 cache_control，中间消息不动。"""

    def _run(self, mode: str | None):
        import asyncio

        from langchain.agents.middleware.types import ModelRequest
        from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

        from app.agent.harness.llm.prompt_cache import PromptCacheMiddleware

        class M:
            prompt_cache_mode = mode

        captured: dict[str, Any] = {}

        async def handler(request: Any) -> str:
            captured["messages"] = list(request.messages)
            return "ok"

        req = ModelRequest(
            model=M(),
            messages=[
                SystemMessage(content="sys"),
                HumanMessage(content="hist"),
                AIMessage(content="yo"),
                HumanMessage(content="now"),
            ],
            system_message=None,
            tool_choice=None,
            tools=[],
            response_format=None,
            state={},
            runtime=None,
            model_settings={},
        )
        asyncio.run(PromptCacheMiddleware().awrap_model_call(req, handler))
        return captured["messages"]

    def test_breakpoints_on_system_and_last_user(self):
        msgs = self._run("dashscope")
        assert msgs[0].content == [{"type": "text", "text": "sys", "cache_control": {"type": "ephemeral"}}]
        assert msgs[3].content == [{"type": "text", "text": "now", "cache_control": {"type": "ephemeral"}}]
        assert msgs[1].content == "hist"
        assert msgs[2].content == "yo"

    def test_no_mode_passthrough(self):
        msgs = self._run(None)
        assert all(isinstance(m.content, str) for m in msgs)

    def test_cached_tokens_of(self):
        from app.agent.harness.llm.budget import cached_tokens_of

        assert cached_tokens_of({"input_tokens": 100, "input_token_details": {"cache_read": 60}}) == 60
        assert cached_tokens_of({"input_tokens": 100}) == 0
        assert cached_tokens_of(None) == 0


if __name__ == "__main__":
    pytest.main([__file__, "-q"])
