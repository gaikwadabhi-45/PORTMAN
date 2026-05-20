"""Add igst_tax_code/cgst_tax_code to sap_api_config; migrate existing tax_code value

Revision ID: b0c1d2e3f4g5
Revises: a9b0c1d2e3f4
Branch_labels: None
Depends_on: None
"""
from alembic import op

revision = 'b0c1d2e3f4g5'
down_revision = 'a9b0c1d2e3f4'
branch_labels = None
depends_on = None


def upgrade():
    op.execute("ALTER TABLE sap_api_config ADD COLUMN IF NOT EXISTS igst_tax_code TEXT DEFAULT ''")
    op.execute("ALTER TABLE sap_api_config ADD COLUMN IF NOT EXISTS cgst_tax_code TEXT DEFAULT ''")
    # Seed both new columns from the old single tax_code where it had a value
    op.execute("""
        UPDATE sap_api_config
        SET igst_tax_code = COALESCE(tax_code, ''),
            cgst_tax_code = COALESCE(tax_code, '')
        WHERE tax_code IS NOT NULL AND tax_code <> ''
    """)


def downgrade():
    op.execute("ALTER TABLE sap_api_config DROP COLUMN IF EXISTS igst_tax_code")
    op.execute("ALTER TABLE sap_api_config DROP COLUMN IF EXISTS cgst_tax_code")
