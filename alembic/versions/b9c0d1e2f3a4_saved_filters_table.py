"""Add saved_filters table for per-module named filter presets

Revision ID: b9c0d1e2f3a4
Revises: (a7b8c9d0e1f2, w0x1y2z3a4b5)
Create Date: 2026-04-23
"""
from typing import Sequence, Union
from alembic import op

revision: str = 'b9c0d1e2f3a4'
down_revision: Union[str, None] = ('a7b8c9d0e1f2', 'w0x1y2z3a4b5')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS saved_filters (
            id SERIAL PRIMARY KEY,
            name VARCHAR(100) NOT NULL,
            module_code VARCHAR(20) NOT NULL,
            filters_json TEXT NOT NULL,
            created_by VARCHAR(100),
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_saved_filters_module ON saved_filters (module_code)")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_saved_filters_module")
    op.execute("DROP TABLE IF EXISTS saved_filters")
