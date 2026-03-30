"""Add missing soft-delete columns for LUEU01 and creation_type for FDCN01

Revision ID: p1q2r3s4t5u6
Revises: o3p4q5r6s7t8
Create Date: 2026-03-30
"""
from alembic import op

revision = 'p1q2r3s4t5u6'
down_revision = 'o3p4q5r6s7t8'
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
        ALTER TABLE lueu_lines
        ADD COLUMN IF NOT EXISTS is_deleted BOOLEAN DEFAULT FALSE
    """)
    op.execute("""
        ALTER TABLE lueu_lines
        ADD COLUMN IF NOT EXISTS deleted_by VARCHAR(100)
    """)
    op.execute("""
        ALTER TABLE lueu_lines
        ADD COLUMN IF NOT EXISTS deleted_date DATE
    """)
    op.execute("""
        ALTER TABLE fdcn_header
        ADD COLUMN IF NOT EXISTS creation_type VARCHAR(30) DEFAULT 'rate_revision'
    """)


def downgrade():
    op.execute("""
        ALTER TABLE fdcn_header
        DROP COLUMN IF EXISTS creation_type
    """)
    op.execute("""
        ALTER TABLE lueu_lines
        DROP COLUMN IF EXISTS deleted_date
    """)
    op.execute("""
        ALTER TABLE lueu_lines
        DROP COLUMN IF EXISTS deleted_by
    """)
    op.execute("""
        ALTER TABLE lueu_lines
        DROP COLUMN IF EXISTS is_deleted
    """)
