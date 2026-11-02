"""v1102: 架构优化修正——技能 = 用户自建；删除内置技能启用状态表并回填内置子代理默认工具。

- drop `builtin_skill_states`（a1v1102bss 曾建它承载内置写作技能启用开关；
  随包内技能目录移除，启用开关语义消失）。幂等：表不存在则跳过；
- 内置子代理按职能预配默认内置工具（tools_json 为空者回填），保证开箱即用：
  planner/writer 需读写大纲与正文；reviewer 只读审查；web-searcher 只检索。

Revision ID: a1v1102drp
Revises: a1v1102bss
Create Date: 2026-11-02 15:30:00.000000
"""
from __future__ import annotations

import json

import sqlalchemy as sa
from alembic import op

revision = 'a1v1102drp'
down_revision = 'a1v1102bss'
branch_labels = None
depends_on = None

#: 内置子代理按职能预配的默认内置工具（与 agent/subagents/builtin/*/spec.py 一致）
_BUILTIN_TOOLS = {
    'planner': ['read_file', 'write_file', 'edit_file', 'ls', 'grep', 'glob'],
    'writer': ['read_file', 'write_file', 'edit_file', 'ls', 'grep', 'glob'],
    'reviewer': ['read_file', 'ls', 'grep', 'glob'],
    'web-searcher': ['web_search'],
}


def upgrade() -> None:
    bind = op.get_bind()
    if 'builtin_skill_states' in sa.inspect(bind).get_table_names():
        op.drop_table('builtin_skill_states')
    for name, tools in _BUILTIN_TOOLS.items():
        op.execute(
            sa.text(
                "UPDATE subagent_configs SET tools_json = :tools "
                "WHERE is_builtin = 1 AND name = :name "
                "AND (tools_json IS NULL OR tools_json = '[]')"
            ).bindparams(tools=json.dumps(tools), name=name)
        )


def downgrade() -> None:
    bind = op.get_bind()
    if 'builtin_skill_states' not in sa.inspect(bind).get_table_names():
        op.create_table(
            'builtin_skill_states',
            sa.Column('id', sa.String(length=36), nullable=False),
            sa.Column('name', sa.String(length=64), nullable=False),
            sa.Column('enabled', sa.Boolean(), nullable=False, server_default=sa.text('1')),
            sa.Column('created_at', sa.String(length=32), nullable=False),
            sa.Column('updated_at', sa.String(length=32), nullable=False),
            sa.PrimaryKeyConstraint('id'),
            sa.UniqueConstraint('name'),
        )
