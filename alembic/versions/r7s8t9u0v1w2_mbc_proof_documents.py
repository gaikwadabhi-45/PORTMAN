"""Add mbc_proof_documents table for MBC01 Proof of Quantity uploads (bytes in DB)

Revision ID: r7s8t9u0v1w2
Revises: q1r2s3t4u5v6
Create Date: 2026-04-20
"""
from alembic import op

revision = 'r7s8t9u0v1w2'
down_revision = 'q1r2s3t4u5v6'
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
        CREATE TABLE IF NOT EXISTS mbc_proof_documents (
            id SERIAL PRIMARY KEY,
            mbc_id INTEGER NOT NULL REFERENCES mbc_header(id) ON DELETE CASCADE,
            original_filename TEXT NOT NULL,
            file_bytes BYTEA NOT NULL,
            mime_type TEXT,
            uploaded_by TEXT,
            uploaded_at TIMESTAMP DEFAULT NOW()
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS idx_mbc_proof_mbc_id ON mbc_proof_documents(mbc_id)")


def downgrade():
    op.execute("DROP INDEX IF EXISTS idx_mbc_proof_mbc_id")
    op.execute("DROP TABLE IF EXISTS mbc_proof_documents")
