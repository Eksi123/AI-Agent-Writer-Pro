# -*- coding: utf-8 -*-
"""
模块: tests
文件名: test_agent_sessions.py
作者: xionggaoqing
时间: 2026-10-27
说明: Agent 会话管理与上下文测试。

    包含：
    - 会话 CRUD（创建/重命名/删除）
    - 会话上下文（置顶章节、技能精选、子代理选择）
    - 会话用量统计（项目级 + 会话级）
    - 上下文 API 往返与审计
"""

from __future__ import annotations

import pytest
from langchain_core.messages import AIMessage

from helpers import setup_provider_model
from fake_model import ScriptedFakeChatModel

from app.agent.loop.builder import build_agent
from app.agent.loop.knowledge import build_pinned_chapters
from app.agent.loop.llm.factory import register_model_override
from app.agent.loop.resolve import make_recap_provider, session_context_of
from app.core.paths import session_skills_dir
from app.db.models import AgentRun, Message
from app.db.session import session_scope
from app.services.agent import project_usage, session_usage
from app.services.skills import materialize_session_skills


class TestSessionCRUD:
    """会话生命周期管理"""

    async def test_create_session_isolated_by_project(self, client, novel_id):
        resp = await client.post("/api/v1/agent/sessions", json={"novel_id": novel_id})
        assert resp.status_code == 200, resp.text
        sess = resp.json()["data"]
        assert sess["project_id"] == novel_id
        assert sess["status"] == "idle"

        # 列表按项目过滤
        resp = await client.get("/api/v1/agent/sessions", params={"novel_id": novel_id})
        assert len(resp.json()["data"]) == 1
        resp = await client.get("/api/v1/agent/sessions", params={"novel_id": "other"})
        assert resp.json()["data"] == []

    async def test_create_session_unknown_project(self, client):
        resp = await client.post("/api/v1/agent/sessions", json={"novel_id": "nope"})
        assert resp.json()["code"] == 40401

    async def test_rename_and_delete_session(self, client, novel_id):
        sid = (await client.post("/api/v1/agent/sessions", json={"novel_id": novel_id})).json()["data"]["id"]
        resp = await client.patch(f"/api/v1/agent/sessions/{sid}", json={"title": "大纲会话"})
        assert resp.json()["data"]["title"] == "大纲会话"

        resp = await client.delete(f"/api/v1/agent/sessions/{sid}")
        assert resp.json()["data"]["deleted"] is True
        resp = await client.get(f"/api/v1/agent/sessions/{sid}/messages")
        assert resp.json()["code"] == 40401


class TestPinnedChapters:
    """置顶章节上下文"""

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

        sid = (await client.post("/api/v1/agent/sessions", json={"novel_id": novel_id})).json()["data"]["id"]
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

        sk = (
            await client.post("/api/v1/skills", json={"name": "ctx-skill", "description": "d", "body": "b"})
        ).json()["data"]

        sid = (await client.post("/api/v1/agent/sessions", json={"novel_id": novel_id})).json()["data"]["id"]
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
        sid = (await client.post("/api/v1/agent/sessions", json={"novel_id": novel_id})).json()["data"]["id"]
        await client.patch(
            f"/api/v1/agent/sessions/{sid}/context",
            json={"skill_ids": ["builtin:naming"]},
        )
        async with session_scope() as db:
            _sub_names, skill_names = await session_context_of(db, sid)
        assert skill_names == set()

    async def test_builtin_web_search_resolves_as_session_skill(self, client, novel_id):
        """`builtin:web_search` 为会话级内置写作技能，解析出 `web_search`。"""
        sid = (await client.post("/api/v1/agent/sessions", json={"novel_id": novel_id})).json()["data"]["id"]
        await client.patch(
            f"/api/v1/agent/sessions/{sid}/context",
            json={"skill_ids": ["builtin:web_search"]},
        )
        async with session_scope() as db:
            _sub_names, skill_names = await session_context_of(db, sid)
        assert skill_names == {"web_search"}


class TestProjectUsage:
    """项目级用量统计"""

    async def test_usage_counts_runs_not_messages(self, client, novel_id):
        """token 按 `agent_runs` 汇总，子代理（独立上下文，不进消息树）的用量也算进来。"""
        sid = (await client.post("/api/v1/agent/sessions", json={"novel_id": novel_id})).json()["data"]["id"]

        async with session_scope() as db:
            db.add(
                AgentRun(
                    id="run-usage-1",
                    session_id=sid,
                    project_id=novel_id,
                    status="completed",
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
        sid = (await client.post("/api/v1/agent/sessions", json={"novel_id": novel_id})).json()["data"]["id"]

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
        sid = (await client.post("/api/v1/agent/sessions", json={"novel_id": novel_id})).json()["data"]["id"]

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
    """会话级用量统计（取最近一次模型调用的输入 token）"""

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

    @staticmethod
    async def _add_assistant_msg(session_id: str, msg_id: str, *, tokens_in: int, created_at: str):
        async with session_scope() as db:
            db.add(
                Message(
                    id=msg_id,
                    session_id=session_id,
                    role="assistant",
                    content_json="x",
                    tokens_in=tokens_in,
                    tokens_out=tokens_in // 2,
                    created_at=created_at,
                )
            )
            await db.commit()

    async def test_no_runs_returns_zero(self, client, novel_id):
        """全新会话没有任何 run → 占用 0（前端显示剩余 100%）。"""
        sid = (await client.post("/api/v1/agent/sessions", json={"novel_id": novel_id})).json()["data"]["id"]

        usage = await client.get(f"/api/v1/agent/sessions/{sid}/usage")
        assert usage.status_code == 200
        assert usage.json()["data"] == {
            "session_id": sid,
            "tokens_in": 0,
            "tokens_out": 0,
            "cached_in": 0,
        }

        async with session_scope() as db:
            assert (await session_usage(db, sid))["tokens_in"] == 0

    async def test_returns_last_model_call_not_run_sum(self, client, novel_id):
        """取**最后一次模型调用**的输入 token，而非 run 累加值（累加会虚高超过窗口）。"""
        sid = (await client.post("/api/v1/agent/sessions", json={"novel_id": novel_id})).json()["data"]["id"]

        # run 累加 700K（长 run 多次调用重发历史），但最后一次调用实际只占 90K
        await self._add_run(sid, novel_id, "run-ctx-sum", tokens_in=700_000, started_at="2026-10-29T12:00:00.000Z")
        await self._add_assistant_msg(sid, "m-ctx-1", tokens_in=80_000, created_at="2026-10-29T12:00:10.000Z")
        await self._add_assistant_msg(sid, "m-ctx-2", tokens_in=90_000, created_at="2026-10-29T12:01:10.000Z")

        async with session_scope() as db:
            usage = await session_usage(db, sid)

        assert usage["tokens_in"] == 90_000  # 不是 700_000（run 累加）也不是 170_000（消息求和）
        assert usage["tokens_out"] == 45_000

    async def test_scoped_to_session(self, client, novel_id):
        """按会话隔离：同项目其它会话的消息不计入。"""
        sid = (await client.post("/api/v1/agent/sessions", json={"novel_id": novel_id})).json()["data"]["id"]
        other = (await client.post("/api/v1/agent/sessions", json={"novel_id": novel_id})).json()["data"]["id"]

        await self._add_assistant_msg(sid, "m-scope-mine", tokens_in=200, created_at="2026-10-29T08:00:00.000Z")
        await self._add_assistant_msg(other, "m-scope-theirs", tokens_in=9999, created_at="2026-10-29T09:00:00.000Z")

        async with session_scope() as db:
            assert (await session_usage(db, sid))["tokens_in"] == 200
            assert (await session_usage(db, other))["tokens_in"] == 9999

    async def test_unknown_session_returns_zero(self, client, novel_id):
        """不存在的会话 → 0（不抛异常，前端保持 100%）。"""
        async with session_scope() as db:
            assert (await session_usage(db, "no-such-session"))["tokens_in"] == 0


class TestContextApiRoundtrip:
    """上下文 API 往返与审计"""

    async def test_update_context_persists_and_lists(self, client, novel_id):
        sid = (await client.post("/api/v1/agent/sessions", json={"novel_id": novel_id})).json()["data"]["id"]

        sk = (
            await client.post("/api/v1/skills", json={"name": "round-skill", "description": "d", "body": "b"})
        ).json()["data"]

        await client.patch(
            f"/api/v1/agent/sessions/{sid}/context",
            json={"subagent_ids": ["a1"], "skill_ids": [sk["id"]], "context_chapter_ids": ["c1", "c2"]},
        )

        sessions = (await client.get("/api/v1/agent/sessions", params={"novel_id": novel_id})).json()["data"]
        sess = next(s for s in sessions if s["id"] == sid)
        assert sess["subagent_ids"] == ["a1"]
        assert sess["skill_ids"] == [sk["id"]]
        assert sess["context_chapter_ids"] == ["c1", "c2"]

    async def test_update_context_audited(self, client, novel_id):
        sid = (await client.post("/api/v1/agent/sessions", json={"novel_id": novel_id})).json()["data"]["id"]
        await client.patch(f"/api/v1/agent/sessions/{sid}/context", json={"model_ref": "p:m"})
        logs = (await client.get("/api/v1/audit-logs", params={"action": "agent.session.context"})).json()["data"]
        assert logs["total"] >= 1
