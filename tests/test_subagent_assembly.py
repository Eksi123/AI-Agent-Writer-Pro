#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
模块: tests
文件名: test_subagent_assembly.py
作者: xionggaoqing
时间: 2026-10-27
说明: 子代理装配集成测试：开启子代理后 agent 可正常构建并运行（假模型）。

    conftest 默认 AGENT_SUBAGENTS_ENABLED=false（聚焦主循环），本文件显式开启验证装配路径：
    - DB 播种的内置子代理进入 `subagents=`；
    - 用户自建子代理（含 model_ref 覆盖）进入装配；
    - 停用/删除的子代理不进入装配。
"""

from __future__ import annotations

import pytest
from langchain_core.messages import AIMessage
from tests.fake_model import ScriptedFakeChatModel

from app.db.session import get_sessionmaker


@pytest.fixture(autouse=True)
def _enable_subagents(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("AGENT_SUBAGENTS_ENABLED", "true")
    from app.core.config import get_settings

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


async def _seed_provider_model(session) -> str:
    from app.db.base import new_id
    from app.db.models import LlmModel, LlmProvider

    pid = new_id()
    session.add(
        LlmProvider(
            id=pid, name="假厂商", type="openai_compatible", api_key_env=None, enabled=True, priority=0
        )
    )
    session.add(
        LlmModel(
            id=new_id(), provider_id=pid, model_id="fake", is_default=True, enabled=True, max_context=8192
        )
    )
    await session.flush()
    return f"{pid}:fake"


class TestSubagentAssembly:
    async def test_builtins_enter_subagents(self, client, session):
        """DB 播种的 5 个内置子代理全部进入装配。"""
        from app.agent.llm.factory import register_model_override
        from app.agent.loop.builder import build_agent, load_subagent_specs

        model_ref = await _seed_provider_model(session)
        await session.commit()
        register_model_override(model_ref, ScriptedFakeChatModel(responses=[AIMessage(content="ok")]))

        specs = await load_subagent_specs(session)
        names = {s["name"] for s in specs}
        assert {"planner", "writer", "editor", "continuity-checker", "researcher"} <= names

        novel = (await client.post("/api/v1/novels", json={"title": "子代理装配书"})).json()["data"]
        fake = ScriptedFakeChatModel(responses=[AIMessage(content="ok")])
        async with get_sessionmaker()() as db:
            agent = await build_agent(db, project_id=novel["id"], model=fake, subagents_enabled=True)
        assert set(agent["subagent_names"]) >= {"planner", "writer", "editor"}

    async def test_disabled_subagent_excluded(self, client, session):
        from app.agent.llm.factory import register_model_override
        from app.agent.loop.builder import build_agent, load_subagent_specs

        model_ref = await _seed_provider_model(session)
        await session.commit()
        register_model_override(model_ref, ScriptedFakeChatModel(responses=[AIMessage(content="ok")]))

        # 停用 writer
        subs = (await client.get("/api/v1/subagents")).json()["data"]
        writer = next(s for s in subs if s["name"] == "writer")
        await client.patch(f"/api/v1/subagents/{writer['id']}", json={"enabled": False})

        async with get_sessionmaker()() as db:
            specs = await load_subagent_specs(db)
        assert "writer" not in {s["name"] for s in specs}

        novel = (await client.post("/api/v1/novels", json={"title": "子代理装配书2"})).json()["data"]
        async with get_sessionmaker()() as db:
            agent = await build_agent(
                db,
                project_id=novel["id"],
                model=ScriptedFakeChatModel(responses=[AIMessage(content="ok")]),
                subagents_enabled=True,
            )
        assert "writer" not in agent["subagent_names"]

    async def test_user_subagent_included(self, client, session):
        from app.agent.llm.factory import register_model_override
        from app.agent.loop.builder import build_agent

        model_ref = await _seed_provider_model(session)
        await session.commit()
        register_model_override(model_ref, ScriptedFakeChatModel(responses=[AIMessage(content="ok")]))

        resp = await client.post(
            "/api/v1/subagents",
            json={"name": "my-helper", "description": "自建助手", "system_prompt": "你是助手"},
        )
        assert resp.json()["code"] == 0

        novel = (await client.post("/api/v1/novels", json={"title": "子代理装配书3"})).json()["data"]
        async with get_sessionmaker()() as db:
            agent = await build_agent(
                db,
                project_id=novel["id"],
                model=ScriptedFakeChatModel(responses=[AIMessage(content="ok")]),
                subagents_enabled=True,
            )
        assert "my-helper" in agent["subagent_names"]

    async def test_run_with_subagents_enabled(self, client, novel_id):
        """开启子代理后完整跑一条消息（task 工具可见、运行正常结束）。"""
        from tests.test_agent_e2e import (
            _drain_events,
            _install_fake,
            _post_message,
            _setup_provider_model,
        )

        _pid, model_ref = await _setup_provider_model(client)
        _install_fake(model_ref, [AIMessage(content="带子代理的回复")])

        sid = (
            await client.post("/api/v1/agent/sessions", json={"novel_id": novel_id})
        ).json()["data"]["id"]
        run_id = await _post_message(client, sid, "你好")
        events = await _drain_events(run_id)

        end = next(e for e in events if e["event"] == "run.end")
        assert end["data"]["status"] == "completed"
