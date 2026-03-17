"""FCAM01: make service_type_id nullable in customer_agreement_lines

Revision ID: i6j7k8l9m0n1
Revises: h5i6j7k8l9m0
Create Date: 2026-03-17
"""
from alembic import op
import sqlalchemy as sa

revision = 'i6j7k8l9m0n1'
down_revision = 'h5i6j7k8l9m0'
branch_labels = None
depends_on = None


def upgrade():
    op.alter_column('customer_agreement_lines', 'service_type_id',
                     existing_type=sa.Integer(),
                     nullable=True)


def downgrade():
    op.alter_column('customer_agreement_lines', 'service_type_id',
                     existing_type=sa.Integer(),
                     nullable=False)
