"""Add TDS columns to bill_lines and invoice_lines for SAP integration

Revision ID: h5i6j7k8l9m0
Revises: g4h5i6j7k8l9
Create Date: 2026-03-16
"""
from alembic import op

revision = 'h5i6j7k8l9m0'
down_revision = 'g4h5i6j7k8l9'
branch_labels = None
depends_on = None


def upgrade():
    # Add TDS columns to bill_lines
    op.execute("""
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name='bill_lines' AND column_name='tds_percent'
            ) THEN
                ALTER TABLE bill_lines
                    ADD COLUMN tds_applicable SMALLINT DEFAULT 0,
                    ADD COLUMN tds_percent NUMERIC(5,2) DEFAULT 0,
                    ADD COLUMN tds_amount NUMERIC(14,2) DEFAULT 0;
            END IF;
        END $$
    """)

    # Add TDS columns to invoice_lines
    op.execute("""
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name='invoice_lines' AND column_name='tds_percent'
            ) THEN
                ALTER TABLE invoice_lines
                    ADD COLUMN tds_applicable SMALLINT DEFAULT 0,
                    ADD COLUMN tds_percent NUMERIC(5,2) DEFAULT 0,
                    ADD COLUMN tds_amount NUMERIC(14,2) DEFAULT 0;
            END IF;
        END $$
    """)

    # Add service_code to invoice_lines (needed for SAP Service_Code field)
    op.execute("""
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name='invoice_lines' AND column_name='service_code'
            ) THEN
                ALTER TABLE invoice_lines
                    ADD COLUMN service_code VARCHAR(50);
            END IF;
        END $$
    """)

    # Add service_code to bill_lines if missing
    op.execute("""
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name='bill_lines' AND column_name='service_code'
            ) THEN
                ALTER TABLE bill_lines
                    ADD COLUMN service_code VARCHAR(50);
            END IF;
        END $$
    """)

    # Add hsn_sac to bill_lines if missing (separate from sac_code for SAP HSN_SAC field)
    op.execute("""
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name='invoice_lines' AND column_name='hsn_sac'
            ) THEN
                ALTER TABLE invoice_lines
                    ADD COLUMN hsn_sac VARCHAR(20);
            END IF;
        END $$
    """)

    # Add sap_tax_code to invoice_lines for SAP Tax_Code field
    op.execute("""
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name='invoice_lines' AND column_name='sap_tax_code'
            ) THEN
                ALTER TABLE invoice_lines
                    ADD COLUMN sap_tax_code VARCHAR(10);
            END IF;
        END $$
    """)


def downgrade():
    op.execute("ALTER TABLE bill_lines DROP COLUMN IF EXISTS tds_applicable")
    op.execute("ALTER TABLE bill_lines DROP COLUMN IF EXISTS tds_percent")
    op.execute("ALTER TABLE bill_lines DROP COLUMN IF EXISTS tds_amount")
    op.execute("ALTER TABLE bill_lines DROP COLUMN IF EXISTS service_code")
    op.execute("ALTER TABLE invoice_lines DROP COLUMN IF EXISTS tds_applicable")
    op.execute("ALTER TABLE invoice_lines DROP COLUMN IF EXISTS tds_percent")
    op.execute("ALTER TABLE invoice_lines DROP COLUMN IF EXISTS tds_amount")
    op.execute("ALTER TABLE invoice_lines DROP COLUMN IF EXISTS service_code")
    op.execute("ALTER TABLE invoice_lines DROP COLUMN IF EXISTS hsn_sac")
    op.execute("ALTER TABLE invoice_lines DROP COLUMN IF EXISTS sap_tax_code")
