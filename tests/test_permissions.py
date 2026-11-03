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

from app.agent.harness.guard.workspace_guard import _TOOL_OPERATION, WorkspaceGuardMiddleware
from app.agent.harness.sandbox.permissions import PERMISSION_RULES, build_permissions

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

        op = _TOOL_OPERATION[tool]
        result = guard.check_path(f"/projects/{OTHER}/chapters/v1/c1.md", op, PID)
        assert result is not None and "跨项目" in result

    # 规则 3：其它项目读通过
    def test_other_project_read_allow(self, guard):
        assert guard.check_path(f"/projects/{OTHER}/novel.yaml", "read", PID) is None

    # 规则 4：/skills/** 读通过（技能 = 用户自建）
    @pytest.mark.parametrize("path", ["/skills/user/my-skill/SKILL.md", "/skills/user/x/y.md"])
    def test_skills_read_allow(self, guard, path):
        assert guard.check_path(path, "read", PID) is None

    # 规则 5：/skills/** 写被拒（技能由服务层维护）
    def test_skills_write_denied(self, guard):
        result = guard.check_path("/skills/user/my-skill/SKILL.md", "write", PID)
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
            "/skills/user/../../workspaces/x/novel.yaml",
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


# ---------------------------------------------------------------------------
# WorkspaceGuard 路径安全 (from test_workspace_guard.py)
# ---------------------------------------------------------------------------

import os

from app.core.errors import ForbiddenError
from app.core.paths import workspace_dir
from app.services.workspace import WorkspaceGuard


@pytest.fixture
async def two_projects(client):
    """建两个项目，用于跨项目越权测试。"""
    a = (await client.post("/api/v1/novels", json={"title": "项目甲"})).json()["data"]["id"]
    b = (await client.post("/api/v1/novels", json={"title": "项目乙"})).json()["data"]["id"]
    return a, b


class TestWorkspaceGuard:
    """WorkspaceGuard 路径安全检查（文件系统层面）"""

    @pytest.mark.asyncio
    async def test_write_inside_workspace_ok(self, two_projects):
        pid, _ = two_projects
        guard = WorkspaceGuard(pid)
        path = guard.write("notes/draft.md", "内容")
        assert path.is_file()
        assert path.read_text(encoding="utf-8") == "内容"

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "rel",
        [
            "../../../etc/passwd",
            "../../other/x.md",
            "chapters/../../..",
            "./../../secret.md",
            "a/../../../b.md",
        ],
    )
    async def test_dotdot_traversal_rejected(self, two_projects, rel):
        """拒绝含 `..` 的路径。"""
        pid, _ = two_projects
        guard = WorkspaceGuard(pid)
        with pytest.raises(ForbiddenError):
            guard.write(rel, "恶意内容")

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "rel",
        [
            "/etc/passwd",
            "C:\\Windows\\System32\\x.txt",
            "C:/Windows/x.txt",
            "/absolute/path.md",
        ],
    )
    async def test_absolute_path_rejected(self, two_projects, rel):
        """拒绝绝对路径逃逸。"""
        pid, _ = two_projects
        guard = WorkspaceGuard(pid)
        with pytest.raises(ForbiddenError):
            guard.write(rel, "恶意内容")

    @pytest.mark.asyncio
    async def test_cross_project_write_rejected(self, two_projects):
        """跨项目写一律拒绝：项目 A 的 guard 不能写项目 B 的文件。"""
        pid_a, pid_b = two_projects
        guard_a = WorkspaceGuard(pid_a)

        # 尝试用相对路径跳到 B 的工作区
        with pytest.raises(ForbiddenError):
            guard_a.write(f"../{pid_b}/novel.yaml", "篡改")

        # 直接用绝对路径指向 B
        b_meta = workspace_dir(pid_b) / "novel.yaml"
        assert b_meta.is_file()
        original = b_meta.read_text(encoding="utf-8")

        with pytest.raises(ForbiddenError):
            guard_a.write(str(b_meta), "篡改")

        assert b_meta.read_text(encoding="utf-8") == original, "B 的文件不得被改动"

    @pytest.mark.asyncio
    async def test_summaries_write_requires_authorization(self, two_projects):
        """`summaries/` 只允许 summarize_chapter 服务写（allow_summaries=True）。"""
        pid, _ = two_projects
        guard = WorkspaceGuard(pid)

        with pytest.raises(ForbiddenError):
            guard.write("summaries/ch-abc.md", "伪造摘要")

        # 授权路径可写
        path = guard.write("summaries/ch-abc.md", "合法摘要", allow_summaries=True)
        assert path.is_file()

    @pytest.mark.asyncio
    async def test_symlink_escape_rejected(self, two_projects, tmp_path):
        """符号链接解析后越界 → 拒绝。Windows 无管理员权限建不了 symlink 时用 junction/跳过。"""
        pid, _ = two_projects
        guard = WorkspaceGuard(pid)
        ws = guard.root

        outside = tmp_path / "outside-secret"
        outside.mkdir(exist_ok=True)
        (outside / "secret.md").write_text("外部机密", encoding="utf-8")

        link = ws / "chapters" / "evil-link"
        link.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.symlink(outside, link, target_is_directory=True)
        except (OSError, NotImplementedError, AttributeError) as exc:
            pytest.skip(f"当前环境无法创建符号链接（{exc}）；Windows 下需管理员权限或改用 junction")

        try:
            # 通过符号链接写外部文件应被拒
            with pytest.raises(ForbiddenError):
                guard.write("chapters/evil-link/secret.md", "篡改外部文件")
            assert (outside / "secret.md").read_text(encoding="utf-8") == "外部机密"
        finally:
            link.unlink(missing_ok=True)

    @pytest.mark.asyncio
    async def test_shared_root_rejects_workspaces(self, two_projects):
        """共享区换算不得指向 workspaces（项目区只能经项目 guard 访问）。"""
        guard = WorkspaceGuard("")
        with pytest.raises(ForbiddenError):
            guard.resolve_shared("workspaces/xxx/novel.yaml")

    @pytest.mark.asyncio
    async def test_shared_root_rejects_traversal(self):
        guard = WorkspaceGuard("")
        for bad in ("../secret", "/abs/path", "skills/../../x"):
            with pytest.raises(ForbiddenError):
                guard.resolve_shared(bad)

    @pytest.mark.asyncio
    async def test_delete_within_workspace(self, two_projects):
        pid, _ = two_projects
        guard = WorkspaceGuard(pid)
        guard.write("tmp/a.md", "x")
        assert guard.delete("tmp/a.md") is True
        assert guard.delete("tmp/a.md") is False  # 幂等

    @pytest.mark.asyncio
    async def test_read_missing_file_raises(self, two_projects):
        pid, _ = two_projects
        guard = WorkspaceGuard(pid)
        with pytest.raises(FileNotFoundError):
            guard.read("does-not-exist.md")
