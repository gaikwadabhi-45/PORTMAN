"""Add customer CIN fields for invoice printing

Revision ID: f1b2c3d4e5f6
Revises: ee4ff5aa6bb7
Create Date: 2026-04-09
"""
from alembic import op

revision = 'f1b2c3d4e5f6'
down_revision = 'ee4ff5aa6bb7'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute('ALTER TABLE vessel_agents ADD COLUMN IF NOT EXISTS cin TEXT')
    op.execute('ALTER TABLE vessel_customers ADD COLUMN IF NOT EXISTS cin TEXT')
    op.execute('ALTER TABLE invoice_header ADD COLUMN IF NOT EXISTS customer_cin TEXT')


def downgrade() -> None:
    op.execute('ALTER TABLE invoice_header DROP COLUMN IF EXISTS customer_cin')
    op.execute('ALTER TABLE vessel_customers DROP COLUMN IF EXISTS cin')
    op.execute('ALTER TABLE vessel_agents DROP COLUMN IF EXISTS cin')
