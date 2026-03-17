"""Add is_default column to gst_rates

Revision ID: j7k8l9m0n1o2
Revises: i6j7k8l9m0n1
Create Date: 2026-03-17
"""
from alembic import op
import sqlalchemy as sa

revision = 'j7k8l9m0n1o2'
down_revision = 'i6j7k8l9m0n1'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('gst_rates', sa.Column('is_default', sa.Boolean(), server_default='false', nullable=False))


def downgrade():
    op.drop_column('gst_rates', 'is_default')
