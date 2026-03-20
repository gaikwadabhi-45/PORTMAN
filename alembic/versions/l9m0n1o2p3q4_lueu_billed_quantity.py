"""Add billed_quantity to lueu_lines for partial billing support

Revision ID: l9m0n1o2p3q4
Revises: k8l9m0n1o2p3
Create Date: 2026-03-20
"""
from alembic import op
import sqlalchemy as sa

revision = 'l9m0n1o2p3q4'
down_revision = 'k8l9m0n1o2p3'
branch_labels = None
depends_on = None


def upgrade():
    # Add billed_quantity column (tracks how much of quantity has been billed so far)
    op.add_column('lueu_lines',
        sa.Column('billed_quantity', sa.Float(), nullable=True, server_default='0'))

    # Backfill: for already-billed lines, set billed_quantity = quantity
    op.execute("""
        UPDATE lueu_lines
        SET billed_quantity = quantity
        WHERE is_billed = 1
    """)


def downgrade():
    op.drop_column('lueu_lines', 'billed_quantity')
