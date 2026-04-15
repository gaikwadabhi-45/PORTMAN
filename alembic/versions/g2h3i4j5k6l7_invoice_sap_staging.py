"""Create invoice_sap_staging table for SAP adapter integration

Revision ID: g2h3i4j5k6l7
Revises: f1b2c3d4e5f6
Create Date: 2026-04-14

Flat staging table (one row per invoice line) consumed by the JSW SAP adapter.
Header fields are repeated on each row.  The adapter reads rows where
processing_status = 'N' (New) and writes back status + document number + IRN.
"""
from alembic import op
import sqlalchemy as sa

revision = 'g2h3i4j5k6l7'
down_revision = 'f1b2c3d4e5f6'
branch_labels = None
depends_on = None


def upgrade():
    op.execute('''
        CREATE TABLE IF NOT EXISTS invoice_sap_staging (
            id                    SERIAL PRIMARY KEY,

            -- Source reference
            invoice_id            INTEGER,
            invoice_line_id       INTEGER,
            line_number           INTEGER,

            -- ── Header fields ────────────────────────────────────────────
            invoice_type          VARCHAR(3),        -- I=Invoice, C=Credit Note
            company_code          VARCHAR(10),
            document_date         DATE,
            posting_date          DATE,
            reference_text        VARCHAR(16),       -- invoice_number (unique primary)
            document_type         VARCHAR(10),       -- Y1 / Y2 / DR / DG
            cancellation_flag     VARCHAR(3),        -- 'X' for reversal
            nature_of_transaction VARCHAR(5),        -- B2B / B2C
            service_sale          VARCHAR(3),        -- S=Service, A=Sale
            customer_code         VARCHAR(10),       -- customer GL code
            invoice_amount        NUMERIC(15,2),
            currency              VARCHAR(5),
            business_place        VARCHAR(20),
            section_code          VARCHAR(20),
            payment_term          VARCHAR(4),
            baseline_date         DATE,
            header_text           VARCHAR(25),

            -- ── Line item fields ─────────────────────────────────────────
            gl_account            VARCHAR(10),       -- service GL account
            gl_amount             NUMERIC(15,2),     -- taxable line amount (with sign)
            plant                 VARCHAR(10),
            profit_center         VARCHAR(10),       -- PRCTR
            text_description      VARCHAR(25),
            tax_code              VARCHAR(2),        -- MWSKZ
            igst_gl               VARCHAR(10),
            igst_amount           NUMERIC(15,2),
            sgst_gl               VARCHAR(10),
            sgst_amount           NUMERIC(15,2),
            cgst_gl               VARCHAR(10),
            cgst_amount           NUMERIC(15,2),
            hsn_sac_code          VARCHAR(16),
            uom                   VARCHAR(3),        -- MEINS
            unit_price            NUMERIC(15,5),
            quantity              NUMERIC(16,3),     -- MENGE
            tds_gl                VARCHAR(10),
            tds_amount            NUMERIC(15,2),
            tcs_gl                VARCHAR(10),
            tcs_amount            NUMERIC(15,2),
            round_off_gl          VARCHAR(10),
            round_off_value       NUMERIC(15,2),

            -- ── Auto fields (SAP adapter writes back) ────────────────────
            processing_status     VARCHAR(1)   NOT NULL DEFAULT 'N',
                                               -- N=New, Y=Posted, E=Error, R=Reversed
            fiscal_year           VARCHAR(4),
            fiscal_period         VARCHAR(2),
            push_date             DATE,
            push_time             TIME,
            sap_document_number   VARCHAR(20),
            sap_message           VARCHAR(500),
            irn_number            VARCHAR(64),
            ack_number            VARCHAR(20),
            irn_date              DATE,
            qr_code               VARCHAR(256),

            -- ── Audit ─────────────────────────────────────────────────────
            pushed_by             VARCHAR(100),
            created_date          TIMESTAMP    DEFAULT NOW(),
            updated_date          TIMESTAMP
        )
    ''')

    op.execute("CREATE INDEX IF NOT EXISTS idx_sap_staging_invoice_id   ON invoice_sap_staging(invoice_id)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_sap_staging_ref_text     ON invoice_sap_staging(reference_text)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_sap_staging_status       ON invoice_sap_staging(processing_status)")


def downgrade():
    op.execute("DROP TABLE IF EXISTS invoice_sap_staging")
