"""Add load_port column to mbc_header

Revision ID: c0d1e2f3a4b5
Revises: b9c0d1e2f3a4
Create Date: 2026-04-24
"""
from typing import Sequence, Union
from alembic import op

revision: str = 'c0d1e2f3a4b5'
down_revision: Union[str, None] = 'b9c0d1e2f3a4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TABLE mbc_header ADD COLUMN IF NOT EXISTS load_port TEXT")


def downgrade() -> None:
    op.execute("ALTER TABLE mbc_header DROP COLUMN IF EXISTS load_port")
