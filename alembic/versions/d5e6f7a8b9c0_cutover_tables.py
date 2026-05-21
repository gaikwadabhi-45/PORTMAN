"""Cutover tables: cutover_seed, cutover_audit

Revision ID: d5e6f7a8b9c0
Revises: b0c1d2e3f4g5
Create Date: 2026-05-21
"""
from alembic import op

revision = 'd5e6f7a8b9c0'
down_revision = 'b0c1d2e3f4g5'
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
        CREATE TABLE IF NOT EXISTS cutover_seed (
            id              SERIAL PRIMARY KEY,
            seed_type       TEXT NOT NULL CHECK (seed_type IN ('invoice','bill')),
            doc_series      TEXT NOT NULL DEFAULT '',
            financial_year  TEXT NOT NULL DEFAULT '',
            start_seq       INTEGER NOT NULL,
            created_by      TEXT,
            created_at      TIMESTAMP DEFAULT now(),
            updated_by      TEXT,
            updated_at      TIMESTAMP,
            UNIQUE (seed_type, doc_series, financial_year)
        )
    """)
    op.execute("""
        CREATE TABLE IF NOT EXISTS cutover_audit (
            id            SERIAL PRIMARY KEY,
            action        TEXT NOT NULL,
            details       JSONB,
            performed_by  TEXT,
            performed_at  TIMESTAMP DEFAULT now()
        )
    """)


def downgrade():
    op.execute("DROP TABLE IF EXISTS cutover_audit")
    op.execute("DROP TABLE IF EXISTS cutover_seed")
