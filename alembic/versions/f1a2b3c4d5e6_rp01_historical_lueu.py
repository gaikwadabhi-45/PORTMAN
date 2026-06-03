"""RP01: rp01_historical_lueu table for historical LUEU reference data

Revision ID: f1a2b3c4d5e6
Revises: e7f8a9b0c1d2
Create Date: 2026-06-03
"""
from typing import Sequence, Union
from alembic import op

revision: str = 'f1a2b3c4d5e6'
down_revision: Union[str, None] = 'e7f8a9b0c1d2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS rp01_historical_lueu (
            id              SERIAL PRIMARY KEY,
            entry_date      DATE NOT NULL,
            shift           VARCHAR(20),
            equipment_name  VARCHAR(200) NOT NULL,
            from_time       VARCHAR(5),
            to_time         VARCHAR(5),
            source_display  VARCHAR(300),
            barge_name      VARCHAR(300),
            cargo_name      VARCHAR(200),
            delay_name      VARCHAR(200),
            system_name     VARCHAR(200),
            route_name      VARCHAR(200),
            berth_name      VARCHAR(200),
            shift_incharge  VARCHAR(200),
            operator_name   VARCHAR(200),
            quantity        NUMERIC,
            quantity_uom    VARCHAR(50),
            remarks         TEXT,
            uploaded_by     INTEGER,
            uploaded_at     TIMESTAMP DEFAULT NOW()
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_rp01_hist_entry_date ON rp01_historical_lueu (entry_date)")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS rp01_historical_lueu")
