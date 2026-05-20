"""invoice_header: normalize date columns from TEXT to proper types

Brings invoice_header in line with fdcn_header (whose equivalent columns are
already proper DATE/TIMESTAMP) and unblocks the /api/sap/callback handler
from string/date COALESCE mismatches:

  sap_posting_date : TEXT -> TIMESTAMP  (anchors the 24h FB08 cancel window
                                          — needs hour precision)
  gst_ack_date     : TEXT -> DATE       (IRP ack is date-only)
  posted_date      : TEXT -> TIMESTAMP  (tracks when we pushed to staging)

All three columns are currently empty across the table (verified at
migration time), so no USING clause / data scrubbing is needed.

Revision ID: c7d8e9f0a1b2
Revises: b6c7d8e9f0a1
Create Date: 2026-05-15
"""
from typing import Sequence, Union
from alembic import op

revision: str = 'c7d8e9f0a1b2'
down_revision: Union[str, None] = 'b6c7d8e9f0a1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE invoice_header
            ALTER COLUMN sap_posting_date TYPE TIMESTAMP
            USING NULLIF(sap_posting_date, '')::TIMESTAMP
    """)
    op.execute("""
        ALTER TABLE invoice_header
            ALTER COLUMN gst_ack_date TYPE DATE
            USING NULLIF(gst_ack_date, '')::DATE
    """)
    op.execute("""
        ALTER TABLE invoice_header
            ALTER COLUMN posted_date TYPE TIMESTAMP
            USING NULLIF(posted_date, '')::TIMESTAMP
    """)


def downgrade() -> None:
    # Reverse to TEXT — preserve formatted values where present.
    op.execute("ALTER TABLE invoice_header ALTER COLUMN sap_posting_date TYPE TEXT USING to_char(sap_posting_date, 'YYYY-MM-DD HH24:MI:SS')")
    op.execute("ALTER TABLE invoice_header ALTER COLUMN gst_ack_date     TYPE TEXT USING to_char(gst_ack_date,     'YYYY-MM-DD')")
    op.execute("ALTER TABLE invoice_header ALTER COLUMN posted_date      TYPE TEXT USING to_char(posted_date,      'YYYY-MM-DD HH24:MI:SS')")
