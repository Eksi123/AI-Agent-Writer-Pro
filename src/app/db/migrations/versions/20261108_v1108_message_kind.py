# -*- coding: utf-8 -*-
"""v1108: messages 增加 kind（消息类别：normal / steer / notice）。

- normal：普通用户消息与 AI 回复（历史数据回填值）；
- steer：运行中「通过消息引导发送」的引导消息（发送时即落库，重进/重启可见）；
- notice：Agent 默认收尾回复（任务暂停 / 网络异常 / 大模型接口异常话术）。

前端据此渲染引导气泡与收尾回复，替代此前仅存内存的 steered 列表，
解决「run 结束清空后重复显示 / 重进会话引导消息丢失」问题。
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "v1108kind"
down_revision = "v1106cache"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("messages") as batch:
        batch.add_column(sa.Column("kind", sa.String(length=16), nullable=False, server_default="normal"))


def downgrade() -> None:
    with op.batch_alter_table("messages") as batch:
        batch.drop_column("kind")
