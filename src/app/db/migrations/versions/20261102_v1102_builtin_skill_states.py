""" 架构优化——新增 builtin_skill_states 表（内置技能/工具的启用状态）。

内置技能本体随包只读，不落 skill_configs；启用开关单独存这张表
（name 唯一，缺行视为启用）。未启用的内置技能不可被主 Agent 调用、
不可被子代理绑定（装配时过滤）。

Revision ID: a1v1102bss
Revises: a1v1102sub
Create Date: 2026-11-02 14:00:00.000000
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = 'a1v1102bss'
down_revision = 'a1v1102sub'
branch_labels = None
depends_on = None


def upgrade() -> None:
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


def downgrade() -> None:
    op.drop_table('builtin_skill_states')
