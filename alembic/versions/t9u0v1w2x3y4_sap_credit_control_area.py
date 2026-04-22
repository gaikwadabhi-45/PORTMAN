"""Add credit_control_area to sap_api_config (PORTBIRD API spec)

Revision ID: t9u0v1w2x3y4
Revises: s8t9u0v1w2x3
Create Date: 2026-04-22
"""
from alembic import op
import sqlalchemy as sa

revision = 't9u0v1w2x3y4'
down_revision = 's8t9u0v1w2x3'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('sap_api_config',
                  sa.Column('credit_control_area', sa.Text(), server_default=''))


def downgrade():
    op.drop_column('sap_api_config', 'credit_control_area')
