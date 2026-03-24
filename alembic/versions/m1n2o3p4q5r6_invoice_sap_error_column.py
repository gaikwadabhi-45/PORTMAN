"""Add sap_error and virtual_account_id columns to invoice_header

Revision ID: m1n2o3p4q5r6
Revises: l9m0n1o2p3q4
Create Date: 2026-03-24
"""
from alembic import op
import sqlalchemy as sa

revision = 'm1n2o3p4q5r6'
down_revision = 'l9m0n1o2p3q4'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('invoice_header',
        sa.Column('sap_error', sa.Text(), nullable=True))
    op.add_column('invoice_header',
        sa.Column('virtual_account_id', sa.Integer(), nullable=True))
    # Move any SAP errors currently stored in remarks to sap_error
    op.execute("""
        UPDATE invoice_header
        SET sap_error = remarks, remarks = NULL
        WHERE remarks LIKE 'SAP token error:%'
           OR remarks LIKE '404 Client Error:%'
           OR remarks LIKE '%sapapidev%'
    """)


def downgrade():
    op.drop_column('invoice_header', 'virtual_account_id')
    op.drop_column('invoice_header', 'sap_error')
