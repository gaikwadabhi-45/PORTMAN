"""Add IGST/CGST/SGST GL columns to finance_service_types; TDS/TCS/RoundOff GL to sap_api_config

Revision ID: cc3dd4ee5ff6
Revises: bb2cc3dd4ee5
Create Date: 2026-04-02
"""
from alembic import op

revision = 'cc3dd4ee5ff6'
down_revision = 'bb2cc3dd4ee5'
branch_labels = None
depends_on = None


def upgrade():
    # Per-service GL accounts for GST lines
    op.execute("ALTER TABLE finance_service_types ADD COLUMN IF NOT EXISTS sap_igst_gl TEXT")
    op.execute("ALTER TABLE finance_service_types ADD COLUMN IF NOT EXISTS sap_cgst_gl TEXT")
    op.execute("ALTER TABLE finance_service_types ADD COLUMN IF NOT EXISTS sap_sgst_gl TEXT")
    # 'S' = Service, 'A' = Sale  (SAP SERVICE/SALE flag)
    op.execute("ALTER TABLE finance_service_types ADD COLUMN IF NOT EXISTS service_sale_flag TEXT DEFAULT 'S'")

    # Default GL accounts for TDS, TCS, round-off at document level
    op.execute("ALTER TABLE sap_api_config ADD COLUMN IF NOT EXISTS tds_gl TEXT DEFAULT ''")
    op.execute("ALTER TABLE sap_api_config ADD COLUMN IF NOT EXISTS tcs_gl TEXT DEFAULT ''")
    op.execute("ALTER TABLE sap_api_config ADD COLUMN IF NOT EXISTS round_off_gl TEXT DEFAULT ''")


def downgrade():
    pass
