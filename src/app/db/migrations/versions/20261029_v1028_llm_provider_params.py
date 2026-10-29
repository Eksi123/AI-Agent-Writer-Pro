"""v1028: llm_providers 增加 timeout_sec / temperature / max_tokens

Revision ID: a1v1028prm
Revises: 22869e0bf363
Create Date: 2026-10-29 16:40:00.000000
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = 'a1v1028prm'
down_revision = '22869e0bf363'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('llm_providers', schema=None) as batch_op:
        batch_op.add_column(sa.Column('timeout_sec', sa.Integer(), nullable=False, server_default='30'))
        batch_op.add_column(sa.Column('temperature', sa.Float(), nullable=False, server_default='1.0'))
        batch_op.add_column(sa.Column('max_tokens', sa.Integer(), nullable=False, server_default='2048'))


def downgrade() -> None:
    with op.batch_alter_table('llm_providers', schema=None) as batch_op:
        batch_op.drop_column('max_tokens')
        batch_op.drop_column('temperature')
        batch_op.drop_column('timeout_sec')
