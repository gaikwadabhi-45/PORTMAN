"""Add token_url to sap_api_config (PORTBIRD OAuth endpoint)

Revision ID: u0v1w2x3y4z5
Revises: t9u0v1w2x3y4
Create Date: 2026-04-22
"""
from alembic import op
import sqlalchemy as sa

revision = 'u0v1w2x3y4z5'
down_revision = 't9u0v1w2x3y4'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('sap_api_config',
                  sa.Column('token_url', sa.Text(), server_default=''))


def downgrade():
    op.drop_column('sap_api_config', 'token_url')
