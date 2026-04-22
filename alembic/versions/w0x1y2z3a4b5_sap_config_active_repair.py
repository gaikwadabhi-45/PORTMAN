"""Mark existing SAP config active when none is active

Revision ID: w0x1y2z3a4b5
Revises: v0w1x2y3z4a5
Create Date: 2026-04-22
"""
from alembic import op

revision = 'w0x1y2z3a4b5'
down_revision = 'v0w1x2y3z4a5'
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
        UPDATE sap_api_config
        SET is_active = 1
        WHERE id = (
            SELECT id
            FROM sap_api_config
            WHERE COALESCE(base_url, '') <> ''
              AND COALESCE(client_id, '') <> ''
              AND COALESCE(client_secret, '') <> ''
            ORDER BY updated_date DESC NULLS LAST,
                     created_date DESC NULLS LAST,
                     id DESC
            LIMIT 1
        )
          AND NOT EXISTS (
              SELECT 1 FROM sap_api_config WHERE COALESCE(is_active, 0) = 1
          )
    """)


def downgrade():
    pass
