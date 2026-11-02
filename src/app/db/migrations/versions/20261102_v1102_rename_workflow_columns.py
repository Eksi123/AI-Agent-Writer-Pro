"""固定流程命名归一：`agent_runs` 的 gate_* 列重命名为 workflow_*。

- `gate_stage` → `workflow_stage`
- `gate_retry_count` → `workflow_retry_count`
- `gate_result_json` → `workflow_result_json`

幂等：列已是新名（或旧列不存在，如新库经 create_all 直接建新列）则跳过。
SQLite ≥3.25 支持 `ALTER TABLE ... RENAME COLUMN`。

Revision ID: a1v1102wfr
Revises: a1v1102drp
Create Date: 2026-11-02 17:20:00.000000
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = 'a1v1102wfr'
down_revision = 'a1v1102drp'
branch_labels = None
depends_on = None

_RENAMES = (
    ("gate_stage", "workflow_stage"),
    ("gate_retry_count", "workflow_retry_count"),
    ("gate_result_json", "workflow_result_json"),
)


def _columns() -> set[str]:
    insp = sa.inspect(op.get_bind())
    return {c["name"] for c in insp.get_columns("agent_runs")}


def upgrade() -> None:
    cols = _columns()
    for old, new in _RENAMES:
        if old in cols and new not in cols:
            op.execute(sa.text(f'ALTER TABLE agent_runs RENAME COLUMN "{old}" TO "{new}"'))


def downgrade() -> None:
    cols = _columns()
    for old, new in _RENAMES:
        if new in cols and old not in cols:
            op.execute(sa.text(f'ALTER TABLE agent_runs RENAME COLUMN "{new}" TO "{old}"'))
