# -*- coding: utf-8 -*-
"""
模块: tests
文件名: test_skills.py
说明: 技能管理测试

    包含：
    - 技能 API 测试（创建、更新、删除、启用控制）
    - 会话技能物化测试
    - 技能物化控制测试
"""

from __future__ import annotations

from app.core.paths import session_skills_dir, skills_root
from app.services.skills import materialize_session_skills


class TestSkillsApi:
    async def test_list_includes_builtin_tools_only(self, client):
        """内置项 = Agent 基础工具（read/write/edit/list/grep/glob/web_search），
        不展示描述；技能 = 用户自建，包内不再随包携带写作技能。"""
        data = (await client.get("/api/v1/skills")).json()["data"]
        builtin = [s for s in data if s["is_builtin"]]
        names = {s["name"] for s in builtin}
        assert {"read", "edit", "list", "grep", "glob", "web_search", "write"} <= names
        # 内置工具不展示描述
        assert all(s["description"] == "" for s in builtin)
        # 每个内置项都带真实工具名，供子代理多选装配
        tool_names = {s["tool_name"] for s in builtin}
        assert {"read_file", "edit_file", "ls", "grep", "glob", "web_search", "write_file"} <= tool_names
        assert all(s["materialized"] for s in builtin)
        assert all(s["kind"] == "tool" for s in builtin)
        # 没有任何「包内写作技能」类别
        assert not [s for s in data if s.get("kind") == "writing" and s["is_builtin"]]

    async def test_picker_lists_only_web_search_builtin(self, client):
        """`?picker=true` 内置项只列 web_search（会话级写作技能），其余基础工具不出现。"""
        data = (await client.get("/api/v1/skills", params={"picker": True})).json()["data"]
        builtin = [s for s in data if s["is_builtin"]]
        assert [s["name"] for s in builtin] == ["web_search"]
        assert builtin[0]["id"] == "builtin:web_search"
        assert builtin[0]["session_builtin"] is True
        # 默认列表里 web_search 也带 session_builtin 标记（前端据此进浮层）
        default = (await client.get("/api/v1/skills")).json()["data"]
        ws = next(s for s in default if s["name"] == "web_search")
        assert ws["session_builtin"] is True
        assert next(s for s in default if s["name"] == "read")["session_builtin"] is False

    async def test_create_skill_materializes(self, client):
        resp = await client.post(
            "/api/v1/skills",
            json={
                "name": "my-craft",
                "description": "自定义写作技巧：对话张力营造。",
                "body": "## 技巧\n1. 潜台词\n2. 节奏控制",
            },
        )
        assert resp.json()["code"] == 0
        dto = resp.json()["data"]
        assert dto["materialized"] is True

        # 物化文件存在且 frontmatter name = 目录名
        path = skills_root() / "my-craft" / "SKILL.md"
        assert path.exists()
        text = path.read_text(encoding="utf-8")
        assert text.startswith("---\n")
        assert "name: my-craft" in text
        assert "description: 自定义写作技巧" in text
        assert "潜台词" in text

    async def test_invalid_skill_name_rejected(self, client):
        for bad in ["My-Skill", "9x", "a_b", ""]:
            resp = await client.post("/api/v1/skills", json={"name": bad, "description": "d"})
            assert resp.json()["code"] != 0, bad

    async def test_empty_description_rejected(self, client):
        resp = await client.post("/api/v1/skills", json={"name": "no-desc", "description": "  "})
        assert resp.json()["code"] == 40001

    async def test_disable_removes_materialization(self, client):
        await client.post("/api/v1/skills", json={"name": "toggle-me", "description": "d"})
        pk = (await client.get("/api/v1/skills")).json()["data"]
        pk = next(s["id"] for s in pk if s["name"] == "toggle-me")

        assert (skills_root() / "toggle-me" / "SKILL.md").exists()
        resp = await client.patch(f"/api/v1/skills/{pk}", json={"enabled": False})
        assert resp.json()["data"]["materialized"] is False
        assert not (skills_root() / "toggle-me" / "SKILL.md").exists()

        resp = await client.patch(f"/api/v1/skills/{pk}", json={"enabled": True})
        assert (skills_root() / "toggle-me" / "SKILL.md").exists()

    async def test_update_skill_rewrites_file(self, client):
        await client.post("/api/v1/skills", json={"name": "edit-me", "description": "初版描述"})
        pk = next(s["id"] for s in (await client.get("/api/v1/skills")).json()["data"] if s["name"] == "edit-me")

        resp = await client.patch(
            f"/api/v1/skills/{pk}",
            json={"description": "二版描述", "body": "新正文内容", "allowed_tools": ["grep"]},
        )
        assert resp.json()["code"] == 0
        text = (skills_root() / "edit-me" / "SKILL.md").read_text(encoding="utf-8")
        assert "二版描述" in text
        assert "新正文内容" in text
        assert "grep" in text
        assert resp.json()["data"]["allowed_tools"] == ["grep"]

    async def test_rename_skill_moves_directory(self, client):
        await client.post("/api/v1/skills", json={"name": "old-name", "description": "d"})
        pk = next(s["id"] for s in (await client.get("/api/v1/skills")).json()["data"] if s["name"] == "old-name")

        resp = await client.patch(f"/api/v1/skills/{pk}", json={"name": "new-name"})
        assert resp.json()["code"] == 0
        assert not (skills_root() / "old-name").exists()
        assert (skills_root() / "new-name" / "SKILL.md").exists()
        text = (skills_root() / "new-name" / "SKILL.md").read_text(encoding="utf-8")
        assert "name: new-name" in text

    async def test_delete_skill(self, client):
        await client.post("/api/v1/skills", json={"name": "doomed", "description": "d"})
        pk = next(s["id"] for s in (await client.get("/api/v1/skills")).json()["data"] if s["name"] == "doomed")
        resp = await client.delete(f"/api/v1/skills/{pk}")
        assert resp.json()["data"]["deleted"] is True
        assert not (skills_root() / "doomed").exists()

    async def test_builtin_skill_readonly(self, client):
        data = (await client.get("/api/v1/skills")).json()["data"]
        builtin = next(s for s in data if s["is_builtin"])
        # 内置技能 id 是 builtin:{name} 虚拟标识，PATCH/DELETE 走 DB 查不到 → 404
        resp = await client.delete(f"/api/v1/skills/{builtin['id']}")
        assert resp.json()["code"] == 40401

    async def test_resync_repairs_missing_dir(self, client):
        import shutil
        await client.post("/api/v1/skills", json={"name": "repair-me", "description": "d"})
        # 模拟目录丢失
        shutil.rmtree(skills_root() / "repair-me", ignore_errors=True)
        assert not (skills_root() / "repair-me" / "SKILL.md").exists()

        resp = await client.post("/api/v1/skills/resync")
        assert resp.json()["code"] == 0
        assert resp.json()["data"]["materialized"] >= 1
        assert (skills_root() / "repair-me" / "SKILL.md").exists()


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


class TestSkillsMaterializationControl:
    async def test_user_skill_enable_controls_materialization(self, client):
        """用户技能启用开关控制物化（停用 → 移除 data/skills/{name}/）。"""
        resp = await client.post(
            "/api/v1/skills", json={"name": "wf-skill", "description": "d", "body": "b"}
        )
        pk = resp.json()["data"]["id"]
        assert (skills_root() / "wf-skill" / "SKILL.md").exists()

        await client.patch(f"/api/v1/skills/{pk}", json={"enabled": False})
        assert not (skills_root() / "wf-skill").exists()

        await client.patch(f"/api/v1/skills/{pk}", json={"enabled": True})
        assert (skills_root() / "wf-skill" / "SKILL.md").exists()


class TestSkillsAreUserOwned:
    """技能 = 用户自建：backend 只挂 `/skills/user/`（+会话精选），无 `/skills/builtin/` 路由"""

    async def test_backend_has_no_builtin_skills_route(self):
        """`/skills/builtin/` 路由已取消：技能 = 用户自建，只挂 `/skills/user/`。"""
        from app.agent.harness.sandbox.backend import build_backend

        backend = build_backend()
        routes = getattr(backend, "routes", None) or getattr(backend, "_routes", {})
        keys = [str(k) for k in routes]
        assert not any(k.startswith("/skills/builtin") for k in keys)
        assert any(k.startswith("/skills/user") for k in keys)

    async def test_builtin_subagents_have_default_tools(self, client, session):
        """内置子代理按职能预配默认内置工具（DB 播种行 tools_json 非空）。"""
        subs = (await client.get("/api/v1/subagents")).json()["data"]
        by_name = {s["name"]: s for s in subs}
        assert {"read_file", "write_file", "edit_file"} <= set(by_name["writer"]["tools"])
        assert {"read_file", "write_file", "edit_file"} <= set(by_name["planner"]["tools"])
        assert set(by_name["reviewer"]["tools"]) == {"read_file", "ls", "grep", "glob"}
        assert by_name["web-searcher"]["tools"] == ["web_search"]

    async def test_load_specs_falls_back_to_builtin_tools(self, client, session):
        """DB 行 tools_json 为空时回落内置 spec 的默认工具（老库兼容）。"""
        from sqlalchemy import select

        from app.agent.subagents.user import load_specs
        from app.db.models import SubagentConfig
        from app.db.session import session_scope

        async with session_scope() as db:
            row = (
                await db.execute(select(SubagentConfig).where(SubagentConfig.name == "writer"))
            ).scalar_one()
            row.tools_json = None
            await db.commit()

        specs = await load_specs(session)
        writer = next(s for s in specs if s["name"] == "writer")
        assert "write_file" in writer["tools"]
