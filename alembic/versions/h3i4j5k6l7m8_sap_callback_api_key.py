"""Add callback_api_key to sap_api_config

Revision ID: h3i4j5k6l7m8
Revises: g2h3i4j5k6l7
Create Date: 2026-04-14

Adds an optional API key column used to authenticate inbound SAP adapter
callbacks at /api/sap/callback.  Leave NULL to skip key validation.
"""
from alembic import op
import sqlalchemy as sa

revision = 'h3i4j5k6l7m8'
down_revision = 'g2h3i4j5k6l7'
branch_labels = None
depends_on = None


def upgrade():
    op.execute('''
        ALTER TABLE sap_api_config
        ADD COLUMN IF NOT EXISTS callback_api_key VARCHAR(128)
    ''')


def downgrade():
    op.execute('ALTER TABLE sap_api_config DROP COLUMN IF EXISTS callback_api_key')
