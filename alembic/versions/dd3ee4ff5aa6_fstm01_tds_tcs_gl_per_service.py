"""Add sap_tds_gl and sap_tcs_gl to finance_service_types

Revision ID: dd3ee4ff5aa6
Revises: cc3dd4ee5ff6
Create Date: 2026-04-03
"""
from alembic import op

revision = 'dd3ee4ff5aa6'
down_revision = 'cc3dd4ee5ff6'
branch_labels = None
depends_on = None


def upgrade():
    op.execute("ALTER TABLE finance_service_types ADD COLUMN IF NOT EXISTS sap_tds_gl TEXT")
    op.execute("ALTER TABLE finance_service_types ADD COLUMN IF NOT EXISTS sap_tcs_gl TEXT")


def downgrade():
    pass
