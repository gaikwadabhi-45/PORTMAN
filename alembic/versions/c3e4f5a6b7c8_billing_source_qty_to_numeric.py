"""billing: convert source quantity columns from real -> numeric

Follow-up to c2d3e4f5a6b7. The finance tables are now numeric, but the upstream
quantity columns that feed bill/invoice line quantities were still `real`
(float4, ~7 significant digits). A cargo quantity with enough significant digits
(e.g. 56789.456 MT) gets snapped on write, so the quantity displayed on the
bill/invoice and used in quantity x rate can be slightly off before it ever
reaches the (now precise) finance tables.

Converts the billing-source quantity columns to numeric(18,3). ROUND() in the
USING clause strips float representation noise from existing values.

Revision ID: c3e4f5a6b7c8
Revises: c2d3e4f5a6b7
Create Date: 2026-06-09
"""
from alembic import op

revision = 'c3e4f5a6b7c8'
down_revision = 'c2d3e4f5a6b7'
branch_labels = None
depends_on = None


QTY = ('numeric(18,3)', 3)

COLUMNS = [
    ('vcn_cargo_declaration',        'bl_quantity',       QTY),
    ('vcn_cargo_declaration',        'billed_quantity',   QTY),
    ('vcn_export_cargo_declaration', 'bl_quantity',       QTY),
    ('vcn_export_cargo_declaration', 'billed_quantity',   QTY),
    ('mbc_customer_details',         'quantity',          QTY),
    ('mbc_customer_details',         'billed_quantity',   QTY),
    ('service_records',              'billable_quantity', QTY),
]


def upgrade() -> None:
    for table, column, (numtype, scale) in COLUMNS:
        op.execute(
            f'ALTER TABLE {table} '
            f'ALTER COLUMN {column} TYPE {numtype} '
            f'USING ROUND({column}::numeric, {scale})'
        )


def downgrade() -> None:
    for table, column, _ in COLUMNS:
        op.execute(
            f'ALTER TABLE {table} '
            f'ALTER COLUMN {column} TYPE real '
            f'USING {column}::real'
        )
