"""Repair proof document BYTEA columns for LDUD01 and MBC01

Revision ID: v0w1x2y3z4a5
Revises: u0v1w2x3y4z5
Create Date: 2026-04-22
"""
from alembic import op

revision = 'v0w1x2y3z4a5'
down_revision = 'u0v1w2x3y4z5'
branch_labels = None
depends_on = None


def upgrade():
    op.execute("ALTER TABLE IF EXISTS ldud_proof_documents ADD COLUMN IF NOT EXISTS file_bytes BYTEA")
    op.execute("ALTER TABLE IF EXISTS ldud_proof_documents ADD COLUMN IF NOT EXISTS mime_type TEXT")
    op.execute("ALTER TABLE IF EXISTS mbc_proof_documents ADD COLUMN IF NOT EXISTS file_bytes BYTEA")
    op.execute("ALTER TABLE IF EXISTS mbc_proof_documents ADD COLUMN IF NOT EXISTS mime_type TEXT")

    # Some deployed databases may have a legacy filesystem column that is NOT NULL.
    # The current upload code stores bytes directly, so keep the legacy value if present
    # but make it optional for new uploads.
    op.execute("""
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1
                FROM information_schema.columns
                WHERE table_schema = current_schema()
                  AND table_name = 'ldud_proof_documents'
                  AND column_name = 'stored_filename'
            ) THEN
                ALTER TABLE ldud_proof_documents ALTER COLUMN stored_filename DROP NOT NULL;
            END IF;

            IF EXISTS (
                SELECT 1
                FROM information_schema.columns
                WHERE table_schema = current_schema()
                  AND table_name = 'mbc_proof_documents'
                  AND column_name = 'stored_filename'
            ) THEN
                ALTER TABLE mbc_proof_documents ALTER COLUMN stored_filename DROP NOT NULL;
            END IF;
        END $$;
    """)


def downgrade():
    pass
