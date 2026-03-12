"""FCAM01: Add cargo_id/cargo_name to customer_agreement_lines for cargo-specific rates

Revision ID: g4h5i6j7k8l9
Revises: f3a4b5c6d7e8
Create Date: 2026-03-11
"""
from alembic import op

revision = 'g4h5i6j7k8l9'
down_revision = 'f3a4b5c6d7e8'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE customer_agreement_lines ADD COLUMN IF NOT EXISTS cargo_id INTEGER")
    op.execute("ALTER TABLE customer_agreement_lines ADD COLUMN IF NOT EXISTS cargo_name VARCHAR(200)")


def downgrade() -> None:
    op.execute("ALTER TABLE customer_agreement_lines DROP COLUMN IF EXISTS cargo_name")
    op.execute("ALTER TABLE customer_agreement_lines DROP COLUMN IF EXISTS cargo_id")
