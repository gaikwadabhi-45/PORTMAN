"""FDCN01: enforce one active cancellation CN per invoice

Adds a partial unique index on fdcn_header so the same invoice can't have
two simultaneous full-cancellation Credit Notes. Race-safe guard for the
FINV01 post-24h CN flow — application-level pre-check alone allowed a
narrow window for concurrent double-submits.

Revision ID: b6c7d8e9f0a1
Revises: a1b2c3d4e5f8
Create Date: 2026-05-15
"""
from typing import Sequence, Union
from alembic import op

revision: str = 'b6c7d8e9f0a1'
down_revision: Union[str, None] = 'a1b2c3d4e5f8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS uniq_active_cancellation_cn_per_invoice
        ON fdcn_header (original_invoice_id)
        WHERE doc_type = 'CN'
          AND creation_type = 'cancellation'
          AND original_invoice_id IS NOT NULL
          AND doc_status NOT IN ('Rejected', 'Cancelled')
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uniq_active_cancellation_cn_per_invoice")
