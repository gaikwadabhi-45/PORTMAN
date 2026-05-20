"""Merge heads: a8b9c0d1e2f3 (rp01_user_delay_preferences) + c7d8e9f0a1b2 (invoice_header_date_types)

Revision ID: a9b0c1d2e3f4
Revises: a8b9c0d1e2f3, c7d8e9f0a1b2
Create Date: 2026-05-19
"""
from typing import Sequence, Union
from alembic import op

revision: str = 'a9b0c1d2e3f4'
down_revision: Union[str, Sequence[str], None] = ('a8b9c0d1e2f3', 'c7d8e9f0a1b2')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
