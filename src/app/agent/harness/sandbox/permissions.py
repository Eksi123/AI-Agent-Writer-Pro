# -*- coding: utf-8 -*-
"""
模块: app.agent.harness.sandbox.permissions
文件名: permissions.py
作者: xionggaoqing
时间: 2026-10-27
说明: 权限规则清单：8 条有序 `FilesystemPermission`（手册「二、(5)」表，顺序即语义）。

    判定机制（铁律 9）：首条匹配生效、无匹配默认 allow，故兜底 deny `/**` 必须最后；
    新增规则只能插在兜底之前，且必须补越权测试（顺序回归测试见 tests/test_permissions.py）。
"""

from __future__ import annotations

from deepagents import FilesystemPermission

#: (operations, paths, mode) 三元组常量 —— 与手册表逐行一致，顺序回归测试断言本数组
PERMISSION_RULES: list[tuple[list[str], list[str], str]] = [
    # 1 当前项目可读写
    (["read", "write"], ["/projects/{pid}/**"], "allow"),
    # 2 其它项目只读（含增删改文件；delete 归入 write）
    (["write"], ["/projects/**"], "deny"),
    # 3 允许跨项目阅读
    (["read"], ["/projects/**"], "allow"),
    # 4 技能渐进式披露依赖 read_file 读 SKILL.md
    (["read"], ["/skills/**"], "allow"),
    # 5 技能由服务层维护，Agent 不得改写
    (["write"], ["/skills/**"], "deny"),
    # 6 summarization 驱逐消息卸载目录
    (["read", "write"], ["/conversation_history/**"], "allow"),
    # 7 大工具输出卸载目录
    (["read", "write"], ["/large_tool_results/**"], "allow"),
    # 8 兜底：项目区/技能区/卸载区之外完全隔离
    (["read", "write"], ["/**"], "deny"),
]


def build_permissions(project_id: str) -> list[FilesystemPermission]:
    """把规则常量装配为 `FilesystemPermission` 列表（`{pid}` 替换为当前项目）。"""
    result: list[FilesystemPermission] = []
    for operations, paths, mode in PERMISSION_RULES:
        concrete = [p.replace("{pid}", project_id) for p in paths]
        result.append(FilesystemPermission(operations=list(operations), paths=concrete, mode=mode))  # type: ignore[arg-type]
    return result
