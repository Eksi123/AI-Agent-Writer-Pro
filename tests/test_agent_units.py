# -*- coding: utf-8 -*-
"""
模块: tests
文件名: test_agent_units.py
作者: xionggaoqing
时间: 2026-10-27
说明: M3 中间件与模块单元测试（手册「十一」）。

    - WriteApprovalMiddleware 高危判定（整章覆盖 / 大范围 edit / delete）
    - WorkspaceGuardMiddleware 错误指纹记忆（2 次告警 / 3 次阻断）
    - 消息树 parent/branch 结构
    - builder 装配（中间件顺序 + permissions + skills 路径）
    - Langfuse 降级（未配置 Key 时 handler 为 None，不影响运行）
"""

from __future__ import annotations

import re

import pytest

import app.agent.tracing as tracing
from app.agent.loop.builder import SKILL_PATHS, build_middleware_stack
from app.agent.loop.middleware.workspace_guard import WorkspaceGuardMiddleware
from app.agent.loop.middleware.write_approval import (
    DEFAULT_EDIT_THRESHOLD,
    DEFAULT_OVERWRITE_THRESHOLD,
    _normalize_decision,
    classify_risk,
    virtual_to_host,
)
from app.agent.memory import add_message, load_tree, set_active_leaf
from app.agent.sandbox.backend import BUILTIN_SKILLS_DIR
from app.agent.sandbox.permissions import PERMISSION_RULES
from app.agent.subagents import BUILTIN_SUBAGENTS, build_subagents
from app.core.config import get_settings
from app.core.paths import workspace_dir
from app.db.base import new_id
from app.db.models import AgentSession

# ---------------------------------------------------------------------------
# WriteApproval 高危判定
# ---------------------------------------------------------------------------


class TestClassifyRisk:
    def test_delete_in_project_is_high_risk(self):
        kind, reason = classify_risk("delete", {"file_path": "/projects/abc/chapters/v1/c1.md"})
        assert kind == "delete"
        assert "不可逆" in reason

    def test_delete_outside_project_allowed(self):
        kind, _ = classify_risk("delete", {"file_path": "/conversation_history/x.md"})
        assert kind is None

    def test_large_edit_is_high_risk(self):
        args = {
            "file_path": "/projects/abc/chapters/v1/c1.md",
            "old_string": "x" * (DEFAULT_EDIT_THRESHOLD + 1),
            "new_string": "y",
        }
        kind, reason = classify_risk("edit_file", args)
        assert kind == "large_edit"
        assert "大范围改写" in reason

    def test_small_edit_allowed(self):
        args = {"file_path": "/projects/abc/chapters/v1/c1.md", "old_string": "旧句", "new_string": "新句"}
        kind, _ = classify_risk("edit_file", args)
        assert kind is None

    def test_overwrite_existing_chapter_is_high_risk(self, tmp_path, monkeypatch):
        """write_file 覆盖已存在的非空章节 → overwrite。"""

        # 构造已存在的项目章节文件

        pid = "testpid"
        ch = workspace_dir(pid) / "chapters" / "v1" / "c1.md"
        ch.parent.mkdir(parents=True, exist_ok=True)
        ch.write_text("---\ntitle: 第一章\n---\n" + "旧" * (DEFAULT_OVERWRITE_THRESHOLD + 10), encoding="utf-8")

        kind, reason = classify_risk(
            "write_file", {"file_path": f"/projects/{pid}/chapters/v1/c1.md", "content": "全新内容"}
        )
        assert kind == "overwrite"
        assert "整章覆盖" in reason

    def test_write_new_file_allowed(self):
        kind, _ = classify_risk(
            "write_file", {"file_path": "/projects/nonexistent-pid/chapters/v1/c9.md", "content": "x" * 1000}
        )
        assert kind is None

    def test_non_file_tools_allowed(self):
        for tool in ("ls", "read_file", "glob", "grep", "web_search"):
            kind, _ = classify_risk(tool, {"path": "/projects/abc"})
            assert kind is None


class TestVirtualToHost:
    def test_maps_project_path(self, tmp_path, monkeypatch):
        monkeypatch.setenv("DATA_ROOT", str(tmp_path))

        get_settings.cache_clear()
        p = virtual_to_host("/projects/pid1/chapters/v1/c1.md")
        assert p is not None
        assert str(p).replace("\\", "/").endswith("workspaces/pid1/chapters/v1/c1.md")

    def test_rejects_traversal(self):
        assert virtual_to_host("/projects/../../etc") is None
        assert virtual_to_host("/skills/builtin/x.md") is None
        assert virtual_to_host("") is None


class TestNormalizeDecision:
    def test_batched_unwrap(self):
        record = _normalize_decision({"decisions": [{"type": "reject", "message": "别删"}]})
        assert record["decision"] == "reject"
        assert record["message"] == "别删"

    def test_edit_extracts_args_from_edited_action(self):
        record = _normalize_decision(
            {"decisions": [{"type": "edit", "edited_action": {"name": "delete", "args": {"file_path": "/a"}}}]}
        )
        assert record["decision"] == "edit"
        assert record["edited_args"] == {"file_path": "/a"}

    def test_plain_dict(self):
        assert _normalize_decision({"type": "approve"})["decision"] == "approve"

    def test_string(self):
        assert _normalize_decision("approve")["decision"] == "approve"

    def test_unknown_defaults_approve(self):
        assert _normalize_decision(42)["decision"] == "approve"


# ---------------------------------------------------------------------------
# WorkspaceGuard 错误指纹
# ---------------------------------------------------------------------------


class TestErrorFingerprint:
    def test_second_failure_warns_third_blocks(self):

        mw = WorkspaceGuardMiddleware()
        args = {"file_path": "/projects/x/f.md"}
        err = "文件不存在"

        assert mw.note_error("write_file", args, err) is None  # 第 1 次：放行
        warn = mw.note_error("write_file", args, err)  # 第 2 次：告警
        assert warn is not None and "警告" in warn
        block = mw.note_error("write_file", args, err)  # 第 3 次：阻断
        assert block is not None and "阻断" in block
        # 阻断后持续阻断
        again = mw.note_error("write_file", args, err)
        assert again is not None and "阻断" in again

    def test_different_args_isolated(self):

        mw = WorkspaceGuardMiddleware()
        mw.note_error("write_file", {"file_path": "/a"}, "err")
        mw.note_error("write_file", {"file_path": "/a"}, "err")
        # 不同参数 → 独立计数，第 1 次仍放行
        assert mw.note_error("write_file", {"file_path": "/b"}, "err") is None

    def test_different_error_isolated(self):

        mw = WorkspaceGuardMiddleware()
        mw.note_error("write_file", {"file_path": "/a"}, "err1")
        mw.note_error("write_file", {"file_path": "/a"}, "err1")
        assert mw.note_error("write_file", {"file_path": "/a"}, "err2") is None

    def test_check_path_cross_project(self):

        mw = WorkspaceGuardMiddleware()
        assert mw.check_path("/projects/other/novel.yaml", "write", "current") is not None
        assert mw.check_path("/projects/current/novel.yaml", "write", "current") is None


# ---------------------------------------------------------------------------
# 消息树
# ---------------------------------------------------------------------------


class TestMessageTree:
    async def test_parent_and_branch_structure(self, session, novel_id):

        sid = new_id()
        session.add(AgentSession(id=sid, project_id=novel_id, title="t"))
        await session.flush()

        # 首条消息：active_leaf 为 None → root（parent_id=None）
        m1 = await add_message(session, session_id=sid, role="user", content="第一问")
        assert m1.parent_id is None and m1.branch_index == 0

        # 第二条：parent_id=None → 接 active_leaf(m1)
        m2 = await add_message(session, session_id=sid, role="assistant", content="第一答")
        assert m2.parent_id == m1.id

        # 第三条：接 active_leaf(m2)
        m3 = await add_message(session, session_id=sid, role="user", content="第二问")
        assert m3.parent_id == m2.id
        await session.flush()

        tree = await load_tree(session, sid)
        assert tree["total"] == 3
        assert tree["active_leaf_id"] == m3.id
        by_id = {m["id"]: m for m in tree["items"]}
        assert by_id[m2.id]["parent_id"] == m1.id
        assert by_id[m1.id]["in_active_path"] is True

        # 兄弟分支：显式指定 parent_id=m1（重新生成「第一答」）
        m2b = await add_message(session, session_id=sid, role="assistant", content="第一答改", parent_id=m1.id)
        await session.flush()
        assert m2b.parent_id == m1.id
        assert m2b.branch_index == 1  # m2 是 0，m2b 是兄弟 1

        tree = await load_tree(session, sid)
        siblings = [m for m in tree["items"] if m["parent_id"] == m1.id]
        assert len(siblings) == 2
        assert {m["branch_index"] for m in siblings} == {0, 1}
        assert all(m["branch_count"] == 2 for m in siblings)

        # 切分支到 m2（旧版本）：活跃路径 = root→m2，即 m1 → m2
        await set_active_leaf(session, sid, m2.id)
        tree = await load_tree(session, sid)
        active = {m["id"] for m in tree["items"] if m["in_active_path"]}
        assert active == {m1.id, m2.id}
        assert m2b.id not in active  # 兄弟分支不在活跃路径
        assert m3.id not in active  # m3 是 m2 的后代，不在 root→m2 路径上

    async def test_set_active_leaf_validates_session(self, session, novel_id):

        sid = new_id()
        session.add(AgentSession(id=sid, project_id=novel_id, title="t"))
        await session.flush()
        with pytest.raises(ValueError):
            await set_active_leaf(session, sid, "no-such-message")


# ---------------------------------------------------------------------------
# builder 装配
# ---------------------------------------------------------------------------


class TestBuilderAssembly:
    def test_middleware_order(self):
        """中间件顺序必须符合手册（Retry → Fallback → CallLimit → Todo → Recap → Guard → Notify → Approval）。"""

        class _DummyModel:
            model_name = "dummy"

        stack = build_middleware_stack(primary_model=_DummyModel(), fallback_models=[], recap_provider=None)
        names = [getattr(m, "name", type(m).__name__) for m in stack]

        def idx(key: str) -> int:
            return next(i for i, n in enumerate(names) if key.lower() in n.lower())

        assert idx("ModelRetry") < idx("ModelCallLimit") < idx("TodoList")
        assert idx("TodoList") < idx("RecapInjection") < idx("WorkspaceGuard")
        assert idx("WorkspaceGuard") < idx("FileChangeNotify") < idx("WriteApproval")
        # WriteApproval 必须最后（参数级审批在链尾）
        assert "WriteApproval" in names[-1]

    def test_permission_rules_constant(self):

        assert len(PERMISSION_RULES) == 8
        assert PERMISSION_RULES[-1][1] == ["/**"] and PERMISSION_RULES[-1][2] == "deny"

    def test_skill_paths(self):

        assert SKILL_PATHS == ["/skills/builtin/", "/skills/user/"]


class TestSubagents:
    def test_build_subagents_all(self):

        agents = build_subagents()
        names = [a["name"] for a in agents]
        assert names == list(BUILTIN_SUBAGENTS.keys())
        assert set(names) == {"planner", "writer", "editor", "continuity-checker", "researcher"}
        for a in agents:
            assert a["mode"] == "isolated"
            assert a["description"] and a["system_prompt"]

    def test_fork_mode_clears_skills(self):

        agents = build_subagents(prompt_overrides={})
        writer = next(a for a in agents if a["name"] == "writer")
        assert writer["skills"] == ["/skills/builtin/prose-writing"]

    def test_enabled_filter(self):

        agents = build_subagents(enabled_names={"writer", "planner"})
        assert {a["name"] for a in agents} == {"writer", "planner"}

    def test_model_override(self):

        sentinel = object()
        agents = build_subagents(
            resolved_models={"p:m": sentinel}, model_overrides={"writer": "p:m"}
        )
        writer = next(a for a in agents if a["name"] == "writer")
        assert writer["model"] is sentinel


# ---------------------------------------------------------------------------
# Langfuse 降级
# ---------------------------------------------------------------------------


class TestTracingDegrade:
    def test_no_client_without_keys(self, monkeypatch):
        monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "")
        monkeypatch.setenv("LANGFUSE_SECRET_KEY", "")

        get_settings.cache_clear()

        tracing.reset_client()
        assert tracing.get_client() is None
        assert tracing.build_callback_handler() is None

        # observation 为 no-op，不抛异常
        with tracing.observation("test") as obs:
            assert obs is None
        tracing.create_score("x", 1)  # 静默
        assert tracing.current_trace_id() is None
        get_settings.cache_clear()


# ---------------------------------------------------------------------------
# 内置技能合规
# ---------------------------------------------------------------------------


class TestBuiltinSkills:
    def test_all_skills_have_valid_frontmatter(self):


        name_re = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
        dirs = [d for d in BUILTIN_SKILLS_DIR.iterdir() if d.is_dir()]
        assert len(dirs) >= 8

        for d in dirs:
            skill_md = d / "SKILL.md"
            assert skill_md.exists(), f"{d.name} 缺 SKILL.md"
            text = skill_md.read_text(encoding="utf-8")
            m = re.match(r"^---\n(.*?)\n---\n", text, re.S)
            assert m, f"{d.name} frontmatter 格式错误"
            fm = m.group(1)
            name = re.search(r"^name:\s*(.+)$", fm, re.M).group(1).strip()
            desc = re.search(r"^description:\s*(.+)$", fm, re.M).group(1).strip()
            # name 必须等于目录名、小写连字符、≤64
            assert name == d.name, f"{d.name}: name={name} 与目录名不符"
            assert name_re.match(name) and len(name) <= 64
            assert 0 < len(desc) <= 1024
            assert skill_md.stat().st_size < 10 * 1024 * 1024

    def test_subagent_skill_paths_exist(self):

        for name, spec in BUILTIN_SUBAGENTS.items():
            for sp in spec.get("skills") or []:
                rel = sp.replace("/skills/builtin/", "")
                assert (BUILTIN_SKILLS_DIR / rel / "SKILL.md").exists(), f"{name} 引用了不存在的技能 {sp}"
