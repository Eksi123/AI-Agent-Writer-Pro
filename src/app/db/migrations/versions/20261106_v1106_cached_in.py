# -*- coding: utf-8 -*-
"""v1106: messages / agent_runs 增加 cached_in（输入 token 缓存命中量）。

缓存命中率 = cached_in / tokens_in，来源 usage_metadata.input_token_details.cache_read
（OpenAI 兼容协议 prompt_tokens_details.cached_tokens / Anthropic cache_read_input_tokens）。
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "v1106cache"
down_revision = "a1v1105otl"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("messages") as batch:
        batch.add_column(sa.Column("cached_in", sa.Integer(), nullable=False, server_default="0"))
    with op.batch_alter_table("agent_runs") as batch:
        batch.add_column(sa.Column("cached_in", sa.Integer(), nullable=False, server_default="0"))


def downgrade() -> None:
    with op.batch_alter_table("agent_runs") as batch:
        batch.drop_column("cached_in")
    with op.batch_alter_table("messages") as batch:
        batch.drop_column("cached_in")
