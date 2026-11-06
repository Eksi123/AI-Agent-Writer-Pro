""" 内置工具重命名：outline_recall → outline_grep。

子代理的工具勾选清单（subagent_configs.tools_json）里存的是旧工具名，
不改的话 `_resolve_selected_tools` 会因别名表查不到而静默丢弃该工具
（仅记 warning），recaller 等内置子代理将失去大纲摘录能力。
此处把 JSON 里的 "outline_recall" 字面量替换为 "outline_grep"；
用户自建子代理若勾选过旧名同样一并迁移。

Revision ID: a1v1105otl
Revises: a1v1104exp
Create Date: 2026-11-05 21:40:00.000000
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = 'a1v1105otl'
down_revision = 'a1v1104exp'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        sa.text(
            "UPDATE subagent_configs"
            " SET tools_json = REPLACE(tools_json, '\"outline_recall\"', '\"outline_grep\"')"
            " WHERE tools_json LIKE '%\"outline_recall\"%'"
        )
    )


def downgrade() -> None:
    op.execute(
        sa.text(
            "UPDATE subagent_configs"
            " SET tools_json = REPLACE(tools_json, '\"outline_grep\"', '\"outline_recall\"')"
            " WHERE tools_json LIKE '%\"outline_grep\"%'"
        )
    )
