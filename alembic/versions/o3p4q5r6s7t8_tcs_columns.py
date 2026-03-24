"""Add TCS columns to service types, bill lines, invoice lines, and invoice header

Revision ID: o3p4q5r6s7t8
Revises: n2o3p4q5r6s7
Create Date: 2026-03-24
"""
from alembic import op
import sqlalchemy as sa

revision = 'o3p4q5r6s7t8'
down_revision = 'n2o3p4q5r6s7'
branch_labels = None
depends_on = None


def upgrade():
    # Service type master
    op.add_column('finance_service_types',
        sa.Column('is_tcs', sa.SmallInteger(), server_default='0', nullable=True))
    op.add_column('finance_service_types',
        sa.Column('tcs_percent', sa.Numeric(5, 2), nullable=True))

    # Bill lines
    op.add_column('bill_lines',
        sa.Column('tcs_applicable', sa.SmallInteger(), server_default='0', nullable=True))
    op.add_column('bill_lines',
        sa.Column('tcs_percent', sa.Numeric(5, 2), nullable=True))
    op.add_column('bill_lines',
        sa.Column('tcs_amount', sa.Numeric(12, 2), server_default='0', nullable=True))

    # Invoice lines
    op.add_column('invoice_lines',
        sa.Column('tcs_applicable', sa.SmallInteger(), server_default='0', nullable=True))
    op.add_column('invoice_lines',
        sa.Column('tcs_percent', sa.Numeric(5, 2), nullable=True))
    op.add_column('invoice_lines',
        sa.Column('tcs_amount', sa.Numeric(12, 2), server_default='0', nullable=True))

    # Invoice header
    op.add_column('invoice_header',
        sa.Column('tcs_amount', sa.Numeric(12, 2), server_default='0', nullable=True))


def downgrade():
    op.drop_column('invoice_header', 'tcs_amount')
    op.drop_column('invoice_lines', 'tcs_amount')
    op.drop_column('invoice_lines', 'tcs_percent')
    op.drop_column('invoice_lines', 'tcs_applicable')
    op.drop_column('bill_lines', 'tcs_amount')
    op.drop_column('bill_lines', 'tcs_percent')
    op.drop_column('bill_lines', 'tcs_applicable')
    op.drop_column('finance_service_types', 'tcs_percent')
    op.drop_column('finance_service_types', 'is_tcs')
