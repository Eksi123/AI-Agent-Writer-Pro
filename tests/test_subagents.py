# -*- coding: utf-8 -*-
"""
模块: tests
文件名: test_subagents.py
说明: 子代理管理测试（M5）

    包含：
    - 子代理构建单元测试（build_subagents 函数）
    - 子代理 API 测试（内置播种、CRUD、权限保护）
    - 子代理装配集成测试（agent 构建时的子代理加载）
"""

from __future__ import annotations

import pytest
from langchain_core.messages import AIMessage

from fake_model import ScriptedFakeChatModel
from helpers import drain_events, install_fake, post_message, setup_provider_model

from app.agent.loop.builder import build_agent, load_subagent_specs
from app.agent.loop.llm.factory import register_model_override
from app.agent.subagents import BUILTIN_SUBAGENTS, build_subagents
from app.core.config import get_settings
from app.db.base import new_id
from app.db.models import LlmModel, LlmProvider
from app.db.session import get_sessionmaker


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------


async def _seed_provider_model(session) -> str:
    """在测试数据库中创建假的 LLM provider 和 model。"""
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


# ---------------------------------------------------------------------------
# 单元测试：build_subagents 函数
# ---------------------------------------------------------------------------


class TestSubagents:
    """测试 build_subagents 函数的各种配置场景。"""

    def test_build_subagents_all(self):
        """构建所有内置子代理。"""
        agents = build_subagents()
        names = [a["name"] for a in agents]
        assert names == list(BUILTIN_SUBAGENTS.keys())
        assert set(names) == {"planner", "writer", "reviewer", "recaller", "web-searcher"}
        for a in agents:
            assert a["mode"] == "isolated"
            assert a["description"] and a["system_prompt"]

    def test_builtin_default_tools_by_role(self):
        """内置子代理按职能预配默认内置工具（没有工具干不了活）。"""
        specs = BUILTIN_SUBAGENTS
        # 写作者/策划需要落盘：read + write + edit
        for name in ("writer", "planner"):
            assert {"read_file", "write_file", "edit_file"} <= set(specs[name]["tools"])
        # 审查者只读
        assert set(specs["reviewer"]["tools"]) == {"read_file", "ls", "grep", "glob"}
        # 回顾者只读 + 大纲按需摘录（不写文件）
        assert set(specs["recaller"]["tools"]) == {
            "read_file", "grep", "glob", "outline_recall",
        }
        assert "write_file" not in specs["recaller"]["tools"]
        # 检索者只有 web_search
        assert specs["web-searcher"]["tools"] == ["web_search"]

    def test_fork_mode_clears_skills(self):
        """fork 模式禁用 skills（deepagents 限制）；技能均为用户自建路径。"""
        agents = build_subagents(prompt_overrides={})
        for a in agents:
            for sp in a.get("skills") or []:
                assert sp.startswith("/skills/user/")

    def test_enabled_filter(self):
        """启用过滤器只返回指定的子代理。"""
        agents = build_subagents(enabled_names={"writer", "planner"})
        assert {a["name"] for a in agents} == {"writer", "planner"}

    def test_model_override(self):
        """模型覆盖参数正确应用到指定子代理。"""
        sentinel = object()
        agents = build_subagents(
            resolved_models={"p:m": sentinel}, model_overrides={"writer": "p:m"}
        )
        writer = next(a for a in agents if a["name"] == "writer")
        assert writer["model"] is sentinel


# ---------------------------------------------------------------------------
# API 测试：子代理端点
# ---------------------------------------------------------------------------


class TestSubagentsApi:
    """测试子代理的 HTTP API 端点。"""

    async def test_builtin_seeded_on_startup(self, client):
        """应用启动播种 5 个内置子代理。"""
        resp = await client.get("/api/v1/subagents")
        data = resp.json()["data"]
        names = {s["name"] for s in data}
        assert {"planner", "writer", "reviewer", "recaller", "web-searcher"} <= names
        builtins = [s for s in data if s["is_builtin"]]
        assert len(builtins) == 5
        for b in builtins:
            assert b["enabled"] is True
            assert b["system_prompt"]

    async def test_create_user_subagent(self, client):
        resp = await client.post(
            "/api/v1/subagents",
            json={
                "name": "my-reviewer",
                "description": "自定义审读子代理",
                "system_prompt": "你是审读员。",
                "mode": "isolated",
                "skills": [],
            },
        )
        assert resp.json()["code"] == 0
        dto = resp.json()["data"]
        assert dto["name"] == "my-reviewer"
        assert dto["is_builtin"] is False

        # 出现在列表里
        listing = (await client.get("/api/v1/subagents")).json()["data"]
        assert any(s["name"] == "my-reviewer" for s in listing)

    async def test_invalid_name_rejected(self, client):
        for bad in ["My-Agent", "1abc", "a b c", ""]:
            resp = await client.post(
                "/api/v1/subagents", json={"name": bad, "description": "x"}
            )
            assert resp.json()["code"] != 0, bad

    async def test_fork_with_skills_rejected(self, client):
        """fork 模式禁用 skills（deepagents 实测会抛错，API 层先拦）。"""
        resp = await client.post(
            "/api/v1/subagents",
            json={
                "name": "fork-agent",
                "description": "fork",
                "mode": "fork",
                "skills": ["/skills/user/some-skill"],
            },
        )
        assert resp.json()["code"] == 40001

        # fork + 无 skills 合法
        resp = await client.post(
            "/api/v1/subagents", json={"name": "fork-agent", "description": "fork", "mode": "fork"}
        )
        assert resp.json()["code"] == 0

    async def test_builtin_protected(self, client):
        listing = (await client.get("/api/v1/subagents")).json()["data"]
        writer = next(s for s in listing if s["name"] == "writer")

        # 不可删除
        resp = await client.delete(f"/api/v1/subagents/{writer['id']}")
        assert resp.json()["code"] == 40001

        # 不可改名
        resp = await client.patch(f"/api/v1/subagents/{writer['id']}", json={"name": "writer2"})
        assert resp.json()["code"] == 40001

        # 可停用
        resp = await client.patch(f"/api/v1/subagents/{writer['id']}", json={"enabled": False})
        assert resp.json()["code"] == 0
        assert resp.json()["data"]["enabled"] is False

        # 可改提示词
        resp = await client.patch(
            f"/api/v1/subagents/{writer['id']}", json={"system_prompt": "定制提示词"}
        )
        assert resp.json()["data"]["system_prompt"] == "定制提示词"

        # 恢复出厂
        resp = await client.post(f"/api/v1/subagents/{writer['id']}/reset")
        assert resp.json()["code"] == 0
        assert resp.json()["data"]["system_prompt"] != "定制提示词"
        assert resp.json()["data"]["enabled"] is True

    async def test_update_to_fork_with_skills_rejected(self, client):
        resp = await client.post(
            "/api/v1/subagents",
            json={"name": "combo", "description": "d", "skills": ["/skills/user/x"]},
        )
        pk = resp.json()["data"]["id"]
        resp = await client.patch(f"/api/v1/subagents/{pk}", json={"mode": "fork"})
        assert resp.json()["code"] == 40001

    async def test_delete_user_subagent(self, client):
        resp = await client.post("/api/v1/subagents", json={"name": "temp-agent", "description": "d"})
        pk = resp.json()["data"]["id"]
        resp = await client.delete(f"/api/v1/subagents/{pk}")
        assert resp.json()["data"]["deleted"] is True
        listing = (await client.get("/api/v1/subagents")).json()["data"]
        assert not any(s["name"] == "temp-agent" for s in listing)


# ---------------------------------------------------------------------------
# 集成测试：子代理装配
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _enable_subagents(monkeypatch: pytest.MonkeyPatch):
    """启用子代理功能进行测试。"""
    monkeypatch.setenv("AGENT_SUBAGENTS_ENABLED", "true")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


class TestSubagentAssembly:
    """测试 agent 构建时的子代理装配逻辑。"""

    async def test_builtins_enter_subagents(self, client, session):
        """DB 播种的 4 个内置子代理全部进入装配。"""
        model_ref = await _seed_provider_model(session)
        await session.commit()
        register_model_override(model_ref, ScriptedFakeChatModel(responses=[AIMessage(content="ok")]))

        specs = await load_subagent_specs(session)
        names = {s["name"] for s in specs}
        # 4 个内置子代理
        assert {"planner", "writer", "reviewer", "web-searcher"} <= names

        novel = (await client.post("/api/v1/novels", json={"title": "子代理装配书"})).json()["data"]
        fake = ScriptedFakeChatModel(responses=[AIMessage(content="ok")])
        async with get_sessionmaker()() as db:
            agent = await build_agent(db, project_id=novel["id"], model=fake, subagents_enabled=True)
        assert set(agent["subagent_names"]) >= {"planner", "writer", "reviewer"}

    async def test_disabled_subagent_excluded(self, client, session):
        """停用的子代理不进入装配。"""
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
        """用户自建的子代理进入装配。"""
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
        _pid, model_ref = await setup_provider_model(client)
        install_fake(model_ref, [AIMessage(content="带子代理的回复")])

        sid = (
            await client.post("/api/v1/agent/sessions", json={"novel_id": novel_id})
        ).json()["data"]["id"]
        run_id = await post_message(client, sid, "你好")
        events = await drain_events(run_id)

        end = next(e for e in events if e["event"] == "run.end")
        assert end["data"]["status"] == "completed"
