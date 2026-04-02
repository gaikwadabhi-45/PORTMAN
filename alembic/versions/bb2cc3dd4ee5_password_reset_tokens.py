"""Add password_reset_tokens table

Revision ID: bb2cc3dd4ee5
Revises: aa1bb2cc3dd4
Create Date: 2026-04-02
"""
from alembic import op

revision = 'bb2cc3dd4ee5'
down_revision = 'aa1bb2cc3dd4'
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
        CREATE TABLE IF NOT EXISTS password_reset_tokens (
            id SERIAL PRIMARY KEY,
            user_id INTEGER NOT NULL,
            email TEXT NOT NULL,
            otp_code TEXT NOT NULL,
            reset_token TEXT NOT NULL UNIQUE,
            expires_at TIMESTAMP NOT NULL,
            otp_verified BOOLEAN NOT NULL DEFAULT FALSE,
            used BOOLEAN NOT NULL DEFAULT FALSE,
            created_at TIMESTAMP NOT NULL DEFAULT NOW()
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS idx_prt_email ON password_reset_tokens(email)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_prt_reset_token ON password_reset_tokens(reset_token)")


def downgrade():
    pass
