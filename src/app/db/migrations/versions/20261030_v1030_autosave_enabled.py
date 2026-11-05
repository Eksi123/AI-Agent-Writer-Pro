""" user_settings 增加 autosave_enabled 开关

Revision ID: a1v1030asv
Revises: a1v1028prm
Create Date: 2026-10-30 15:20:00.000000
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = 'a1v1030asv'
down_revision = 'a1v1028prm'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('user_settings', schema=None) as batch_op:
        batch_op.add_column(
            sa.Column('autosave_enabled', sa.Boolean(), nullable=False, server_default=sa.text('1'))
        )


def downgrade() -> None:
    with op.batch_alter_table('user_settings', schema=None) as batch_op:
        batch_op.drop_column('autosave_enabled')
