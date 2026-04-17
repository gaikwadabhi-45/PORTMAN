"""Add ldud_proof_documents table for Proof of Quantity uploads

Revision ID: a1b2c3d4e5f6
Revises: z6a7b8c9d0e1
Create Date: 2026-04-17
"""
from alembic import op

revision = 'a1b2c3d4e5f6'
down_revision = 'z6a7b8c9d0e1'
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
        CREATE TABLE IF NOT EXISTS ldud_proof_documents (
            id SERIAL PRIMARY KEY,
            ldud_id INTEGER NOT NULL REFERENCES ldud_header(id) ON DELETE CASCADE,
            original_filename TEXT NOT NULL,
            stored_filename TEXT NOT NULL,
            uploaded_by TEXT,
            uploaded_at TIMESTAMP DEFAULT NOW()
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS idx_ldud_proof_ldud_id ON ldud_proof_documents(ldud_id)")


def downgrade():
    op.execute("DROP INDEX IF EXISTS idx_ldud_proof_ldud_id")
    op.execute("DROP TABLE IF EXISTS ldud_proof_documents")
