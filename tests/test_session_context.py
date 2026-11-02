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

from app.agent.loop.builder import build_agent
from app.agent.loop.knowledge import build_pinned_chapters
from app.agent.loop.llm.factory import register_model_override
from app.agent.loop.resolve import make_recap_provider, session_context_of
from app.core.paths import session_skills_dir
from app.db.models import AgentRun
from app.db.session import session_scope
from app.services.agent import project_usage, session_usage
from app.services.skills import materialize_session_skills


class TestPinnedChapters:
    async def test_build_pinned_chapters_includes_selected(self, session, client, novel_id, volume_id):
        """选中的章节出现在 pinned 上下文（标题 + 节选）。"""

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

        assert await build_pinned_chapters(session, novel_id, ["nope"]) == ""
        assert await build_pinned_chapters(session, novel_id, []) == ""

    async def test_recap_includes_pinned_via_session_context(self, client, novel_id, volume_id):
        """会话设置了 context_chapter_ids 后，recap provider 输出包含该章。"""

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
        """subagent/skill 的 DB id 被解析为 name（技能 = 用户自建）。"""

        subs = (await client.get("/api/v1/subagents")).json()["data"]
        writer = next(s for s in subs if s["name"] == "writer")

        # 建一个用户技能，用它的 DB id 做会话选择
        sk = (
            await client.post(
                "/api/v1/skills", json={"name": "ctx-skill", "description": "d", "body": "b"}
            )
        ).json()["data"]

        sid = (
            await client.post("/api/v1/agent/sessions", json={"novel_id": novel_id})
        ).json()["data"]["id"]
        await client.patch(
            f"/api/v1/agent/sessions/{sid}/context",
            json={"subagent_ids": [writer["id"]], "skill_ids": [sk["id"]]},
        )

        async with session_scope() as db:
            sub_names, skill_names = await session_context_of(db, sid)
        assert sub_names == {"writer"}
        assert skill_names == {"ctx-skill"}

    async def test_legacy_builtin_prefix_ids_skipped(self, client, novel_id):
        """历史 `builtin:{name}` 技能 id（web_search 除外）已失效，解析时静默跳过。"""
        sid = (
            await client.post("/api/v1/agent/sessions", json={"novel_id": novel_id})
        ).json()["data"]["id"]
        await client.patch(
            f"/api/v1/agent/sessions/{sid}/context",
            json={"skill_ids": ["builtin:naming"]},
        )
        async with session_scope() as db:
            _sub_names, skill_names = await session_context_of(db, sid)
        assert skill_names == set()

    async def test_builtin_web_search_resolves_as_session_skill(self, client, novel_id):
        """v1102：`builtin:web_search` 为会话级内置写作技能，解析出 `web_search`。"""
        sid = (
            await client.post("/api/v1/agent/sessions", json={"novel_id": novel_id})
        ).json()["data"]["id"]
        await client.patch(
            f"/api/v1/agent/sessions/{sid}/context",
            json={"skill_ids": ["builtin:web_search"]},
        )
        async with session_scope() as db:
            _sub_names, skill_names = await session_context_of(db, sid)
        assert skill_names == {"web_search"}

    async def test_materialize_counts_web_search_without_file(self, client, session):
        """v1102：web_search 计入会话精选数但不落 SKILL.md（主代理工具恒可用）。"""
        count = await materialize_session_skills(session, "s-web", {"web_search"})
        assert count == 1
        assert not (session_skills_dir("s-web") / "web_search" / "SKILL.md").exists()


class TestProjectUsage:
    async def test_usage_counts_runs_not_messages(self, client, novel_id):
        """★v1029：token 按 `agent_runs` 汇总，子代理（独立上下文，不进消息树）的用量也算进来。"""

        sid = (
            await client.post("/api/v1/agent/sessions", json={"novel_id": novel_id})
        ).json()["data"]["id"]

        async with session_scope() as db:
            db.add(
                AgentRun(
                    id="run-usage-1",
                    session_id=sid,
                    project_id=novel_id,
                    status="completed",
                    # run 级用量已含子代理（streaming 对带 namespace 的 chunk 同样累加）
                    tokens_in=700,
                    tokens_out=300,
                )
            )
            await db.commit()

        async with session_scope() as db:
            usage = await project_usage(db, novel_id)

        assert usage["session_count"] == 1
        assert usage["tokens_in"] == 700
        assert usage["tokens_out"] == 300
        assert usage["tokens_total"] == 1000

    async def test_usage_sums_multiple_runs_without_double_count(self, client, novel_id):
        """每个 run 的 tokens 是收尾时整体赋值，逐 run 求和不会重复计数。"""

        sid = (
            await client.post("/api/v1/agent/sessions", json={"novel_id": novel_id})
        ).json()["data"]["id"]

        async with session_scope() as db:
            for i, (tin, tout) in enumerate([(100, 50), (200, 80)], start=1):
                db.add(
                    AgentRun(
                        id=f"run-multi-{i}",
                        session_id=sid,
                        project_id=novel_id,
                        status="completed",
                        tokens_in=tin,
                        tokens_out=tout,
                    )
                )
            await db.commit()

        async with session_scope() as db:
            usage = await project_usage(db, novel_id)

        assert usage["tokens_in"] == 300
        assert usage["tokens_out"] == 130
        assert usage["tokens_total"] == 430

    async def test_usage_scoped_to_project(self, client, novel_id):
        """用量按项目隔离：其它项目的 run 不计入。"""

        sid = (
            await client.post("/api/v1/agent/sessions", json={"novel_id": novel_id})
        ).json()["data"]["id"]

        async with session_scope() as db:
            db.add(
                AgentRun(
                    id="run-scope-1",
                    session_id=sid,
                    project_id=novel_id,
                    status="completed",
                    tokens_in=10,
                    tokens_out=5,
                )
            )
            db.add(
                AgentRun(
                    id="run-scope-other",
                    session_id="other-session",
                    project_id="other-project",
                    status="completed",
                    tokens_in=9999,
                    tokens_out=9999,
                )
            )
            await db.commit()

        async with session_scope() as db:
            usage = await project_usage(db, novel_id)

        assert usage["tokens_total"] == 15


class TestSessionUsage:
    """v1029（大模型选择弹窗 1.4）：会话级上下文占用 = 最近一次 run 的输入 token。"""

    @staticmethod
    async def _add_run(session_id: str, project_id: str, run_id: str, *, tokens_in: int, started_at: str):

        async with session_scope() as db:
            db.add(
                AgentRun(
                    id=run_id,
                    session_id=session_id,
                    project_id=project_id,
                    status="completed",
                    tokens_in=tokens_in,
                    tokens_out=tokens_in // 2,
                    started_at=started_at,
                )
            )
            await db.commit()

    async def test_no_runs_returns_zero(self, client, novel_id):
        """全新会话没有任何 run → 占用 0（前端显示剩余 100%）。"""

        sid = (
            await client.post("/api/v1/agent/sessions", json={"novel_id": novel_id})
        ).json()["data"]["id"]

        usage = await client.get(f"/api/v1/agent/sessions/{sid}/usage")
        assert usage.status_code == 200
        assert usage.json()["data"] == {"session_id": sid, "tokens_in": 0, "tokens_out": 0}

        # service 层同构

        async with session_scope() as db:
            assert (await session_usage(db, sid))["tokens_in"] == 0

    async def test_returns_most_recent_run_not_sum(self, client, novel_id):
        """★取**最后一次** run 的 tokens_in，而非历史累加（累加会严重高估占用）。"""

        sid = (
            await client.post("/api/v1/agent/sessions", json={"novel_id": novel_id})
        ).json()["data"]["id"]

        # 故意乱序写入：较新的 run 先落库
        await self._add_run(
            sid, novel_id, "run-sess-new", tokens_in=5000, started_at="2026-10-29T12:00:00.000Z"
        )
        await self._add_run(
            sid, novel_id, "run-sess-old", tokens_in=1000, started_at="2026-10-29T09:00:00.000Z"
        )
        await self._add_run(
            sid, novel_id, "run-sess-mid", tokens_in=3000, started_at="2026-10-29T10:30:00.000Z"
        )

        async with session_scope() as db:
            usage = await session_usage(db, sid)

        assert usage["tokens_in"] == 5000  # 不是 9000（求和）
        assert usage["tokens_out"] == 2500

    async def test_scoped_to_session(self, client, novel_id):
        """按会话隔离：同项目其它会话的 run 不计入。"""

        sid = (
            await client.post("/api/v1/agent/sessions", json={"novel_id": novel_id})
        ).json()["data"]["id"]
        other = (
            await client.post("/api/v1/agent/sessions", json={"novel_id": novel_id})
        ).json()["data"]["id"]

        await self._add_run(
            sid, novel_id, "run-scope-mine", tokens_in=200, started_at="2026-10-29T08:00:00.000Z"
        )
        await self._add_run(
            other, novel_id, "run-scope-theirs", tokens_in=9999, started_at="2026-10-29T09:00:00.000Z"
        )

        async with session_scope() as db:
            assert (await session_usage(db, sid))["tokens_in"] == 200
            assert (await session_usage(db, other))["tokens_in"] == 9999

    async def test_unknown_session_returns_zero(self, client, novel_id):
        """不存在的会话 → 0（不抛异常，前端保持 100%）。"""

        async with session_scope() as db:
            assert (await session_usage(db, "no-such-session"))["tokens_in"] == 0


class TestSessionSkillsMaterialization:
    async def test_materialize_selected_skills(self, client, session, novel_id):
        """会话精选技能物化到 data/session-skills/{sid}/（技能 = 用户自建）。"""

        # 建两个用户技能
        for name in ("my-skill", "other-skill"):
            resp = await client.post(
                "/api/v1/skills", json={"name": name, "description": "用户技能描述", "body": "正文"}
            )
            assert resp.json()["code"] == 0

        sid = "test-session-1"
        count = await materialize_session_skills(session, sid, {"my-skill", "other-skill"})
        assert count == 2

        root = session_skills_dir(sid)
        assert (root / "my-skill" / "SKILL.md").exists()
        assert (root / "other-skill" / "SKILL.md").exists()
        text = (root / "my-skill" / "SKILL.md").read_text(encoding="utf-8")
        assert "name: my-skill" in text

    async def test_materialize_skips_missing(self, client, session):

        count = await materialize_session_skills(session, "s-x", {"no-such-skill"})
        assert count == 0

    async def test_materialize_skips_disabled(self, client, session):
        """停用的用户技能不进会话精选。"""
        resp = await client.post(
            "/api/v1/skills", json={"name": "off-skill", "description": "d", "body": "b"}
        )
        pk = resp.json()["data"]["id"]
        await client.patch(f"/api/v1/skills/{pk}", json={"enabled": False})

        count = await materialize_session_skills(session, "s-off", {"off-skill"})
        assert count == 0

    async def test_rematerialize_clears_stale(self, client, session):
        """重新物化会清空旧选择（会话上下文变化后不留残影）。"""
        for name in ("keep-skill", "drop-skill"):
            await client.post(
                "/api/v1/skills", json={"name": name, "description": "d", "body": "b"}
            )

        sid = "test-session-2"
        await materialize_session_skills(session, sid, {"keep-skill", "drop-skill"})
        root = session_skills_dir(sid)
        assert (root / "drop-skill").exists()

        await materialize_session_skills(session, sid, {"keep-skill"})
        assert not (root / "drop-skill").exists()
        assert (root / "keep-skill" / "SKILL.md").exists()


class TestSubagentFiltering:
    async def test_build_agent_filters_by_session_selection(self, client, session, novel_id):
        """会话只选 writer → 装配的 subagents 只有 writer（general-purpose 由 deepagents 自动追加）。"""


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

        fake = ScriptedFakeChatModel(responses=[AIMessage(content="ok")])
        agent = await build_agent(
            session, project_id=novel_id, model=fake, subagents_enabled=True, tools_enabled=False
        )
        assert set(agent["subagent_names"]) >= {
            "planner", "writer", "reviewer", "web-searcher",
        }


class TestContextApiRoundtrip:
    async def test_update_context_persists_and_lists(self, client, novel_id):
        sid = (
            await client.post("/api/v1/agent/sessions", json={"novel_id": novel_id})
        ).json()["data"]["id"]

        sk = (
            await client.post(
                "/api/v1/skills", json={"name": "round-skill", "description": "d", "body": "b"}
            )
        ).json()["data"]

        await client.patch(
            f"/api/v1/agent/sessions/{sid}/context",
            json={"subagent_ids": ["a1"], "skill_ids": [sk["id"]], "context_chapter_ids": ["c1", "c2"]},
        )

        # 列表接口返回同构 DTO（前端 Chips 回填依赖）
        sessions = (await client.get("/api/v1/agent/sessions", params={"novel_id": novel_id})).json()["data"]
        sess = next(s for s in sessions if s["id"] == sid)
        assert sess["subagent_ids"] == ["a1"]
        assert sess["skill_ids"] == [sk["id"]]
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
