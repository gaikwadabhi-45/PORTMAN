"""daily_ops_cutoff: reshape cutoff_values to FY x cargo-type throughput

Replaces the legacy {mbc_cargo, cargo_handled} payload with the new
{fy_throughput: {}} shape. No DDL change (cutoff_values stays TEXT); this is a
data migration. The admin re-saves the cutoff after deploy to populate the
snapshot.

Revision ID: c1d2e3f4a5b6
Revises: f1a2b3c4d5e6
Create Date: 2026-06-08
"""
from alembic import op

revision = 'c1d2e3f4a5b6'
down_revision = 'f1a2b3c4d5e6'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Preserve cutoff_date; reset the (now-incompatible) values payload.
    op.execute("""
        UPDATE daily_ops_cutoff
        SET cutoff_values = '{"fy_throughput": {}}'
    """)


def downgrade() -> None:
    # Restore the legacy empty shape. Pre-migration payloads are not recoverable.
    op.execute("""
        UPDATE daily_ops_cutoff
        SET cutoff_values = '{"mbc_cargo": {}, "cargo_handled": {}}'
    """)
