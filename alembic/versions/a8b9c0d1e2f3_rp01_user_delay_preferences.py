"""RP01: create user_delay_preferences table for saveable shift-report views

Revision ID: a8b9c0d1e2f3
Revises: z6a7b8c9d0e1
Create Date: 2026-05-18
"""
from typing import Sequence, Union
from alembic import op


revision: str = 'a8b9c0d1e2f3'
down_revision: Union[str, None] = 'z6a7b8c9d0e1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS user_delay_preferences (
            id          SERIAL PRIMARY KEY,
            preference_name TEXT NOT NULL,
            delay_keys  JSONB NOT NULL DEFAULT '[]',
            updated_at  TIMESTAMP NOT NULL DEFAULT NOW()
        )
    """)
    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS uix_user_delay_preferences_name
            ON user_delay_preferences (LOWER(preference_name))
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uix_user_delay_preferences_name")
    op.execute("DROP TABLE IF EXISTS user_delay_preferences")
