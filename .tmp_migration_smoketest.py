"""
Smoke test for migration a1b2c3d4e5f8_ldud_barge_unique_trip.

Runs against the local DATABASE_URL inside a transaction that ROLLBACKS at
the end. Nothing it does persists.

Verifies:
  - no-duplicate rows are untouched
  - single duplicate group is renumbered past MAX
  - multiple duplicate groups for the same barge get sequential bumps
  - triple+ duplicates get N consecutive bumps
  - different barges / different LDUDs don't interfere
  - NULL trip_number / NULL/blank barge_name rows are ignored
  - unique index rejects post-migration duplicates
  - migration is idempotent on a clean table
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from config import DATABASE_URL

import psycopg2
import psycopg2.extras

# --- The migration SQL (kept in sync with alembic/.../a1b2c3d4e5f8_*.py) -----
RENUMBER_SQL = """
WITH max_per_barge AS (
    SELECT ldud_id, barge_name, MAX(trip_number) AS max_trip
    FROM ldud_barge_lines
    WHERE barge_name IS NOT NULL AND TRIM(barge_name) <> '' AND trip_number IS NOT NULL
    GROUP BY ldud_id, barge_name
),
ranked AS (
    SELECT id, ldud_id, barge_name, trip_number,
           ROW_NUMBER() OVER (PARTITION BY ldud_id, barge_name, trip_number ORDER BY id) AS dup_rank
    FROM ldud_barge_lines
    WHERE barge_name IS NOT NULL AND TRIM(barge_name) <> '' AND trip_number IS NOT NULL
),
to_renumber AS (
    SELECT id, ldud_id, barge_name,
           ROW_NUMBER() OVER (PARTITION BY ldud_id, barge_name ORDER BY id) AS bump_rank
    FROM ranked
    WHERE dup_rank > 1
)
UPDATE ldud_barge_lines lbl
SET trip_number = mpb.max_trip + tr.bump_rank
FROM to_renumber tr
JOIN max_per_barge mpb ON mpb.ldud_id = tr.ldud_id AND mpb.barge_name = tr.barge_name
WHERE lbl.id = tr.id
"""

CREATE_INDEX_SQL = """
CREATE UNIQUE INDEX IF NOT EXISTS uq_ldud_barge_trip
    ON ldud_barge_lines (ldud_id, barge_name, trip_number)
    WHERE barge_name IS NOT NULL AND TRIM(barge_name) <> '' AND trip_number IS NOT NULL
"""

# Use a high test LDUD id — collides with nothing in prod
TEST_LDUD_A = 99000001
TEST_LDUD_B = 99000002


def ok(msg):
    print(f"  PASS  {msg}")


def fail(msg, expected, got):
    print(f"  FAIL  {msg}")
    print(f"        expected: {expected}")
    print(f"        got:      {got}")
    raise SystemExit(1)


def fetch_trips(cur, ldud_id, barge):
    cur.execute("""
        SELECT id, trip_number FROM ldud_barge_lines
        WHERE ldud_id = %s AND barge_name = %s
        ORDER BY id
    """, (ldud_id, barge))
    return [(r['id'], r['trip_number']) for r in cur.fetchall()]


def main():
    print(f"Connecting to {DATABASE_URL.split('@')[-1]}")
    conn = psycopg2.connect(DATABASE_URL)
    conn.autocommit = False
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    try:
        # ---- Setup: 2 fake LDUD headers + a deliberately-broken barge_lines set
        cur.execute("""
            INSERT INTO ldud_header (id, doc_num, vessel_name, doc_status)
            VALUES (%s, 'SMOKE-A', 'TEST_VESSEL_A', 'Pending'),
                   (%s, 'SMOKE-B', 'TEST_VESSEL_B', 'Pending')
        """, (TEST_LDUD_A, TEST_LDUD_B))

        # Case A: single duplicate of trip 2
        # ids inserted in this order define entry order
        # expect after: [1, 2, 3, 4]   (the dup of trip 2 becomes trip 4)
        a_ids = []
        for trip in [1, 2, 2, 3]:
            cur.execute("""
                INSERT INTO ldud_barge_lines (ldud_id, barge_name, trip_number, discharge_quantity)
                VALUES (%s, 'BARGE_X', %s, 100) RETURNING id
            """, (TEST_LDUD_A, trip))
            a_ids.append(cur.fetchone()['id'])

        # Case B: two duplicate groups for same barge, interleaved entry order
        # entry order: trip1, trip1, trip2, trip2
        # expect after: [1, 2, 3, 4]
        b_ids = []
        for trip in [1, 1, 2, 2]:
            cur.execute("""
                INSERT INTO ldud_barge_lines (ldud_id, barge_name, trip_number, discharge_quantity)
                VALUES (%s, 'BARGE_Y', %s, 100) RETURNING id
            """, (TEST_LDUD_A, trip))
            b_ids.append(cur.fetchone()['id'])

        # Case C: no duplicates — should be untouched
        c_ids = []
        for trip in [1, 2, 3]:
            cur.execute("""
                INSERT INTO ldud_barge_lines (ldud_id, barge_name, trip_number, discharge_quantity)
                VALUES (%s, 'BARGE_Z', %s, 100) RETURNING id
            """, (TEST_LDUD_A, trip))
            c_ids.append(cur.fetchone()['id'])

        # Case D: same barge name on a DIFFERENT ldud — must not be affected
        d_ids = []
        for trip in [1, 2]:
            cur.execute("""
                INSERT INTO ldud_barge_lines (ldud_id, barge_name, trip_number, discharge_quantity)
                VALUES (%s, 'BARGE_X', %s, 100) RETURNING id
            """, (TEST_LDUD_B, trip))
            d_ids.append(cur.fetchone()['id'])

        # Case E: NULL trip / NULL barge — should be ignored
        cur.execute("""
            INSERT INTO ldud_barge_lines (ldud_id, barge_name, trip_number)
            VALUES (%s, NULL, NULL), (%s, '', 5), (%s, '   ', 6)
            RETURNING id
        """, (TEST_LDUD_A, TEST_LDUD_A, TEST_LDUD_A))
        e_ids = [r['id'] for r in cur.fetchall()]

        # Case G: triple duplicate
        # entry order: trip5, trip5, trip5
        # expect after: [5, 6, 7]
        g_ids = []
        for trip in [5, 5, 5]:
            cur.execute("""
                INSERT INTO ldud_barge_lines (ldud_id, barge_name, trip_number, discharge_quantity)
                VALUES (%s, 'BARGE_W', %s, 100) RETURNING id
            """, (TEST_LDUD_A, trip))
            g_ids.append(cur.fetchone()['id'])

        print("Setup complete. Running migration step 1 (renumber)...")
        cur.execute(RENUMBER_SQL)
        renumbered = cur.rowcount
        print(f"  rows renumbered: {renumbered}")

        # ---- Assertions
        print("Verifying outcomes...")

        # A: [1, 2, 3, 4] in id order
        got = fetch_trips(cur, TEST_LDUD_A, 'BARGE_X')
        want = [(a_ids[0], 1), (a_ids[1], 2), (a_ids[2], 4), (a_ids[3], 3)]
        # Note: id order doesn't equal trip order after renumber. Let's compare as set of (id,trip).
        if sorted(got) != sorted(want):
            fail("Case A (single dup of trip 2)", want, got)
        ok("Case A: dup of trip 2 -> trip 4 (kept original at 2)")

        # B: dup of trip1 and dup of trip2, both bump past max=2
        # entry order ids: b_ids[0]=trip1(keep), b_ids[1]=trip1(bump), b_ids[2]=trip2(keep), b_ids[3]=trip2(bump)
        # bump_rank for to_renumber rows ordered by id: b_ids[1] -> bump=1 -> trip=3, b_ids[3] -> bump=2 -> trip=4
        got = fetch_trips(cur, TEST_LDUD_A, 'BARGE_Y')
        want = [(b_ids[0], 1), (b_ids[1], 3), (b_ids[2], 2), (b_ids[3], 4)]
        if sorted(got) != sorted(want):
            fail("Case B (two dup groups, sequential bump)", want, got)
        ok("Case B: two dup groups bumped sequentially past MAX")

        # C: untouched
        got = fetch_trips(cur, TEST_LDUD_A, 'BARGE_Z')
        want = [(c_ids[0], 1), (c_ids[1], 2), (c_ids[2], 3)]
        if got != want:
            fail("Case C (no dupes)", want, got)
        ok("Case C: clean trips untouched")

        # D: different LDUD untouched
        got = fetch_trips(cur, TEST_LDUD_B, 'BARGE_X')
        want = [(d_ids[0], 1), (d_ids[1], 2)]
        if got != want:
            fail("Case D (different LDUD, same barge name)", want, got)
        ok("Case D: same barge on different LDUD untouched")

        # E: NULL/blank rows untouched
        cur.execute("""
            SELECT id, barge_name, trip_number FROM ldud_barge_lines
            WHERE id = ANY(%s) ORDER BY id
        """, (e_ids,))
        rows = [(r['id'], r['barge_name'], r['trip_number']) for r in cur.fetchall()]
        want = [
            (e_ids[0], None, None),
            (e_ids[1], '',   5),
            (e_ids[2], '   ', 6),
        ]
        if rows != want:
            fail("Case E (NULL/blank rows)", want, rows)
        ok("Case E: NULL trip / blank barge rows ignored")

        # G: triple dup
        got = fetch_trips(cur, TEST_LDUD_A, 'BARGE_W')
        # MAX before = 5, bumps = 1, 2 -> 6, 7
        want = [(g_ids[0], 5), (g_ids[1], 6), (g_ids[2], 7)]
        if got != want:
            fail("Case G (triple dup)", want, got)
        ok("Case G: triple dup -> 5, 6, 7")

        # ---- Step 2: create the unique index, verify it's enforceable
        print("Running migration step 2 (create unique index)...")
        cur.execute(CREATE_INDEX_SQL)

        # Try inserting a duplicate post-index — must raise UniqueViolation
        try:
            cur.execute("""
                INSERT INTO ldud_barge_lines (ldud_id, barge_name, trip_number, discharge_quantity)
                VALUES (%s, 'BARGE_X', 1, 100)
            """, (TEST_LDUD_A,))
            fail("Index enforcement", "UniqueViolation", "INSERT succeeded")
        except psycopg2.errors.UniqueViolation:
            conn.rollback()
            # rollback dropped our setup — re-do minimal state for the next check
            # Actually the rollback wipes everything, including the test data.
            # We'll handle this with savepoints instead. Restart the whole TX.
            raise RuntimeError("Need savepoint for this assertion — see next attempt")

    except RuntimeError:
        # Restart the whole flow with a savepoint around the violating insert
        conn.rollback()
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        print("Re-running with savepoint to test unique index enforcement...")

        cur.execute("""
            INSERT INTO ldud_header (id, doc_num, vessel_name, doc_status)
            VALUES (%s, 'SMOKE-A', 'TEST_VESSEL_A', 'Pending')
        """, (TEST_LDUD_A,))
        cur.execute("""
            INSERT INTO ldud_barge_lines (ldud_id, barge_name, trip_number, discharge_quantity)
            VALUES (%s, 'BARGE_X', 1, 100)
        """, (TEST_LDUD_A,))
        cur.execute(RENUMBER_SQL)
        cur.execute(CREATE_INDEX_SQL)

        cur.execute("SAVEPOINT before_dup")
        try:
            cur.execute("""
                INSERT INTO ldud_barge_lines (ldud_id, barge_name, trip_number, discharge_quantity)
                VALUES (%s, 'BARGE_X', 1, 100)
            """, (TEST_LDUD_A,))
            fail("Index enforcement", "UniqueViolation", "INSERT succeeded")
        except psycopg2.errors.UniqueViolation:
            cur.execute("ROLLBACK TO SAVEPOINT before_dup")
            ok("Unique index rejects future duplicates")

        # Idempotency: re-run renumber on a clean state — should affect 0 rows
        cur.execute(RENUMBER_SQL)
        if cur.rowcount != 0:
            fail("Idempotency", "0 rows renumbered on second run", f"{cur.rowcount} rows")
        ok("Idempotency: re-running renumber on clean data is a no-op")

    finally:
        print("\nROLLING BACK — no changes persisted.")
        conn.rollback()
        conn.close()

    print("\nALL CHECKS PASSED.")


if __name__ == '__main__':
    main()
