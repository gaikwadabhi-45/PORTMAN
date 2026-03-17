"""Add FB70 fields to sap_api_config for full SAP FI posting support

Revision ID: k8l9m0n1o2p3
Revises: j7k8l9m0n1o2
Create Date: 2026-03-17
"""
from alembic import op
import sqlalchemy as sa

revision = 'k8l9m0n1o2p3'
down_revision = 'j7k8l9m0n1o2'
branch_labels = None
depends_on = None


def upgrade():
    # FB70 posting defaults
    op.add_column('sap_api_config', sa.Column('plant_code', sa.Text(), server_default=''))
    op.add_column('sap_api_config', sa.Column('business_place', sa.Text(), server_default=''))
    op.add_column('sap_api_config', sa.Column('section_code', sa.Text(), server_default=''))
    op.add_column('sap_api_config', sa.Column('profit_center', sa.Text(), server_default=''))
    op.add_column('sap_api_config', sa.Column('tax_code', sa.Text(), server_default=''))
    op.add_column('sap_api_config', sa.Column('currency', sa.Text(), server_default='INR'))
    op.add_column('sap_api_config', sa.Column('created_by', sa.Text()))

    # Rename default_payment_term to payment_term for consistency
    # (keep both — old code uses default_payment_term, new code uses payment_term)
    op.add_column('sap_api_config', sa.Column('payment_term', sa.Text(), server_default=''))


def downgrade():
    op.drop_column('sap_api_config', 'plant_code')
    op.drop_column('sap_api_config', 'business_place')
    op.drop_column('sap_api_config', 'section_code')
    op.drop_column('sap_api_config', 'profit_center')
    op.drop_column('sap_api_config', 'tax_code')
    op.drop_column('sap_api_config', 'currency')
    op.drop_column('sap_api_config', 'created_by')
    op.drop_column('sap_api_config', 'payment_term')
