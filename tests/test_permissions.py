# -*- coding: utf-8 -*-
"""
模块: tests
文件名: test_permissions.py
作者: xionggaoqing
时间: 2026-10-27
说明: 权限矩阵与顺序回归测试（手册「十一 · 权限矩阵 / WorkspaceGuard」）。

    8 条规则逐条越权用例 + 规则顺序回归（断言常量数组与手册表逐行一致）。
"""

from __future__ import annotations

import pytest

from app.agent.loop.middleware.workspace_guard import WorkspaceGuardMiddleware
from app.agent.sandbox.permissions import PERMISSION_RULES, build_permissions

PID = "p" * 36
OTHER = "o" * 36

#: 手册「二、(5)」权限表 —— 顺序回归基准（与 PERMISSION_RULES 逐行一致）
MANUAL_TABLE = [
    (["read", "write"], ["/projects/{pid}/**"], "allow"),
    (["write"], ["/projects/**"], "deny"),
    (["read"], ["/projects/**"], "allow"),
    (["read"], ["/skills/**"], "allow"),
    (["write"], ["/skills/**"], "deny"),
    (["read", "write"], ["/conversation_history/**"], "allow"),
    (["read", "write"], ["/large_tool_results/**"], "allow"),
    (["read", "write"], ["/**"], "deny"),
]


class TestPermissionOrderRegression:
    def test_rules_match_manual_table(self):
        """常量数组必须与手册表逐行一致（铁律 9：兜底 deny 必须最后）。"""
        assert PERMISSION_RULES == MANUAL_TABLE

    def test_rule_count_is_eight(self):
        assert len(PERMISSION_RULES) == 8

    def test_fallback_deny_is_last(self):
        ops, paths, mode = PERMISSION_RULES[-1]
        assert paths == ["/**"]
        assert mode == "deny"
        assert ops == ["read", "write"]

    def test_build_permissions_substitutes_pid(self):
        perms = build_permissions(PID)
        assert len(perms) == 8
        assert perms[0].paths == [f"/projects/{PID}/**"]
        assert perms[0].mode == "allow"
        assert perms[1].mode == "deny"
        assert perms[-1].paths == ["/**"]
        assert perms[-1].mode == "deny"

    def test_operations_mapping(self):
        """ls/read_file/glob/grep → read；write_file/edit_file/delete → write。"""
        from app.agent.loop.middleware.workspace_guard import _TOOL_OPERATION

        for tool in ("ls", "read_file", "glob", "grep"):
            assert _TOOL_OPERATION[tool] == "read"
        for tool in ("write_file", "edit_file", "delete"):
            assert _TOOL_OPERATION[tool] == "write"


class TestGuardPathMatrix:
    """8 条规则逐条用例（中间件层 check_path，permissions 之外的第二道）。"""

    @pytest.fixture
    def guard(self) -> WorkspaceGuardMiddleware:
        return WorkspaceGuardMiddleware()

    # 规则 1：当前项目读写通过
    def test_current_project_read_allow(self, guard):
        assert guard.check_path(f"/projects/{PID}/novel.yaml", "read", PID) is None

    def test_current_project_write_allow(self, guard):
        assert guard.check_path(f"/projects/{PID}/chapters/v1/c1.md", "write", PID) is None

    # 规则 2：其它项目写被拒（含 write_file/edit_file/delete 三个工具 → 同为 write 操作）
    @pytest.mark.parametrize("tool", ["write_file", "edit_file", "delete"])
    def test_other_project_write_denied_all_write_tools(self, guard, tool):
        from app.agent.loop.middleware.workspace_guard import _TOOL_OPERATION

        op = _TOOL_OPERATION[tool]
        result = guard.check_path(f"/projects/{OTHER}/chapters/v1/c1.md", op, PID)
        assert result is not None and "跨项目" in result

    # 规则 3：其它项目读通过
    def test_other_project_read_allow(self, guard):
        assert guard.check_path(f"/projects/{OTHER}/novel.yaml", "read", PID) is None

    # 规则 4：/skills/** 读通过
    @pytest.mark.parametrize("path", ["/skills/builtin/prose-writing/SKILL.md", "/skills/user/my-skill/SKILL.md"])
    def test_skills_read_allow(self, guard, path):
        assert guard.check_path(path, "read", PID) is None

    # 规则 5：/skills/** 写被拒
    def test_skills_write_denied(self, guard):
        result = guard.check_path("/skills/builtin/prose-writing/SKILL.md", "write", PID)
        assert result is not None and "只读" in result

    # 规则 6/7：卸载区读写通过
    @pytest.mark.parametrize(
        "path", ["/conversation_history/abc.md", "/large_tool_results/x.json"]
    )
    def test_offload_dirs_allow(self, guard, path):
        assert guard.check_path(path, "read", PID) is None
        assert guard.check_path(path, "write", PID) is None

    # 规则 8：兜底 deny —— 项目区/技能区/卸载区之外全拒
    @pytest.mark.parametrize(
        "path", ["/etc/passwd", "/root/.env", "/anything/else.txt", "/projects", "/skills"]
    )
    def test_outside_denied_read_and_write(self, guard, path):
        assert guard.check_path(path, "read", PID) is not None
        assert guard.check_path(path, "write", PID) is not None

    # 路径穿越
    @pytest.mark.parametrize(
        "path",
        [
            f"/projects/{PID}/../{OTHER}/novel.yaml",
            "/projects/../../etc/passwd",
            "/skills/builtin/../../workspaces/x/novel.yaml",
        ],
    )
    def test_dotdot_rejected(self, guard, path):
        result = guard.check_path(path, "read", PID)
        assert result is not None and ".." in result

    def test_empty_path_allowed(self, guard):
        """无路径参数的工具（如 web_search）直接放行。"""
        assert guard.check_path("", "read", PID) is None

    def test_relative_path_deferred(self, guard):
        """相对路径由 backend 解析（permissions 已覆盖），中间件不拦。"""
        assert guard.check_path("chapters/v1/c1.md", "write", PID) is None
