"""Add mail_queue, smtp_config tables and users.email column

Revision ID: aa1bb2cc3dd4
Revises: p1q2r3s4t5u6
Create Date: 2026-04-02
"""
from alembic import op

revision = 'aa1bb2cc3dd4'
down_revision = 'p1q2r3s4t5u6'
branch_labels = None
depends_on = None


def upgrade():
    op.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS email TEXT")

    op.execute("""
        CREATE TABLE IF NOT EXISTS smtp_config (
            id SERIAL PRIMARY KEY,
            host TEXT NOT NULL DEFAULT 'smtp-mail.outlook.com',
            port INTEGER NOT NULL DEFAULT 587,
            username TEXT,
            password TEXT,
            from_email TEXT,
            from_name TEXT DEFAULT 'PORTMAN',
            use_tls BOOLEAN NOT NULL DEFAULT TRUE,
            is_enabled BOOLEAN NOT NULL DEFAULT FALSE,
            schedule_minutes INTEGER NOT NULL DEFAULT 5,
            updated_by TEXT,
            updated_at TIMESTAMP
        )
    """)

    op.execute("""
        CREATE TABLE IF NOT EXISTS mail_queue (
            id SERIAL PRIMARY KEY,
            to_email TEXT NOT NULL,
            to_name TEXT,
            subject TEXT NOT NULL,
            body_html TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            retry_count INTEGER NOT NULL DEFAULT 0,
            max_retries INTEGER NOT NULL DEFAULT 3,
            created_at TIMESTAMP NOT NULL DEFAULT NOW(),
            sent_at TIMESTAMP,
            error_message TEXT,
            module_code TEXT,
            ref_id INTEGER
        )
    """)

    op.execute("CREATE INDEX IF NOT EXISTS idx_mail_queue_status ON mail_queue(status)")


def downgrade():
    pass
