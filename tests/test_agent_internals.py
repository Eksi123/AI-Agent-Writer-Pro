# -*- coding: utf-8 -*-
"""
模块: tests
文件名: test_agent_internals.py
说明: Agent 内部逻辑单元测试

    包含：
    - WriteApproval 高危判定（classify_risk / virtual_to_host / normalize_decision）
    - WorkspaceGuard 错误指纹记忆
    - 消息树 parent/branch 结构
    - Builder 装配（中间件顺序 + permissions + skills 路径）
    - Langfuse 降级
    - 内置工具与技能边界
"""

from __future__ import annotations

import pytest

import app.agent.more.langfuse as tracing
from app.agent.harness.approval.write_approval import (
    DEFAULT_EDIT_THRESHOLD,
    DEFAULT_OVERWRITE_THRESHOLD,
    _normalize_decision,
    classify_risk,
    virtual_to_host,
)
from app.agent.harness.guard.workspace_guard import WorkspaceGuardMiddleware
from app.agent.harness.sandbox.permissions import PERMISSION_RULES
from app.agent.loop.builder import SKILL_PATHS, build_middleware_stack
from app.agent.tracing.messages import add_message, load_tree, set_active_leaf
from app.core.config import get_settings
from app.core.paths import workspace_dir
from app.db.base import new_id
from app.db.models import AgentSession


class TestClassifyRisk:
    """WriteApproval 高危判定"""

    def test_delete_in_project_is_high_risk(self):
        kind, reason = classify_risk("delete", {"file_path": "/projects/abc/chapters/v1/c1.md"})
        assert kind == "delete"
        assert "不可逆" in reason

    def test_delete_outside_project_allowed(self):
        kind, _ = classify_risk("delete", {"file_path": "/conversation_history/x.md"})
        assert kind is None

    def test_large_edit_allowed(self):
        """大范围 edit 不再审批（写小说常规操作）。"""
        args = {
            "file_path": "/projects/abc/chapters/v1/c1.md",
            "old_string": "x" * (DEFAULT_EDIT_THRESHOLD + 1),
            "new_string": "y",
        }
        kind, _reason = classify_risk("edit_file", args)
        assert kind is None

    def test_small_edit_allowed(self):
        args = {"file_path": "/projects/abc/chapters/v1/c1.md", "old_string": "旧句", "new_string": "新句"}
        kind, _ = classify_risk("edit_file", args)
        assert kind is None

    def test_overwrite_existing_chapter_allowed(self, tmp_path, monkeypatch):
        """整章覆盖不再审批（生成/改写正文是常规操作）。"""
        pid = "testpid"
        ch = workspace_dir(pid) / "chapters" / "v1" / "c1.md"
        ch.parent.mkdir(parents=True, exist_ok=True)
        ch.write_text("---\ntitle: 第一章\n---\n" + "旧" * (DEFAULT_OVERWRITE_THRESHOLD + 10), encoding="utf-8")

        kind, _reason = classify_risk(
            "write_file", {"file_path": f"/projects/{pid}/chapters/v1/c1.md", "content": "全新内容"}
        )
        assert kind is None

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
    """虚拟路径 → 宿主路径映射"""

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
    """审批决策归一化"""

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


class TestErrorFingerprint:
    """WorkspaceGuard 错误指纹记忆"""

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


class TestMessageTree:
    """消息树 parent/branch 结构"""

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


class TestBuilderAssembly:
    """Builder 装配（中间件顺序 + permissions + skills 路径）"""

    def test_middleware_order(self):
        """中间件顺序必须符合手册（Retry → SwitchOnFailure → CallLimit → Todo → Recap → Guard → Notify → Approval）。"""

        class _DummyModel:
            model_name = "dummy"

        stack = build_middleware_stack(primary_model=_DummyModel(), alternate_models=[], recap_provider=None)
        names = [getattr(m, "name", type(m).__name__) for m in stack]

        def idx(key: str) -> int:
            return next(i for i, n in enumerate(names) if key.lower() in n.lower())

        assert idx("ModelRetry") < idx("ModelCallLimit") < idx("TodoList")
        assert idx("TodoList") < idx("RecapInjection") < idx("WorkspaceGuard")
        assert idx("WorkspaceGuard") < idx("FileChangeNotify")
        # WriteApproval 必须**最外层**（index 0）
        assert names[0] == "WriteApproval"

    def test_permission_rules_constant(self):
        assert len(PERMISSION_RULES) == 8
        assert PERMISSION_RULES[-1][1] == ["/**"] and PERMISSION_RULES[-1][2] == "deny"

    def test_skill_paths(self):
        # 技能 = 用户自建：只挂 /skills/user/
        assert SKILL_PATHS == ["/skills/user/"]


class TestTracingDegrade:
    """Langfuse 降级（未配置 Key 时 handler 为 None）"""

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


class TestBuiltinToolsAndSkills:
    """内置工具与技能边界（技能 = 用户自建）"""

    def test_package_has_no_builtin_skills_dir(self):
        """包内不再随包携带写作技能目录（tools/builtin 只放内置工具代码）。"""
        from pathlib import Path

        builtin_dir = Path(__file__).resolve().parents[1] / "src" / "app" / "agent" / "tools" / "builtin"
        assert not (builtin_dir / "skills").exists()

    def test_builtin_tool_dtos(self):
        """内置工具清单：read/write/edit/list/grep/glob/outline_grep/web_search，展示名与真实名对应。"""
        from app.services.skills import BUILTIN_TOOL_DTOS, BUILTIN_TOOL_NAMES

        labels = {d["label"] for d in BUILTIN_TOOL_DTOS}
        assert {"read", "edit", "list", "grep", "glob", "outline_grep", "web_search", "write"} <= labels
        assert "write_file" in BUILTIN_TOOL_NAMES
        assert "outline_grep" in BUILTIN_TOOL_NAMES
        # 展示顺序（写作技能页/子代理多选）：write 紧随 read，outline_grep 在 web_search 前
        assert [d["label"] for d in BUILTIN_TOOL_DTOS] == [
            "read", "write", "edit", "list", "grep", "glob", "outline_grep", "web_search",
        ]

    def test_subagent_tools_are_known_builtin_tools(self):
        """内置子代理预配的工具必须都在内置工具清单内（装配别名可解析）。"""
        from app.agent.loop.builder import _FS_TOOL_ALIASES
        from app.agent.subagents.builtin import BUILTIN_SUBAGENTS

        known = set(_FS_TOOL_ALIASES)
        for name, spec in BUILTIN_SUBAGENTS.items():
            unknown = [t for t in (spec.get("tools") or []) if t not in known]
            assert not unknown, f"{name} 预配了未知工具 {unknown}"
            # 技能绑定只允许用户自建路径（或为空）
            for sp in spec.get("skills") or []:
                assert sp.startswith("/skills/user/"), f"{name} 绑定了非用户技能 {sp}"
