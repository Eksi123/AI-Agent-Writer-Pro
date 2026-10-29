# -*- coding: utf-8 -*-
"""
模块: tests
文件名: test_subagents_skills.py
作者: xionggaoqing
时间: 2026-10-27
说明: 子代理与技能管理测试（M5）。

    - 子代理：内置播种、CRUD、fork+skills 校验、内置保护（不可删/不可改名/可恢复出厂）
    - 技能：CRUD + 物化到 data/skills/{name}/SKILL.md、停用即移除物化、
      frontmatter name=目录名、与内置技能重名拒绝
"""

from __future__ import annotations

from app.core.paths import skills_root

# ---------------------------------------------------------------------------
# 子代理
# ---------------------------------------------------------------------------


class TestSubagentsApi:
    async def test_builtin_seeded_on_startup(self, client):
        """应用启动播种 5 个内置子代理。"""
        resp = await client.get("/api/v1/subagents")
        data = resp.json()["data"]
        names = {s["name"] for s in data}
        assert {"planner", "writer", "editor", "continuity-checker", "researcher"} <= names
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
                "skills": ["/skills/builtin/prose-writing"],
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
            json={"name": "combo", "description": "d", "skills": ["/skills/builtin/naming"]},
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
# 技能
# ---------------------------------------------------------------------------


class TestSkillsApi:
    async def test_list_includes_builtin(self, client):
        """v1028：内置「技能」= Agent 基础工具（read/edit/list/grep/glob/web_search），
        不展示描述；原内置写作技能目录不再在列表中呈现。"""
        data = (await client.get("/api/v1/skills")).json()["data"]
        builtin = [s for s in data if s["is_builtin"]]
        names = {s["name"] for s in builtin}
        assert {"read", "edit", "list", "grep", "glob", "web_search"} <= names
        assert {"prose-writing", "novel-planning"} & names == set()
        # 内置工具不展示描述
        assert all(s["description"] == "" for s in builtin)
        # 每个内置项都带真实工具名，供子代理多选装配
        tool_names = {s["tool_name"] for s in builtin}
        assert {"read_file", "edit_file", "ls", "grep", "glob", "web_search"} <= tool_names
        assert all(s["materialized"] for s in builtin)

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

    async def test_builtin_name_conflict_rejected(self, client):
        resp = await client.post(
            "/api/v1/skills", json={"name": "prose-writing", "description": "抢注内置名"}
        )
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
        await client.post("/api/v1/skills", json={"name": "repair-me", "description": "d"})
        # 模拟目录丢失
        import shutil

        shutil.rmtree(skills_root() / "repair-me", ignore_errors=True)
        assert not (skills_root() / "repair-me" / "SKILL.md").exists()

        resp = await client.post("/api/v1/skills/resync")
        assert resp.json()["code"] == 0
        assert resp.json()["data"]["materialized"] >= 1
        assert (skills_root() / "repair-me" / "SKILL.md").exists()
