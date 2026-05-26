"""Port map schema: convert anchorage lat/lon to NUMERIC, add lat/lon/sequence to
port_berth_master, create port_waypoints table with seed data.

Revision ID: b2c3d4e5f6a7
Revises: z6a7b8c9d0e1
Create Date: 2026-05-26
"""
from typing import Sequence, Union
from alembic import op

revision: str = 'b2c3d4e5f6a7'
down_revision: Union[str, None] = 'z6a7b8c9d0e1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Convert anchorage_master lat/lon from TEXT to NUMERIC(9,6)
    op.execute("""
        ALTER TABLE anchorage_master
        ALTER COLUMN latitude  TYPE NUMERIC(9,6)
        USING CASE WHEN latitude  IS NULL OR latitude  = '' THEN NULL
                   ELSE latitude::numeric  END
    """)
    op.execute("""
        ALTER TABLE anchorage_master
        ALTER COLUMN longitude TYPE NUMERIC(9,6)
        USING CASE WHEN longitude IS NULL OR longitude = '' THEN NULL
                   ELSE longitude::numeric END
    """)

    # Add coordinates and ordering to port_berth_master
    op.execute("""
        ALTER TABLE port_berth_master
        ADD COLUMN IF NOT EXISTS latitude       NUMERIC(9,6),
        ADD COLUMN IF NOT EXISTS longitude      NUMERIC(9,6),
        ADD COLUMN IF NOT EXISTS berth_sequence INTEGER DEFAULT 0
    """)

    # Key transit/terminal waypoints for the Dharamtar channel
    op.execute("""
        CREATE TABLE IF NOT EXISTS port_waypoints (
            id             SERIAL PRIMARY KEY,
            name           TEXT UNIQUE NOT NULL,
            latitude       NUMERIC(9,6) NOT NULL,
            longitude      NUMERIC(9,6) NOT NULL,
            waypoint_type  TEXT DEFAULT 'transit'
        )
    """)
    op.execute("""
        INSERT INTO port_waypoints (name, latitude, longitude, waypoint_type)
        VALUES
            ('Gull Island',   18.834000, 72.896800, 'transit'),
            ('Yellow Crane',  18.717324, 73.018150, 'terminal')
        ON CONFLICT (name) DO NOTHING
    """)


def downgrade() -> None:
    op.execute('DROP TABLE IF EXISTS port_waypoints')
    op.execute('ALTER TABLE port_berth_master DROP COLUMN IF EXISTS latitude')
    op.execute('ALTER TABLE port_berth_master DROP COLUMN IF EXISTS longitude')
    op.execute('ALTER TABLE port_berth_master DROP COLUMN IF EXISTS berth_sequence')
    op.execute("ALTER TABLE anchorage_master ALTER COLUMN latitude  TYPE TEXT USING latitude::text")
    op.execute("ALTER TABLE anchorage_master ALTER COLUMN longitude TYPE TEXT USING longitude::text")
