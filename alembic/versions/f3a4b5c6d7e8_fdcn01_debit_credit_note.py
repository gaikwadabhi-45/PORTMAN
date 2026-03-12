"""FDCN01: Debit/Credit Note module tables, remove FCN01 credit_note tables

Revision ID: f3a4b5c6d7e8
Revises: e2f3a4b5c6d7
Create Date: 2026-03-11
"""
from alembic import op

revision = 'f3a4b5c6d7e8'
down_revision = 'e2f3a4b5c6d7'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── 1. Create FDCN01 tables ─────────────────────────────────────────────

    op.execute("""
        CREATE TABLE IF NOT EXISTS fdcn_doc_series (
            id SERIAL PRIMARY KEY,
            name VARCHAR(100) NOT NULL,
            prefix VARCHAR(20) NOT NULL,
            type VARCHAR(5) NOT NULL CHECK (type IN ('DN', 'CN')),
            is_default BOOLEAN DEFAULT FALSE,
            is_active BOOLEAN DEFAULT TRUE
        )
    """)

    op.execute("""
        CREATE TABLE IF NOT EXISTS fdcn_header (
            id SERIAL PRIMARY KEY,
            doc_number VARCHAR(50) UNIQUE,
            doc_type VARCHAR(5) NOT NULL CHECK (doc_type IN ('DN', 'CN')),
            doc_date DATE,
            doc_series VARCHAR(20),
            doc_series_seq INTEGER,
            financial_year VARCHAR(10),
            original_invoice_id INTEGER REFERENCES invoice_header(id),
            original_invoice_number VARCHAR(50),
            customer_id INTEGER,
            customer_type VARCHAR(20),
            customer_name VARCHAR(200),
            customer_gstin VARCHAR(20),
            customer_gst_state_code VARCHAR(5),
            customer_gl_code VARCHAR(50),
            subtotal NUMERIC(15,2) DEFAULT 0,
            cgst_amount NUMERIC(15,2) DEFAULT 0,
            sgst_amount NUMERIC(15,2) DEFAULT 0,
            igst_amount NUMERIC(15,2) DEFAULT 0,
            total_amount NUMERIC(15,2) DEFAULT 0,
            doc_status VARCHAR(30) DEFAULT 'Draft',
            rejection_reason TEXT,
            created_by VARCHAR(100),
            created_date DATE,
            approved_by VARCHAR(100),
            approved_date DATE,
            sap_document_number VARCHAR(50),
            sap_posting_date DATE,
            sap_fiscal_year VARCHAR(10),
            sap_company_code VARCHAR(10),
            gst_irn VARCHAR(100),
            gst_ack_number VARCHAR(50),
            gst_ack_date DATE,
            gst_qr_code TEXT,
            posted_by VARCHAR(100),
            posted_date DATE,
            remarks TEXT
        )
    """)

    op.execute("""
        CREATE TABLE IF NOT EXISTS fdcn_lines (
            id SERIAL PRIMARY KEY,
            fdcn_id INTEGER NOT NULL REFERENCES fdcn_header(id) ON DELETE CASCADE,
            invoice_line_id INTEGER,
            service_type_id INTEGER,
            service_name VARCHAR(200),
            service_description TEXT,
            quantity NUMERIC(15,4) DEFAULT 0,
            uom VARCHAR(20),
            original_rate NUMERIC(15,4) DEFAULT 0,
            revised_rate NUMERIC(15,4) DEFAULT 0,
            rate_difference NUMERIC(15,4) DEFAULT 0,
            line_amount NUMERIC(15,2) DEFAULT 0,
            gst_rate_id INTEGER,
            cgst_rate NUMERIC(6,2) DEFAULT 0,
            sgst_rate NUMERIC(6,2) DEFAULT 0,
            igst_rate NUMERIC(6,2) DEFAULT 0,
            cgst_amount NUMERIC(15,2) DEFAULT 0,
            sgst_amount NUMERIC(15,2) DEFAULT 0,
            igst_amount NUMERIC(15,2) DEFAULT 0,
            line_total NUMERIC(15,2) DEFAULT 0,
            gl_code VARCHAR(50),
            sac_code VARCHAR(20),
            remarks VARCHAR(500)
        )
    """)

    # ── 2. Drop FCN01 credit_note tables ────────────────────────────────────

    op.execute("DROP TABLE IF EXISTS credit_note_lines CASCADE")
    op.execute("DROP TABLE IF EXISTS credit_note_header CASCADE")


def downgrade() -> None:
    # Drop FDCN01 tables
    op.execute("DROP TABLE IF EXISTS fdcn_lines")
    op.execute("DROP TABLE IF EXISTS fdcn_header")
    op.execute("DROP TABLE IF EXISTS fdcn_doc_series")

    # Recreate FCN01 tables
    op.execute("""
        CREATE TABLE IF NOT EXISTS credit_note_header (
            id SERIAL PRIMARY KEY,
            credit_note_number TEXT UNIQUE NOT NULL,
            credit_note_date DATE,
            financial_year TEXT,
            original_invoice_id INTEGER REFERENCES invoice_header(id),
            party_type TEXT,
            party_id INTEGER,
            reason TEXT,
            subtotal NUMERIC(15,2) DEFAULT 0,
            cgst_amount NUMERIC(15,2) DEFAULT 0,
            sgst_amount NUMERIC(15,2) DEFAULT 0,
            igst_amount NUMERIC(15,2) DEFAULT 0,
            total_amount NUMERIC(15,2) DEFAULT 0,
            credit_note_status TEXT DEFAULT 'Draft',
            created_by TEXT,
            created_date DATE,
            sap_document_number TEXT,
            sap_posting_date DATE,
            gst_irn TEXT,
            gst_ack_number TEXT,
            gst_ack_date DATE
        )
    """)

    op.execute("""
        CREATE TABLE IF NOT EXISTS credit_note_lines (
            id SERIAL PRIMARY KEY,
            credit_note_id INTEGER NOT NULL REFERENCES credit_note_header(id) ON DELETE CASCADE,
            original_invoice_line_id INTEGER REFERENCES invoice_lines(id),
            line_number INTEGER,
            service_name TEXT,
            service_description TEXT,
            quantity NUMERIC(15,4) DEFAULT 0,
            rate NUMERIC(15,4) DEFAULT 0,
            line_amount NUMERIC(15,2) DEFAULT 0,
            cgst_amount NUMERIC(15,2) DEFAULT 0,
            sgst_amount NUMERIC(15,2) DEFAULT 0,
            igst_amount NUMERIC(15,2) DEFAULT 0,
            line_total NUMERIC(15,2) DEFAULT 0,
            gl_code TEXT,
            sac_code TEXT
        )
    """)
