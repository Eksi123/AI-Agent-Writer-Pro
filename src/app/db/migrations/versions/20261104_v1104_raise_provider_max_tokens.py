# -*- coding: utf-8 -*-
""" 既有 provider 的 max_tokens 仍停留在 的 2048 默认值，
导致长回复（如整篇大纲改写）被截断成空正文。把仍为 2048 的记录抬到 8192。

Revision ID: a1v1104mtx
Revises: a1v1102wfr
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = 'a1v1104mtx'
down_revision = 'a1v1102wfr'
branch_labels = None
depends_on = None


def upgrade() -> None:
  # Max Token 改以 K 为单位（默认 32K）；旧默认值 2048 / 8192 一并抬升
    op.execute(sa.text("UPDATE llm_providers SET max_tokens = 32768 WHERE max_tokens IN (2048, 8192)"))
    # Context Length 进弹窗（默认 256K）；旧模型行仍是 32768 的老默认值，一并抬升
    op.execute(sa.text("UPDATE llm_models SET max_context = 262144 WHERE max_context = 32768"))


def downgrade() -> None:
    # 不自动回退：无法区分「用户手工设成 8192」与「本次迁移抬升」的记录
    pass
