"""Move LDUD proof documents from filesystem to DB (BYTEA)

Revision ID: s8t9u0v1w2x3
Revises: r7s8t9u0v1w2
Create Date: 2026-04-20
"""
from alembic import op

revision = 's8t9u0v1w2x3'
down_revision = 'r7s8t9u0v1w2'
branch_labels = None
depends_on = None


def upgrade():
    op.execute("ALTER TABLE ldud_proof_documents ADD COLUMN IF NOT EXISTS file_bytes BYTEA")
    op.execute("ALTER TABLE ldud_proof_documents ADD COLUMN IF NOT EXISTS mime_type TEXT")
    op.execute("ALTER TABLE ldud_proof_documents DROP COLUMN IF EXISTS stored_filename")


def downgrade():
    op.execute("ALTER TABLE ldud_proof_documents ADD COLUMN IF NOT EXISTS stored_filename TEXT")
    op.execute("ALTER TABLE ldud_proof_documents DROP COLUMN IF EXISTS mime_type")
    op.execute("ALTER TABLE ldud_proof_documents DROP COLUMN IF EXISTS file_bytes")
