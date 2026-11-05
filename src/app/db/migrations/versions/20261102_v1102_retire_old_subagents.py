""" 架构优化——清退旧 5 个内置子代理（editor/continuity-checker/researcher 删除，
planner/writer 重置），下次启动由 seed_builtins 重新播种新 4 个
（planner/writer/reviewer/web-searcher，提示词来自 agent/subagents/builtin/*/prompt.md）。

只删 is_builtin=1 的行；用户自建子代理不受影响。
用户曾对内置子代理做的自定义（提示词/模型覆盖）随清退丢弃——这是
「删除原来的旧子代理，整合新增四个内置子代理」的预期语义。

Revision ID: a1v1102sub
Revises: a1v1030asv
Create Date: 2026-11-02 11:00:00.000000
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = 'a1v1102sub'
down_revision = 'a1v1030asv'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(sa.text("DELETE FROM subagent_configs WHERE is_builtin = 1"))


def downgrade() -> None:
    # 旧内置定义已随代码重构删除，无法自动恢复；
    # 回退版本后由 seed_builtins 播种当时代码的内置定义。
    pass
