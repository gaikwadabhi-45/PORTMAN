"""Cargo declaration billing — final finance module rework

Moves billing source from lueu_lines to vcn_cargo_declaration,
vcn_export_cargo_declaration, and mbc_customer_details.

Changes:
  - ADD billed tracking (is_billed, bill_id, billed_quantity) to the 3 declaration tables
  - ADD cargo_source_type + cargo_source_id to bill_lines
  - DROP eu_line_id from bill_lines (and its FK to lueu_lines)
  - DROP billing columns from lueu_lines (is_billed, bill_id, billed_quantity, service_type_id)
    lueu_lines remains for equipment utilization tracking only

Revision ID: ee4ff5aa6bb7
Revises: dd3ee4ff5aa6
Create Date: 2026-04-09
"""
from alembic import op

revision = 'ee4ff5aa6bb7'
down_revision = 'dd3ee4ff5aa6'
branch_labels = None
depends_on = None


def upgrade():
    # ------------------------------------------------------------------ #
    # 1. Add billed tracking to vcn_cargo_declaration (import BL rows)   #
    # ------------------------------------------------------------------ #
    op.execute("""
        ALTER TABLE vcn_cargo_declaration
        ADD COLUMN IF NOT EXISTS is_billed      INTEGER DEFAULT 0,
        ADD COLUMN IF NOT EXISTS bill_id        INTEGER,
        ADD COLUMN IF NOT EXISTS billed_quantity REAL DEFAULT 0
    """)

    # ------------------------------------------------------------------ #
    # 2. Add billed tracking to vcn_export_cargo_declaration (export)    #
    # ------------------------------------------------------------------ #
    op.execute("""
        ALTER TABLE vcn_export_cargo_declaration
        ADD COLUMN IF NOT EXISTS is_billed      INTEGER DEFAULT 0,
        ADD COLUMN IF NOT EXISTS bill_id        INTEGER,
        ADD COLUMN IF NOT EXISTS billed_quantity REAL DEFAULT 0
    """)

    # ------------------------------------------------------------------ #
    # 3. Add billed tracking to mbc_customer_details                     #
    # ------------------------------------------------------------------ #
    op.execute("""
        ALTER TABLE mbc_customer_details
        ADD COLUMN IF NOT EXISTS is_billed      INTEGER DEFAULT 0,
        ADD COLUMN IF NOT EXISTS bill_id        INTEGER,
        ADD COLUMN IF NOT EXISTS billed_quantity REAL DEFAULT 0
    """)

    # ------------------------------------------------------------------ #
    # 4. bill_lines: add cargo_source columns, drop eu_line_id           #
    #    cargo_source_type: 'VCN_IMPORT' | 'VCN_EXPORT' | 'MBC'         #
    #    cargo_source_id  : id in the respective declaration table       #
    # ------------------------------------------------------------------ #
    op.execute("""
        ALTER TABLE bill_lines
        ADD COLUMN IF NOT EXISTS cargo_source_type TEXT,
        ADD COLUMN IF NOT EXISTS cargo_source_id   INTEGER
    """)

    # Drop FK constraint then drop the column
    op.execute("""
        ALTER TABLE bill_lines
        DROP CONSTRAINT IF EXISTS bill_lines_eu_line_id_fkey
    """)
    op.execute("""
        ALTER TABLE bill_lines
        DROP COLUMN IF EXISTS eu_line_id
    """)

    # ------------------------------------------------------------------ #
    # 5. lueu_lines: drop billing columns — table is now equipment-only  #
    # ------------------------------------------------------------------ #
    op.execute("""
        ALTER TABLE lueu_lines
        DROP COLUMN IF EXISTS is_billed,
        DROP COLUMN IF EXISTS bill_id,
        DROP COLUMN IF EXISTS billed_quantity,
        DROP COLUMN IF EXISTS service_type_id
    """)


def downgrade():
    # Restore lueu_lines billing columns
    op.execute("""
        ALTER TABLE lueu_lines
        ADD COLUMN IF NOT EXISTS service_type_id  INTEGER,
        ADD COLUMN IF NOT EXISTS is_billed        INTEGER DEFAULT 0,
        ADD COLUMN IF NOT EXISTS bill_id          INTEGER,
        ADD COLUMN IF NOT EXISTS billed_quantity  DOUBLE PRECISION
    """)

    # Restore eu_line_id to bill_lines (no FK restored — data is gone)
    op.execute("""
        ALTER TABLE bill_lines
        ADD COLUMN IF NOT EXISTS eu_line_id INTEGER,
        DROP COLUMN IF EXISTS cargo_source_type,
        DROP COLUMN IF EXISTS cargo_source_id
    """)

    # Drop billed tracking from declaration tables
    op.execute("""
        ALTER TABLE mbc_customer_details
        DROP COLUMN IF EXISTS is_billed,
        DROP COLUMN IF EXISTS bill_id,
        DROP COLUMN IF EXISTS billed_quantity
    """)
    op.execute("""
        ALTER TABLE vcn_export_cargo_declaration
        DROP COLUMN IF EXISTS is_billed,
        DROP COLUMN IF EXISTS bill_id,
        DROP COLUMN IF EXISTS billed_quantity
    """)
    op.execute("""
        ALTER TABLE vcn_cargo_declaration
        DROP COLUMN IF EXISTS is_billed,
        DROP COLUMN IF EXISTS bill_id,
        DROP COLUMN IF EXISTS billed_quantity
    """)
