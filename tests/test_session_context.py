# -*- coding: utf-8 -*-
"""
模块: tests
文件名: test_session_context.py
作者: xionggaoqing
时间: 2026-10-27
说明: 会话级上下文测试（手册「三、(3) 输入窗口」上下文 Chips 的后端消费链路）。

    - context_chapter_ids → build_pinned_chapters 注入 recap；
    - subagent_ids → build_agent 只装配选中子代理；
    - skill_ids → materialize_session_skills 物化会话精选目录 + builder 挂载路由。
"""

from __future__ import annotations

from langchain_core.messages import AIMessage
from tests.fake_model import ScriptedFakeChatModel
from tests.test_agent_e2e import _setup_provider_model

from app.agent.llm.factory import register_model_override
from app.core.paths import session_skills_dir


class TestPinnedChapters:
    async def test_build_pinned_chapters_includes_selected(self, session, client, novel_id, volume_id):
        """选中的章节出现在 pinned 上下文（标题 + 节选）。"""
        from app.agent.knowledge import build_pinned_chapters

        resp = await client.post(
            f"/api/v1/volumes/{volume_id}/chapters",
            json={"title": "钉住的章", "content": "这是被钉住的正文内容。"},
        )
        ch_id = resp.json()["data"]["id"]

        text = await build_pinned_chapters(session, novel_id, [ch_id])
        assert "钉住的章" in text
        assert "被钉住的正文" in text
        assert "用户指定的上下文章节" in text

    async def test_pinned_empty_for_unknown_ids(self, session, novel_id):
        from app.agent.knowledge import build_pinned_chapters

        assert await build_pinned_chapters(session, novel_id, ["nope"]) == ""
        assert await build_pinned_chapters(session, novel_id, []) == ""

    async def test_recap_includes_pinned_via_session_context(self, client, novel_id, volume_id):
        """会话设置了 context_chapter_ids 后，recap provider 输出包含该章。"""
        from app.agent.loop.resolve import make_recap_provider

        resp = await client.post(
            f"/api/v1/volumes/{volume_id}/chapters",
            json={"title": "上下文章", "content": "上下文正文甲。"},
        )
        ch_id = resp.json()["data"]["id"]

        sid = (
            await client.post("/api/v1/agent/sessions", json={"novel_id": novel_id})
        ).json()["data"]["id"]
        resp = await client.patch(
            f"/api/v1/agent/sessions/{sid}/context", json={"context_chapter_ids": [ch_id]}
        )
        assert resp.json()["code"] == 0
        assert resp.json()["data"]["context_chapter_ids"] == [ch_id]

        provider = make_recap_provider(novel_id, sid)
        recap = await provider(None)
        assert "上下文章" in recap

    async def test_session_context_of_resolves_ids_to_names(self, client, novel_id):
        """subagent/skill 的 DB id 被解析为 name（builtin: 前缀也支持）。"""
        from app.agent.loop.resolve import session_context_of
        from app.db.session import session_scope

        subs = (await client.get("/api/v1/subagents")).json()["data"]
        writer = next(s for s in subs if s["name"] == "writer")

        sid = (
            await client.post("/api/v1/agent/sessions", json={"novel_id": novel_id})
        ).json()["data"]["id"]
        await client.patch(
            f"/api/v1/agent/sessions/{sid}/context",
            json={"subagent_ids": [writer["id"]], "skill_ids": ["builtin:naming"]},
        )

        async with session_scope() as db:
            sub_names, skill_names = await session_context_of(db, sid)
        assert sub_names == {"writer"}
        assert skill_names == {"naming"}


class TestSessionSkillsMaterialization:
    async def test_materialize_selected_skills(self, client, session, novel_id):
        """会话精选技能物化到 data/session-skills/{sid}/（内置复制 + 用户技能复制）。"""
        from app.services.skills import materialize_session_skills

        # 建一个用户技能
        resp = await client.post(
            "/api/v1/skills", json={"name": "my-skill", "description": "用户技能描述", "body": "正文"}
        )
        assert resp.json()["code"] == 0

        sid = "test-session-1"
        count = await materialize_session_skills(session, sid, {"naming", "my-skill"})
        assert count == 2

        root = session_skills_dir(sid)
        assert (root / "naming" / "SKILL.md").exists()
        assert (root / "my-skill" / "SKILL.md").exists()
        text = (root / "naming" / "SKILL.md").read_text(encoding="utf-8")
        assert "name: naming" in text

    async def test_materialize_skips_missing(self, client, session):
        from app.services.skills import materialize_session_skills

        count = await materialize_session_skills(session, "s-x", {"no-such-skill"})
        assert count == 0

    async def test_rematerialize_clears_stale(self, client, session):
        """重新物化会清空旧选择（会话上下文变化后不留残影）。"""
        from app.services.skills import materialize_session_skills

        sid = "test-session-2"
        await materialize_session_skills(session, sid, {"naming", "prose-writing"})
        root = session_skills_dir(sid)
        assert (root / "prose-writing").exists()

        await materialize_session_skills(session, sid, {"naming"})
        assert not (root / "prose-writing").exists()
        assert (root / "naming" / "SKILL.md").exists()


class TestSubagentFiltering:
    async def test_build_agent_filters_by_session_selection(self, client, session, novel_id):
        """会话只选 writer → 装配的 subagents 只有 writer（general-purpose 由 deepagents 自动追加）。"""

        from app.agent.loop.builder import build_agent

        _pid, model_ref = await _setup_provider_model(client)
        register_model_override(model_ref, ScriptedFakeChatModel(responses=[AIMessage(content="ok")]))

        fake = ScriptedFakeChatModel(responses=[AIMessage(content="ok")])
        agent = await build_agent(
            session,
            project_id=novel_id,
            model=fake,
            subagents_enabled=True,
            tools_enabled=False,
            subagent_names={"writer"},
        )
        assert agent["subagent_names"] == ["writer"]

    async def test_build_agent_default_includes_all(self, client, session, novel_id):
        from app.agent.loop.builder import build_agent

        fake = ScriptedFakeChatModel(responses=[AIMessage(content="ok")])
        agent = await build_agent(
            session, project_id=novel_id, model=fake, subagents_enabled=True, tools_enabled=False
        )
        assert set(agent["subagent_names"]) >= {
            "planner", "writer", "editor", "continuity-checker", "researcher",
        }


class TestContextApiRoundtrip:
    async def test_update_context_persists_and_lists(self, client, novel_id):
        sid = (
            await client.post("/api/v1/agent/sessions", json={"novel_id": novel_id})
        ).json()["data"]["id"]

        await client.patch(
            f"/api/v1/agent/sessions/{sid}/context",
            json={"subagent_ids": ["a1"], "skill_ids": ["builtin:naming"], "context_chapter_ids": ["c1", "c2"]},
        )

        # 列表接口返回同构 DTO（前端 Chips 回填依赖）
        sessions = (await client.get("/api/v1/agent/sessions", params={"novel_id": novel_id})).json()["data"]
        sess = next(s for s in sessions if s["id"] == sid)
        assert sess["subagent_ids"] == ["a1"]
        assert sess["skill_ids"] == ["builtin:naming"]
        assert sess["context_chapter_ids"] == ["c1", "c2"]

    async def test_update_context_audited(self, client, novel_id):
        sid = (
            await client.post("/api/v1/agent/sessions", json={"novel_id": novel_id})
        ).json()["data"]["id"]
        await client.patch(
            f"/api/v1/agent/sessions/{sid}/context", json={"model_ref": "p:m"}
        )
        logs = (
            await client.get("/api/v1/audit-logs", params={"action": "agent.session.context"})
        ).json()["data"]
        assert logs["total"] >= 1
