"""daily_ops_cutoff table for storing hardcoded MTD values before system go-live

Revision ID: e2f3a4b5c6d7
Revises: d1e2f3a4b5c6
Create Date: 2026-03-11
"""
from alembic import op

revision = 'e2f3a4b5c6d7'
down_revision = 'd1e2f3a4b5c6'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS daily_ops_cutoff (
            id           SERIAL PRIMARY KEY,
            cutoff_date  TEXT    NOT NULL,
            cutoff_values TEXT   NOT NULL,
            created_by   TEXT,
            created_date TEXT    DEFAULT CURRENT_TIMESTAMP
        )
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS daily_ops_cutoff")
