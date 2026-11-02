# -*- coding: utf-8 -*-
"""
模块: tests
文件名: test_workflows.py
作者: xionggaoqing
时间: 2026-11-02
说明: 架构优化新增能力测试：

    - 技能 = 用户自建：backend 只挂 `/skills/user/`（+会话精选），无 `/skills/builtin/` 路由；
      内置子代理按职能预配默认内置工具（DB tools_json 回落 spec）；
    - workflows：导出预检（纯规则）/ 全书一致性审查（假模型驱动）。
"""

from __future__ import annotations

from langchain_core.messages import AIMessage

from app.agent.harness.sandbox.backend import build_backend
from app.agent.workflows.novel_export.preflight import run_export_preflight


class TestSkillsAreUserOwned:
    async def test_backend_has_no_builtin_skills_route(self):
        """`/skills/builtin/` 路由已取消：技能 = 用户自建，只挂 `/skills/user/`。"""
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

    async def test_user_skill_enable_controls_materialization(self, client):
        """用户技能启用开关控制物化（停用 → 移除 data/skills/{name}/）。"""
        from app.core.paths import skills_root

        resp = await client.post(
            "/api/v1/skills", json={"name": "wf-skill", "description": "d", "body": "b"}
        )
        pk = resp.json()["data"]["id"]
        assert (skills_root() / "wf-skill" / "SKILL.md").exists()

        await client.patch(f"/api/v1/skills/{pk}", json={"enabled": False})
        assert not (skills_root() / "wf-skill").exists()

        await client.patch(f"/api/v1/skills/{pk}", json={"enabled": True})
        assert (skills_root() / "wf-skill" / "SKILL.md").exists()


class TestExportPreflight:
    async def test_no_chapters_blocks(self, client, session, novel_id):
        result = await run_export_preflight(session, novel_id, "epub")
        assert result["passed"] is False
        assert any(c["code"] == "no_chapters" for c in result["checks"])

    async def test_empty_and_draft_warnings(self, client, session, novel_id, chapter_id):
        result = await run_export_preflight(session, novel_id, "markdown")
        assert result["passed"] is True  # 只有警告，无阻断
        codes = {c["code"] for c in result["checks"]}
        # fixture 章节正文很短（<50 字）→ short_chapters；未定稿 → draft_chapters
        assert "short_chapters" in codes or "empty_chapters" in codes

    async def test_mobi_without_calibre_blocks(self, client, session, novel_id, chapter_id, monkeypatch):
        import app.services.exporters as exporters

        monkeypatch.setattr(exporters, "calibre_available", lambda: False)
        result = await run_export_preflight(session, novel_id, "mobi")
        assert result["passed"] is False
        assert any(c["code"] == "calibre_missing" for c in result["checks"])

    async def test_api_endpoint(self, client, novel_id):
        resp = await client.get(f"/api/v1/novels/{novel_id}/export-preflight", params={"format": "epub"})
        assert resp.json()["code"] == 0
        assert "passed" in resp.json()["data"]


class TestBookConsistencyAudit:
    async def test_skipped_without_model(self, client, session, novel_id, chapter_id):
        """有章节但未配置模型 → skipped（带 reason，不抛错）。"""
        from app.agent.workflows.book_consistency.audit import run_book_consistency_audit

        result = await run_book_consistency_audit(session, novel_id)
        assert result["status"] == "skipped"
        assert "未配置可用模型" in result["reason"]

    async def test_skipped_without_chapters(self, client, session, novel_id):
        """无章节时 skipped（章节检查先于模型解析，无需配模型）。"""
        from app.agent.workflows.book_consistency.audit import run_book_consistency_audit

        result = await run_book_consistency_audit(session, novel_id)
        assert result["status"] == "skipped"
        assert result["reason"] == "尚无章节"

    async def test_audit_with_fake_model(self, client, session, novel_id, volume_id, chapter_id):
        """假模型返回结构化问题 → 汇总排序 + 报告落盘。"""
        from tests.fake_model import ScriptedFakeChatModel

        from app.agent.loop.llm.factory import register_model_override
        from app.agent.workflows.book_consistency.audit import run_book_consistency_audit
        from app.services.llm import create_model, create_provider

        provider = await create_provider(
            session, name="审计测试厂商2", type="openai_compatible", api_key_env=None
        )
        model = await create_model(
            session, provider_id=provider["id"], model_id="fake-audit2", is_default=True
        )
        fake = ScriptedFakeChatModel(
            responses=[
                AIMessage(
                    content=(
                        '{"issues": [{"level": "suggestion", "chapter": "拾荒少年的清晨",'
                        ' "type": "timeline", "detail": "季节描写前后不一", "advice": "统一为初春"}],'
                        ' "summary": "总体连贯"}'
                    )
                )
            ]
        )
        register_model_override(model["model_ref"], fake)

        result = await run_book_consistency_audit(session, novel_id)
        assert result["status"] == "done"
        assert result["volumes_checked"] >= 1
        assert len(result["issues"]) == 1
        assert result["issues"][0]["type"] == "timeline"

        from app.core.paths import workspace_dir

        report = workspace_dir(novel_id) / "outline" / "consistency-audit.md"
        assert report.exists()
        text = report.read_text(encoding="utf-8")
        assert "一致性审查报告" in text
        assert "季节描写前后不一" in text
