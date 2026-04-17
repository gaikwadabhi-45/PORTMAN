"""Add global default columns to sap_api_config for SAP payload builder fallbacks

Revision ID: q1r2s3t4u5v6
Revises: p9q0r1s2t3u4
Create Date: 2026-04-17
"""
from alembic import op

revision = 'q1r2s3t4u5v6'
down_revision = 'p9q0r1s2t3u4'
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
        ALTER TABLE sap_api_config
          ADD COLUMN IF NOT EXISTS business_place TEXT,
          ADD COLUMN IF NOT EXISTS section_code TEXT,
          ADD COLUMN IF NOT EXISTS plant_code TEXT,
          ADD COLUMN IF NOT EXISTS tax_code TEXT,
          ADD COLUMN IF NOT EXISTS profit_center TEXT,
          ADD COLUMN IF NOT EXISTS tds_gl TEXT,
          ADD COLUMN IF NOT EXISTS tcs_gl TEXT,
          ADD COLUMN IF NOT EXISTS round_off_gl TEXT
    """)


def downgrade():
    op.execute("""
        ALTER TABLE sap_api_config
          DROP COLUMN IF EXISTS business_place,
          DROP COLUMN IF EXISTS section_code,
          DROP COLUMN IF EXISTS plant_code,
          DROP COLUMN IF EXISTS tax_code,
          DROP COLUMN IF EXISTS profit_center,
          DROP COLUMN IF EXISTS tds_gl,
          DROP COLUMN IF EXISTS tcs_gl,
          DROP COLUMN IF EXISTS round_off_gl
    """)
