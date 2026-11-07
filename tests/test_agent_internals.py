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
        assert "不可恢复" in reason
        # 人话文案：不暴露内部路径（无工作区时回落「某一章」，绝不编造序号/名称）
        assert "c1.md" not in reason
        assert "某一章的正文" in reason

    def test_humanize_virtual_path_fallback_without_workspace(self):
        from app.agent.harness.approval.write_approval import humanize_virtual_path

        # 项目工作区不存在 → 序号/名称读不到 → 省略，绝不编造
        assert humanize_virtual_path("/projects/p/chapters/v2/c3.md") == "某一章的正文"
        assert humanize_virtual_path("/projects/p/chapters/v2/") == "某一卷的全部章节"
        assert humanize_virtual_path("/projects/p/outline/x-大纲.md") == "小说大纲"
        assert humanize_virtual_path("/projects/p/summaries/ch-5.md") == "章节摘要"
        assert humanize_virtual_path("/projects/p/volumes.yaml") == "卷目录"
        assert humanize_virtual_path("/projects/p/novel.yaml") == "书籍信息"

    def test_humanize_uses_real_ordinals_and_titles(self, tmp_path, monkeypatch):
        """序号取 volumes.yaml / frontmatter 的 sort_order，名称取各自 title（目录名是短 ID 非序号）。"""
        from app.agent.harness.approval.write_approval import humanize_virtual_path
        from app.services import files

        monkeypatch.setenv("DATA_ROOT", str(tmp_path))
        get_settings.cache_clear()
        pid = "proj1"
        files.write_volumes(
            pid,
            [
                {"id": "aa", "title": "谪凡间", "summary": "", "sort_order": 1},
                {"id": "bb", "title": "醉锦城", "summary": "", "sort_order": 2},
            ],
        )
        files.write_chapter(pid, "bb", "cc", {"title": "夜宴", "sort_order": 3}, "正文。")

        assert humanize_virtual_path(f"/projects/{pid}/chapters/bb/cc.md") == "第二卷第三章《夜宴》的正文"
        assert humanize_virtual_path(f"/projects/{pid}/chapters/bb/") == "第二卷《醉锦城》的全部章节"
        reason = classify_risk("delete", {"file_path": f"/projects/{pid}/chapters/bb/cc.md"})[1]
        assert reason == "删除第二卷第三章《夜宴》的正文，删除后不可恢复"

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


class TestResumeInputFor:
    """resume 路由（lifecycle.resume_input_for）——★批准绝不能流向另一个高危写入。

    回归：并行工具调用下某个任务的 `interrupt()` 可能被取消（审批卡已建、中断未落
    checkpoint）。用户批准这张「幽灵卡」时，若退回标量（langgraph 记为全局
    null_resume）或「只有一个挂起就给它」，该决定会被**另一个**待批的删除操作当成
    自己的答复直接执行——未经用户批准就删了章节。
    """

    class _FakeIntr:
        def __init__(self, iid: str, tool_call_id: str) -> None:
            self.id = iid
            self.value = {"kind": "writer_approval", "tool_call_id": tool_call_id}

    @pytest.fixture(autouse=True)
    async def _bootstrap_db(self, session):
        """初始化临时库（resume_input_for 内部用 session_scope 读写中断行）。"""
        return session

    class _FakeTask:
        def __init__(self, intrs: list) -> None:
            self.interrupts = intrs

    class _FakeSnap:
        def __init__(self, tasks: list) -> None:
            self.tasks = tasks

    class _FakeGraph:
        def __init__(self, tasks: list) -> None:
            self._tasks = tasks

        async def aget_state(self, config):  # noqa: ARG002
            return TestResumeInputFor._FakeSnap(self._tasks)

    async def _add_row(self, run_id: str, session_id: str, tool_call_id: str, *, decided: bool) -> str:
        import json

        from app.db.base import utcnow
        from app.db.models import AgentInterrupt
        from app.db.session import session_scope

        iid = new_id()
        async with session_scope() as db:
            db.add(
                AgentInterrupt(
                    id=iid,
                    run_id=run_id,
                    session_id=session_id,
                    type="approval",
                    tool_name="delete",
                    payload_json=json.dumps({"tool_call_id": tool_call_id}),
                    decision="approve" if decided else None,
                    response_json=json.dumps({"decision": "approve"}) if decided else None,
                    decided_by="user" if decided else None,
                    decided_at=utcnow() if decided else None,
                    created_at=utcnow(),
                )
            )
        return iid

    async def test_phantom_card_approval_does_not_leak_to_pending_sibling(self):
        """批准「幽灵卡」时，另一张仍待批的卡必须保持挂起（失败关闭）。"""
        from app.agent.loop.lifecycle import _UNMATCHED_RESUME_KEY, resume_input_for

        run_id, session_id = new_id(), new_id()
        # c1 的卡已决（幽灵卡：其中断不在 checkpoint 里）；c2 的卡仍未决
        ghost_id = await self._add_row(run_id, session_id, "c1", decided=True)
        await self._add_row(run_id, session_id, "c2", decided=False)
        graph = self._FakeGraph([self._FakeTask([self._FakeIntr("f" * 32, "c2")])])

        out = await resume_input_for(run_id, ghost_id, {"decision": "approve"}, graph=graph, config={})
        # 绝不能把 c1 的批准投递给 c2 → 只放一个匹配不到任何命名空间的占位键
        assert out == {_UNMATCHED_RESUME_KEY: {"decision": "approve"}}

    async def test_matched_interrupt_gets_its_own_decision(self):
        """正常路径：按 langgraph interrupt id 定向投递本次决定。"""
        from app.agent.loop.lifecycle import resume_input_for

        run_id, session_id = new_id(), new_id()
        iid = await self._add_row(run_id, session_id, "c1", decided=True)
        graph = self._FakeGraph([self._FakeTask([self._FakeIntr("a" * 32, "c1")])])

        out = await resume_input_for(run_id, iid, {"decision": "approve"}, graph=graph, config={})
        assert out == {"a" * 32: {"decision": "approve"}}

    async def test_decided_sibling_delivered_undecided_stays_pending(self):
        """多中断挂起：本次决定 + 已决兄弟一并送达，未决兄弟保持挂起。"""
        from app.agent.loop.lifecycle import resume_input_for

        run_id, session_id = new_id(), new_id()
        await self._add_row(run_id, session_id, "c1", decided=True)
        await self._add_row(run_id, session_id, "c3", decided=True)
        await self._add_row(run_id, session_id, "c2", decided=False)
        graph = self._FakeGraph(
            [
                self._FakeTask([self._FakeIntr("a" * 32, "c1")]),
                self._FakeTask([self._FakeIntr("b" * 32, "c2")]),
                self._FakeTask([self._FakeIntr("c" * 32, "c3")]),
            ]
        )
        iid = (await self._rows_by_tool_call(run_id))["c1"]

        out = await resume_input_for(run_id, iid, {"decision": "reject"}, graph=graph, config={})
        assert set(out) == {"a" * 32, "c" * 32}  # c2 未决 → 不在映射里
        assert out["a" * 32] == {"decision": "reject"}
        assert out["c" * 32] == {"decision": "approve"}  # 已决兄弟用其落库 response

    async def _rows_by_tool_call(self, run_id: str) -> dict[str, str]:
        import json

        from sqlalchemy import select

        from app.db.models import AgentInterrupt
        from app.db.session import session_scope

        async with session_scope() as db:
            rows = (
                await db.execute(select(AgentInterrupt).where(AgentInterrupt.run_id == run_id))
            ).scalars().all()
        return {json.loads(r.payload_json or "{}")["tool_call_id"]: r.id for r in rows}

    async def test_no_pending_interrupt_returns_scalar(self):
        """checkpoint 无挂起中断（重放已消化）→ 沿用标量，避免无人可投的空映射。"""
        from app.agent.loop.lifecycle import resume_input_for

        run_id, session_id = new_id(), new_id()
        iid = await self._add_row(run_id, session_id, "c1", decided=True)
        graph = self._FakeGraph([])

        out = await resume_input_for(run_id, iid, {"decision": "approve"}, graph=graph, config={})
        assert out == {"decision": "approve"}


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

        assert idx("ModelCallBudget") < idx("ModelRetry") < idx("TodoList")
        assert idx("TodoList") < idx("RecapInjection") < idx("WorkspaceGuard")
        assert idx("WorkspaceGuard") < idx("FileChangeNotify")
        # 两层保险第二层：ContextGuard 在 HiddenContext 之后、SteerInjection 之前
        assert idx("HiddenContext") < idx("ContextGuard") < idx("SteerInjection")
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
