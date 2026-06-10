"""finance: convert money/quantity/rate columns from real -> numeric

Root cause of wrong rounding (e.g. 7801.00 MT x 174.46 stored as 1360962.50
instead of 1360962.46): the finance amount/quantity/rate columns were declared
`real` (PostgreSQL float4, ~7 significant digits). Near 1.36M a float4 can only
land on multiples of 0.125, so an exact value like 1360962.46 is physically
snapped to 1360962.5 on write. The calculation in JS/Python is correct; the
column type was silently truncating precision.

This migration converts those columns to `numeric` with appropriate scale.
ROUND() in the USING clause strips the float representation noise from existing
values on the way in. Already-saved rows keep whatever value was stored (a
historical row that was snapped to .50 stays .50); only the storage type is
fixed so all future writes are exact.

Revision ID: c2d3e4f5a6b7
Revises: c1d2e3f4a5b6
Create Date: 2026-06-09
"""
from alembic import op

revision = 'c2d3e4f5a6b7'
down_revision = 'c1d2e3f4a5b6'
branch_labels = None
depends_on = None


# (table, column, numeric_type, scale)
AMOUNT = ('numeric(18,2)', 2)   # money values
RATE   = ('numeric(18,4)', 4)   # unit rate / exchange rate
PCT    = ('numeric(7,4)',  4)   # gst percentage rates
QTY    = ('numeric(18,3)', 3)   # quantities
MISC   = ('numeric(12,2)', 2)   # no_of_days / no_of_hrs

COLUMNS = [
    # bill_header
    ('bill_header', 'exchange_rate', RATE),
    ('bill_header', 'subtotal',      AMOUNT),
    ('bill_header', 'cgst_amount',   AMOUNT),
    ('bill_header', 'sgst_amount',   AMOUNT),
    ('bill_header', 'igst_amount',   AMOUNT),
    ('bill_header', 'total_amount',  AMOUNT),
    # bill_lines
    ('bill_lines', 'quantity',    QTY),
    ('bill_lines', 'rate',        RATE),
    ('bill_lines', 'line_amount', AMOUNT),
    ('bill_lines', 'cgst_rate',   PCT),
    ('bill_lines', 'sgst_rate',   PCT),
    ('bill_lines', 'igst_rate',   PCT),
    ('bill_lines', 'cgst_amount', AMOUNT),
    ('bill_lines', 'sgst_amount', AMOUNT),
    ('bill_lines', 'igst_amount', AMOUNT),
    ('bill_lines', 'line_total',  AMOUNT),
    # customer_agreement_lines
    ('customer_agreement_lines', 'rate',       RATE),
    ('customer_agreement_lines', 'min_charge', AMOUNT),
    ('customer_agreement_lines', 'max_charge', AMOUNT),
    # invoice_bill_mapping
    ('invoice_bill_mapping', 'bill_amount', AMOUNT),
    # invoice_header
    ('invoice_header', 'exchange_rate',  RATE),
    ('invoice_header', 'subtotal',       AMOUNT),
    ('invoice_header', 'cgst_amount',    AMOUNT),
    ('invoice_header', 'sgst_amount',    AMOUNT),
    ('invoice_header', 'igst_amount',    AMOUNT),
    ('invoice_header', 'tds_amount',     AMOUNT),
    ('invoice_header', 'round_off',      AMOUNT),
    ('invoice_header', 'total_amount',   AMOUNT),
    ('invoice_header', 'no_of_days',     MISC),
    ('invoice_header', 'cargo_quantity', QTY),
    ('invoice_header', 'no_of_hrs',      MISC),
    # invoice_lines
    ('invoice_lines', 'quantity',    QTY),
    ('invoice_lines', 'rate',        RATE),
    ('invoice_lines', 'line_amount', AMOUNT),
    ('invoice_lines', 'cgst_rate',   PCT),
    ('invoice_lines', 'sgst_rate',   PCT),
    ('invoice_lines', 'igst_rate',   PCT),
    ('invoice_lines', 'cgst_amount', AMOUNT),
    ('invoice_lines', 'sgst_amount', AMOUNT),
    ('invoice_lines', 'igst_amount', AMOUNT),
    ('invoice_lines', 'line_total',  AMOUNT),
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
