"""Add ldud_proof_documents table for Proof of Quantity uploads

Revision ID: p9q0r1s2t3u4
Revises: h3i4j5k6l7m8
Create Date: 2026-04-17
"""
from alembic import op

revision = 'p9q0r1s2t3u4'
down_revision = 'h3i4j5k6l7m8'
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
