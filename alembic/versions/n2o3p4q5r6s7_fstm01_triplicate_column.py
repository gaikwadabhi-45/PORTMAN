"""Add is_triplicate column to finance_service_types

Revision ID: n2o3p4q5r6s7
Revises: m1n2o3p4q5r6
Create Date: 2026-03-24
"""
from alembic import op
import sqlalchemy as sa

revision = 'n2o3p4q5r6s7'
down_revision = 'm1n2o3p4q5r6'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('finance_service_types',
        sa.Column('is_triplicate', sa.SmallInteger(), server_default='0', nullable=True))


def downgrade():
    op.drop_column('finance_service_types', 'is_triplicate')
