"""Renumber duplicate (barge, trip) rows and add unique index

Two-step migration on ldud_barge_lines:

1. **Renumber existing duplicates.** For any (ldud_id, barge_name) group that
   has more than one row sharing the same trip_number, the earliest-entered
   row (lowest id) keeps its trip_number; later duplicates are pushed to
   trip_number = MAX(trip_number for that barge) + N, in id order. Nothing
   is deleted — the duplicate row becomes a legitimate next trip.

2. **Add a partial unique index** on (ldud_id, barge_name, trip_number) so
   future duplicates fail at the database level. Null/blank barge_name and
   null trip_number are allowed (placeholder rows during data entry).

Without step 1, step 2 would fail with "Key (...) is duplicated".

Revision ID: a1b2c3d4e5f8
Revises: z0y9x8w7v6u5
Create Date: 2026-05-11
"""
from typing import Sequence, Union
from alembic import op


revision: str = 'a1b2c3d4e5f8'
down_revision: Union[str, None] = 'z0y9x8w7v6u5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Step 1: renumber duplicates (later-entered rows get bumped past MAX).
    #
    # `dup_rank` ranks rows inside each (ldud_id, barge_name, trip_number)
    # by id ASC; rank 1 keeps its trip number, rank > 1 needs renumbering.
    # `bump_rank` then assigns sequential numbers above the existing MAX
    # within each (ldud_id, barge_name) so multiple duplicate groups for
    # the same barge don't collide with each other.
    op.execute("""
        WITH max_per_barge AS (
            SELECT ldud_id, barge_name, MAX(trip_number) AS max_trip
            FROM ldud_barge_lines
            WHERE barge_name IS NOT NULL
              AND TRIM(barge_name) <> ''
              AND trip_number IS NOT NULL
            GROUP BY ldud_id, barge_name
        ),
        ranked AS (
            SELECT id, ldud_id, barge_name, trip_number,
                   ROW_NUMBER() OVER (
                       PARTITION BY ldud_id, barge_name, trip_number
                       ORDER BY id
                   ) AS dup_rank
            FROM ldud_barge_lines
            WHERE barge_name IS NOT NULL
              AND TRIM(barge_name) <> ''
              AND trip_number IS NOT NULL
        ),
        to_renumber AS (
            SELECT id, ldud_id, barge_name,
                   ROW_NUMBER() OVER (
                       PARTITION BY ldud_id, barge_name
                       ORDER BY id
                   ) AS bump_rank
            FROM ranked
            WHERE dup_rank > 1
        )
        UPDATE ldud_barge_lines lbl
        SET trip_number = mpb.max_trip + tr.bump_rank
        FROM to_renumber tr
        JOIN max_per_barge mpb
          ON mpb.ldud_id = tr.ldud_id
         AND mpb.barge_name = tr.barge_name
        WHERE lbl.id = tr.id
    """)

    # Step 2: prevent recurrence with a partial unique index.
    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS uq_ldud_barge_trip
            ON ldud_barge_lines (ldud_id, barge_name, trip_number)
            WHERE barge_name IS NOT NULL
              AND TRIM(barge_name) <> ''
              AND trip_number IS NOT NULL
    """)


def downgrade() -> None:
    # The renumbering is not reversible (we don't store the original
    # trip_number anywhere), so downgrade only drops the index.
    op.execute('DROP INDEX IF EXISTS uq_ldud_barge_trip')
