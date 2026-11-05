# -*- coding: utf-8 -*-
"""user_settings 增加 export_dir（小说导出目录，NULL = 默认桌面）。

Revision ID: a1v1104exp
Revises: a1v1104mtx
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = 'a1v1104exp'
down_revision = 'a1v1104mtx'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('user_settings', schema=None) as batch_op:
        batch_op.add_column(sa.Column('export_dir', sa.String(length=300), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('user_settings', schema=None) as batch_op:
        batch_op.drop_column('export_dir')
