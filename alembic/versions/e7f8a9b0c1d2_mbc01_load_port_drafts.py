"""MBC01: add FWD/MID/AFT draft to load port lines (import & export)

Revision ID: e7f8a9b0c1d2
Revises: 75a1a399c282
Create Date: 2026-06-03
"""
from typing import Sequence, Union
from alembic import op

revision: str = 'e7f8a9b0c1d2'
down_revision: Union[str, None] = '75a1a399c282'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TABLE mbc_load_port_lines ADD COLUMN IF NOT EXISTS fwd_draft NUMERIC")
    op.execute("ALTER TABLE mbc_load_port_lines ADD COLUMN IF NOT EXISTS mid_draft NUMERIC")
    op.execute("ALTER TABLE mbc_load_port_lines ADD COLUMN IF NOT EXISTS aft_draft NUMERIC")
    op.execute("ALTER TABLE mbc_export_load_port_lines ADD COLUMN IF NOT EXISTS fwd_draft NUMERIC")
    op.execute("ALTER TABLE mbc_export_load_port_lines ADD COLUMN IF NOT EXISTS mid_draft NUMERIC")
    op.execute("ALTER TABLE mbc_export_load_port_lines ADD COLUMN IF NOT EXISTS aft_draft NUMERIC")


def downgrade() -> None:
    op.execute("ALTER TABLE mbc_load_port_lines DROP COLUMN IF EXISTS fwd_draft")
    op.execute("ALTER TABLE mbc_load_port_lines DROP COLUMN IF EXISTS mid_draft")
    op.execute("ALTER TABLE mbc_load_port_lines DROP COLUMN IF EXISTS aft_draft")
    op.execute("ALTER TABLE mbc_export_load_port_lines DROP COLUMN IF EXISTS fwd_draft")
    op.execute("ALTER TABLE mbc_export_load_port_lines DROP COLUMN IF EXISTS mid_draft")
    op.execute("ALTER TABLE mbc_export_load_port_lines DROP COLUMN IF EXISTS aft_draft")
