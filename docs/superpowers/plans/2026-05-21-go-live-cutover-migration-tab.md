# Go-Live Cutover / Migration Tab — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an admin-only "Cutover / Migration" tab that lets staff set the exact starting invoice/bill number at go-live and mark already-billed vessel items as billed (pure flag — no invoice, no SAP), with an audit trail and a post-go-live lock.

**Architecture:** Two new tables (`cutover_seed`, `cutover_audit`). The existing number generators read a per-`(series, fy)` invoice seed and a global bill seed as a *floor* (`GREATEST(max+1, start_seq)`) — no fabricated documents. Cutover logic lives in a new `modules/ADMIN/cutover.py` (pure helpers + DB functions); admin routes in `modules/ADMIN/views.py`; UI as a new tab in `templates/admin.html`. Mark-billed flips `is_billed`/`billed_quantity` on cargo declarations and `is_billed` on service records. A `module_config` flag (`ADMIN.cutover_locked`) gates all writes after go-live.

**Tech Stack:** Python 3 / Flask, PostgreSQL (psycopg via `database.get_db/get_cursor`), Alembic, pytest. Spec: `docs/superpowers/specs/2026-05-21-go-live-cutover-migration-tab-design.md`.

**Before starting:** the repo default branch is `main`. Create a feature branch first (e.g. `git checkout -b feat/cutover-migration-tab`). Commit steps below assume that branch.

---

## File Structure

- **Create** `alembic/versions/d5e6f7a8b9c0_cutover_tables.py` — `cutover_seed`, `cutover_audit` (down_revision = current head `b0c1d2e3f4g5`).
- **Create** `modules/ADMIN/cutover.py` — pure helpers (`next_from_seed`, `validate_start_seq`, `CARGO_SOURCES`/`cargo_source`) + DB functions (seed read/write, mark/unmark billed, lock state, audit).
- **Modify** `modules/FIN01/model.py` — add `next_from_seed`, `lookup_seed`, `next_invoice_seq`; make `get_next_bill_number` seed-aware.
- **Modify** `modules/FINV01/views.py` — `create_invoice` uses `model.next_invoice_seq(...)` instead of inline `+1`.
- **Modify** `modules/ADMIN/views.py` — admin-only, lock-aware cutover routes.
- **Modify** `templates/admin.html` — "Cutover" tab (UI + JS).
- **Create** `test_cutover.py` — pure-core + guardrail + lock-gate tests (repo-root pytest, matching `test_sap_builder.py`).

Pure functions are placed so they import without a live DB (DB calls happen inside functions only), matching the existing test pattern.

---

## Task 1: Migration — create cutover tables

**Files:**
- Create: `alembic/versions/d5e6f7a8b9c0_cutover_tables.py`

- [ ] **Step 1: Write the migration**

```python
"""Cutover tables: cutover_seed, cutover_audit

Revision ID: d5e6f7a8b9c0
Revises: b0c1d2e3f4g5
Create Date: 2026-05-21
"""
from alembic import op

revision = 'd5e6f7a8b9c0'
down_revision = 'b0c1d2e3f4g5'
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
        CREATE TABLE IF NOT EXISTS cutover_seed (
            id              SERIAL PRIMARY KEY,
            seed_type       TEXT NOT NULL CHECK (seed_type IN ('invoice','bill')),
            doc_series      TEXT NOT NULL DEFAULT '',
            financial_year  TEXT NOT NULL DEFAULT '',
            start_seq       INTEGER NOT NULL,
            created_by      TEXT,
            created_at      TIMESTAMP DEFAULT now(),
            updated_by      TEXT,
            updated_at      TIMESTAMP,
            UNIQUE (seed_type, doc_series, financial_year)
        )
    """)
    op.execute("""
        CREATE TABLE IF NOT EXISTS cutover_audit (
            id            SERIAL PRIMARY KEY,
            action        TEXT NOT NULL,
            details       JSONB,
            performed_by  TEXT,
            performed_at  TIMESTAMP DEFAULT now()
        )
    """)


def downgrade():
    op.execute("DROP TABLE IF EXISTS cutover_audit")
    op.execute("DROP TABLE IF EXISTS cutover_seed")
```

- [ ] **Step 2: Verify single head (no new branch introduced)**

Run: `python -m alembic heads`
Expected: exactly one line — `d5e6f7a8b9c0 (head)`.

- [ ] **Step 3: Commit**

```bash
git add alembic/versions/d5e6f7a8b9c0_cutover_tables.py
git commit -m "feat(cutover): add cutover_seed and cutover_audit tables"
```

---

## Task 2: Pure seed-floor helper in FIN01 model

**Files:**
- Modify: `modules/FIN01/model.py`
- Test: `test_cutover.py`

- [ ] **Step 1: Write the failing test**

```python
# test_cutover.py
from modules.FIN01 import model


def test_next_from_seed_no_existing_uses_seed():
    assert model.next_from_seed(0, 4568) == 4568

def test_next_from_seed_existing_at_seed_increments():
    assert model.next_from_seed(4568, 4568) == 4569

def test_next_from_seed_existing_above_seed_dominates():
    assert model.next_from_seed(5000, 4568) == 5001

def test_next_from_seed_no_seed_is_plain_increment():
    assert model.next_from_seed(10, None) == 11
    assert model.next_from_seed(0, None) == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest test_cutover.py -q -W ignore::DeprecationWarning`
Expected: FAIL — `AttributeError: module 'modules.FIN01.model' has no attribute 'next_from_seed'`.

- [ ] **Step 3: Add the helper** (place near the top of `modules/FIN01/model.py`, after imports)

```python
def next_from_seed(existing_max, start_seq):
    """Next sequence number given the highest already-used number and an
    optional cutover floor. The seed is a floor only: once real documents
    exceed it, normal incrementing wins, so a stale seed can never collide."""
    base = (existing_max or 0) + 1
    if start_seq:
        return max(base, int(start_seq))
    return base
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest test_cutover.py -q -W ignore::DeprecationWarning`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add modules/FIN01/model.py test_cutover.py
git commit -m "feat(cutover): add next_from_seed floor helper"
```

---

## Task 3: Seed lookup + wire generators (invoice & bill)

**Files:**
- Modify: `modules/FIN01/model.py`
- Modify: `modules/FINV01/views.py` (the `create_invoice` sequence block, ~lines 288-293)

- [ ] **Step 1: Add `lookup_seed` and `next_invoice_seq` to `modules/FIN01/model.py`**

```python
def lookup_seed(cur, seed_type, doc_series='', financial_year=''):
    """Return the cutover start_seq for this key, or None. Tolerates a missing
    table (pre-migration) by returning None."""
    try:
        cur.execute(
            '''SELECT start_seq FROM cutover_seed
               WHERE seed_type=%s AND doc_series=%s AND financial_year=%s''',
            [seed_type, doc_series or '', financial_year or ''])
        row = cur.fetchone()
        return row['start_seq'] if row else None
    except Exception:
        cur.connection.rollback()
        return None


def next_invoice_seq(cur, doc_series, financial_year):
    """Next invoice doc_series_seq for (doc_series, fy), honouring a cutover seed
    as a floor. Uses the SAME key as the existing MAX query."""
    cur.execute(
        'SELECT MAX(doc_series_seq) AS m FROM invoice_header WHERE doc_series=%s AND financial_year=%s',
        [doc_series, financial_year])
    row = cur.fetchone()
    existing_max = (row['m'] if row else 0) or 0
    seed = lookup_seed(cur, 'invoice', doc_series, financial_year)
    return next_from_seed(existing_max, seed)
```

- [ ] **Step 2: Make `get_next_bill_number` seed-aware** — replace the body of `get_next_bill_number` in `modules/FIN01/model.py` (currently ~lines 79-89)

```python
def get_next_bill_number():
    """Generate next bill number, honouring a cutover bill seed as a floor."""
    conn = get_db()
    cur = get_cursor(conn)
    cur.execute(
        "SELECT MAX(CAST(SUBSTR(bill_number, 5) AS INTEGER)) AS m FROM bill_header WHERE bill_number LIKE 'BILL%%'"
    )
    existing_max = (cur.fetchone()['m'] or 0)
    seed = lookup_seed(cur, 'bill')      # doc_series='', financial_year=''
    next_num = next_from_seed(existing_max, seed)
    conn.close()
    return f"BILL{next_num:04d}"
```

- [ ] **Step 3: Wire `create_invoice` in `modules/FINV01/views.py`** — replace the inline sequence block (currently):

```python
    fy_suffix = model.get_financial_year(invoice_date) if invoice_date else ''
    cur_seq.execute(
        'SELECT MAX(doc_series_seq) FROM invoice_header WHERE doc_series=%s AND financial_year=%s',
        [doc_series_prefix, fy_suffix]
    )
    row_seq = cur_seq.fetchone()
    next_seq = (row_seq['max'] or 0) + 1 if row_seq else 1
    conn_seq.close()
    invoice_number_override = f'{doc_series_prefix}/{next_seq}'
```

with:

```python
    fy_suffix = model.get_financial_year(invoice_date) if invoice_date else ''
    next_seq = model.next_invoice_seq(cur_seq, doc_series_prefix, fy_suffix)
    conn_seq.close()
    invoice_number_override = f'{doc_series_prefix}/{next_seq}'
```

- [ ] **Step 4: Verify import + app still builds**

Run: `python -c "import app as a; print('routes', len(list(a.app.url_map.iter_rules())))"`
Expected: prints a route count, no exception.

- [ ] **Step 5: Commit**

```bash
git add modules/FIN01/model.py modules/FINV01/views.py
git commit -m "feat(cutover): generators honour cutover seed as a floor"
```

---

## Task 4: Pure cutover helpers (guardrail + cargo mapping)

**Files:**
- Create: `modules/ADMIN/cutover.py`
- Test: `test_cutover.py`

- [ ] **Step 1: Add failing tests to `test_cutover.py`**

```python
from modules.ADMIN import cutover


def test_validate_start_seq_ok_above_max():
    assert cutover.validate_start_seq(4568, 0) == (True, '')

def test_validate_start_seq_rejects_at_or_below_max():
    ok, msg = cutover.validate_start_seq(5, 10)
    assert ok is False and '10' in msg

def test_validate_start_seq_rejects_non_positive():
    assert cutover.validate_start_seq(0, 0)[0] is False
    assert cutover.validate_start_seq(-3, 0)[0] is False

def test_cargo_source_maps_known_types():
    assert cutover.cargo_source('VCN_IMPORT') == ('vcn_cargo_declaration', 'bl_quantity')
    assert cutover.cargo_source('VCN_EXPORT') == ('vcn_export_cargo_declaration', 'bl_quantity')
    assert cutover.cargo_source('MBC') == ('mbc_customer_details', 'quantity')

def test_cargo_source_unknown_is_none():
    assert cutover.cargo_source('NOPE') is None
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest test_cutover.py -q -W ignore::DeprecationWarning`
Expected: FAIL — `ModuleNotFoundError: No module named 'modules.ADMIN.cutover'`.

- [ ] **Step 3: Create `modules/ADMIN/cutover.py` with the pure helpers**

```python
"""Go-live cutover logic: seed numbers, mark items billed, lock. Pure helpers
have no DB dependency so they are unit-testable; DB functions open their own
connection like the rest of the codebase."""
from database import get_db, get_cursor, get_module_config, save_module_config
import json

# cargo_source_type -> (declaration table, total-quantity column)
CARGO_SOURCES = {
    'VCN_IMPORT': ('vcn_cargo_declaration', 'bl_quantity'),
    'VCN_EXPORT': ('vcn_export_cargo_declaration', 'bl_quantity'),
    'MBC':        ('mbc_customer_details', 'quantity'),
}


def cargo_source(source_type):
    """Map a cargo_source_type to its (table, qty_column), or None if unknown."""
    return CARGO_SOURCES.get(source_type)


def validate_start_seq(start_seq, current_max):
    """A cutover start number must be a positive integer strictly greater than
    the highest number already issued (else it would be silently ignored)."""
    if not isinstance(start_seq, int) or start_seq <= 0:
        return False, 'Start number must be a positive integer.'
    if start_seq <= (current_max or 0):
        return False, (f'Start number must be greater than the highest number '
                       f'already issued ({current_max or 0}).')
    return True, ''
```

- [ ] **Step 4: Run to verify passing**

Run: `python -m pytest test_cutover.py -q -W ignore::DeprecationWarning`
Expected: PASS (all tests so far).

- [ ] **Step 5: Commit**

```bash
git add modules/ADMIN/cutover.py test_cutover.py
git commit -m "feat(cutover): pure guardrail + cargo-source mapping helpers"
```

---

## Task 5: Lock state + seed DB functions

**Files:**
- Modify: `modules/ADMIN/cutover.py`

- [ ] **Step 1: Add lock + audit + seed functions**

```python
def is_locked():
    cfg = get_module_config('ADMIN') or {}
    return str(cfg.get('cutover_locked', '0')) == '1'


def set_lock(locked, username):
    cfg = get_module_config('ADMIN') or {}
    cfg['cutover_locked'] = '1' if locked else '0'
    save_module_config('ADMIN', cfg)
    write_audit('lock' if locked else 'unlock', {}, username)


def write_audit(action, details, username):
    conn = get_db()
    cur = get_cursor(conn)
    cur.execute(
        'INSERT INTO cutover_audit (action, details, performed_by) VALUES (%s, %s, %s)',
        [action, json.dumps(details), username])
    conn.commit()
    conn.close()


def get_seeds():
    conn = get_db()
    cur = get_cursor(conn)
    cur.execute('SELECT * FROM cutover_seed ORDER BY seed_type, doc_series, financial_year')
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return rows


def _current_invoice_max(cur, doc_series, financial_year):
    cur.execute(
        'SELECT MAX(doc_series_seq) AS m FROM invoice_header WHERE doc_series=%s AND financial_year=%s',
        [doc_series, financial_year])
    return (cur.fetchone()['m'] or 0)


def _current_bill_max(cur):
    cur.execute(
        "SELECT MAX(CAST(SUBSTR(bill_number, 5) AS INTEGER)) AS m FROM bill_header WHERE bill_number LIKE 'BILL%%'")
    return (cur.fetchone()['m'] or 0)


def set_invoice_seed(doc_series, financial_year, start_seq, username):
    """Upsert an invoice seed after validating against the current max. Returns
    (ok, message)."""
    if is_locked():
        return False, 'Cutover is locked.'
    conn = get_db()
    cur = get_cursor(conn)
    current_max = _current_invoice_max(cur, doc_series, financial_year)
    ok, msg = validate_start_seq(start_seq, current_max)
    if not ok:
        conn.close()
        return False, msg
    cur.execute('''
        INSERT INTO cutover_seed (seed_type, doc_series, financial_year, start_seq, created_by, updated_by, updated_at)
        VALUES ('invoice', %s, %s, %s, %s, %s, now())
        ON CONFLICT (seed_type, doc_series, financial_year)
        DO UPDATE SET start_seq=EXCLUDED.start_seq, updated_by=EXCLUDED.updated_by, updated_at=now()
    ''', [doc_series, financial_year, start_seq, username, username])
    conn.commit()
    conn.close()
    write_audit('set_invoice_seed',
                {'doc_series': doc_series, 'financial_year': financial_year, 'start_seq': start_seq},
                username)
    return True, ''


def set_bill_seed(start_seq, username):
    if is_locked():
        return False, 'Cutover is locked.'
    conn = get_db()
    cur = get_cursor(conn)
    current_max = _current_bill_max(cur)
    ok, msg = validate_start_seq(start_seq, current_max)
    if not ok:
        conn.close()
        return False, msg
    cur.execute('''
        INSERT INTO cutover_seed (seed_type, doc_series, financial_year, start_seq, created_by, updated_by, updated_at)
        VALUES ('bill', '', '', %s, %s, %s, now())
        ON CONFLICT (seed_type, doc_series, financial_year)
        DO UPDATE SET start_seq=EXCLUDED.start_seq, updated_by=EXCLUDED.updated_by, updated_at=now()
    ''', [start_seq, username, username])
    conn.commit()
    conn.close()
    write_audit('set_bill_seed', {'start_seq': start_seq}, username)
    return True, ''
```

- [ ] **Step 2: Verify import**

Run: `python -c "from modules.ADMIN import cutover; print('ok', cutover.is_locked.__name__)"`
Expected: prints `ok is_locked` (no DB connection on import).

- [ ] **Step 3: Commit**

```bash
git add modules/ADMIN/cutover.py
git commit -m "feat(cutover): seed upsert, lock state, and audit DB functions"
```

---

## Task 6: Mark / unmark items billed

**Files:**
- Modify: `modules/ADMIN/cutover.py`

- [ ] **Step 1: Add mark/unmark functions**

```python
def _apply_billed(cur, cargo_items, service_ids, billed):
    """Flip billed flags. cargo_items: list of {'source_type','id'}.
    billed=True  -> is_billed=1, billed_quantity=<declared qty>
    billed=False -> is_billed=0, billed_quantity=0
    Returns counts dict. Raises ValueError on unknown source_type."""
    cargo_done, svc_done = 0, 0
    for item in cargo_items or []:
        mapping = cargo_source(item.get('source_type'))
        if not mapping:
            raise ValueError(f"Unknown cargo source_type: {item.get('source_type')}")
        table, qty_col = mapping
        if billed:
            # qty_col and table are trusted constants from CARGO_SOURCES (never user input)
            cur.execute(
                f"UPDATE {table} SET is_billed=1, billed_quantity={qty_col} WHERE id=%s",
                [item.get('id')])
        else:
            cur.execute(
                f"UPDATE {table} SET is_billed=0, billed_quantity=0 WHERE id=%s",
                [item.get('id')])
        cargo_done += cur.rowcount
    for sid in service_ids or []:
        if billed:
            cur.execute("UPDATE service_records SET is_billed=1 WHERE id=%s", [sid])
        else:
            cur.execute("UPDATE service_records SET is_billed=0, bill_id=NULL WHERE id=%s", [sid])
        svc_done += cur.rowcount
    return {'cargo': cargo_done, 'services': svc_done}


def mark_items_billed(cargo_items, service_ids, username, billed=True):
    """Mark (or unmark) the given items as billed. Pure status flag — no bill,
    no invoice, no SAP. Transactional. Returns (ok, message, counts)."""
    if is_locked():
        return False, 'Cutover is locked.', {}
    conn = get_db()
    cur = get_cursor(conn)
    try:
        counts = _apply_billed(cur, cargo_items, service_ids, billed)
        conn.commit()
    except Exception as e:
        conn.rollback()
        conn.close()
        return False, str(e), {}
    conn.close()
    write_audit('mark_billed' if billed else 'unmark_billed',
                {'cargo': cargo_items, 'services': service_ids, 'counts': counts},
                username)
    return True, '', counts
```

- [ ] **Step 2: Add a unit test for the dispatch logic (no DB) to `test_cutover.py`**

```python
class _FakeCursor:
    def __init__(self):
        self.calls = []
        self.rowcount = 1
    def execute(self, sql, params=None):
        self.calls.append((' '.join(sql.split()), params))


def test_apply_billed_marks_cargo_and_services():
    cur = _FakeCursor()
    counts = cutover._apply_billed(
        cur,
        [{'source_type': 'VCN_IMPORT', 'id': 7}],
        [42],
        billed=True,
    )
    assert counts == {'cargo': 1, 'services': 1}
    sqls = [c[0] for c in cur.calls]
    assert any("UPDATE vcn_cargo_declaration SET is_billed=1, billed_quantity=bl_quantity WHERE id=%s" in s for s in sqls)
    assert any("UPDATE service_records SET is_billed=1 WHERE id=%s" in s for s in sqls)


def test_apply_billed_unknown_source_raises():
    import pytest
    cur = _FakeCursor()
    with pytest.raises(ValueError):
        cutover._apply_billed(cur, [{'source_type': 'XXX', 'id': 1}], [], billed=True)
```

- [ ] **Step 3: Run tests**

Run: `python -m pytest test_cutover.py -q -W ignore::DeprecationWarning`
Expected: PASS (all cutover tests).

- [ ] **Step 4: Commit**

```bash
git add modules/ADMIN/cutover.py test_cutover.py
git commit -m "feat(cutover): mark/unmark items billed (pure flag, transactional)"
```

---

## Task 7: Admin routes (lock-aware, admin-only)

**Files:**
- Modify: `modules/ADMIN/views.py`

- [ ] **Step 1: Add routes** (append near the other `@bp.route` admin handlers; `admin_required` already defined in this file)

```python
from modules.ADMIN import cutover as cutover_mod


@bp.route('/api/cutover/state')
@admin_required
def cutover_state():
    return jsonify({'locked': cutover_mod.is_locked(), 'seeds': cutover_mod.get_seeds()})


@bp.route('/api/cutover/invoice-seed', methods=['POST'])
@admin_required
def cutover_invoice_seed():
    d = request.json or {}
    try:
        start_seq = int(d.get('start_seq'))
    except (TypeError, ValueError):
        return jsonify({'success': False, 'error': 'start_seq must be an integer'}), 400
    ok, msg = cutover_mod.set_invoice_seed(
        (d.get('doc_series') or '').strip().upper(),
        (d.get('financial_year') or '').strip(),
        start_seq,
        session.get('username'))
    return (jsonify({'success': True}) if ok
            else (jsonify({'success': False, 'error': msg}), 403 if 'locked' in msg.lower() else 400))


@bp.route('/api/cutover/bill-seed', methods=['POST'])
@admin_required
def cutover_bill_seed():
    d = request.json or {}
    try:
        start_seq = int(d.get('start_seq'))
    except (TypeError, ValueError):
        return jsonify({'success': False, 'error': 'start_seq must be an integer'}), 400
    ok, msg = cutover_mod.set_bill_seed(start_seq, session.get('username'))
    return (jsonify({'success': True}) if ok
            else (jsonify({'success': False, 'error': msg}), 403 if 'locked' in msg.lower() else 400))


@bp.route('/api/cutover/mark-billed', methods=['POST'])
@admin_required
def cutover_mark_billed():
    d = request.json or {}
    billed = bool(d.get('billed', True))
    ok, msg, counts = cutover_mod.mark_items_billed(
        d.get('cargo_items') or [], d.get('service_ids') or [],
        session.get('username'), billed=billed)
    if ok:
        return jsonify({'success': True, 'counts': counts})
    return jsonify({'success': False, 'error': msg}), 403 if 'locked' in msg.lower() else 400


@bp.route('/api/cutover/lock', methods=['POST'])
@admin_required
def cutover_lock():
    d = request.json or {}
    cutover_mod.set_lock(bool(d.get('locked', True)), session.get('username'))
    return jsonify({'success': True, 'locked': cutover_mod.is_locked()})
```

> Billable items for the mark-billed UI are fetched from the existing endpoint
> `GET /api/module/FIN01/customer-billables/<customer_type>/<customer_id>` — no new
> listing endpoint is needed.

- [ ] **Step 2: Verify app builds and routes register**

Run: `python -c "import app as a; print([r.rule for r in a.app.url_map.iter_rules() if '/api/cutover' in r.rule])"`
Expected: lists the 5 new `/admin/api/cutover/...` routes (prefixed with `/admin`).

- [ ] **Step 3: Commit**

```bash
git add modules/ADMIN/views.py
git commit -m "feat(cutover): admin-only, lock-aware cutover API routes"
```

---

## Task 8: Cutover tab UI in admin.html

**Files:**
- Modify: `templates/admin.html`

- [ ] **Step 1: Add the tab button** — after the existing `<button ... showTab('logs')>Logs</button>` in the `.admin-tabs` block, add:

```html
    <button class="tab-btn" onclick="showTab('cutover')">Cutover</button>
```

- [ ] **Step 2: Add the tab content** — after the last `<div id="...-tab" class="tab-content">...</div>` block, add:

```html
<div id="cutover-tab" class="tab-content">
    <div class="admin-section">
        <div id="cutover-locked-banner" style="display:none;background:#b00020;color:#fff;padding:8px 12px;border-radius:6px;margin-bottom:10px">
            Cutover is LOCKED. Unlock to make changes.
        </div>

        <h3>Document numbers</h3>
        <div>
            <label>Series</label> <input id="cut-inv-series" placeholder="e.g. DPPL">
            <label>FY</label> <input id="cut-inv-fy" placeholder="e.g. 26-27">
            <label>Start at</label> <input id="cut-inv-start" type="number" min="1">
            <button class="btn" onclick="cutSetInvoiceSeed()">Set invoice start</button>
        </div>
        <div style="margin-top:8px">
            <label>Bill start at</label> <input id="cut-bill-start" type="number" min="1">
            <button class="btn" onclick="cutSetBillSeed()">Set bill start</button>
        </div>
        <table class="admin-table" id="cutSeedTable" style="margin-top:10px">
            <thead><tr><th>Type</th><th>Series</th><th>FY</th><th>Start</th><th>By</th></tr></thead>
            <tbody></tbody>
        </table>

        <h3 style="margin-top:18px">Mark items billed (no invoice / no SAP)</h3>
        <div>
            <select id="cut-cust-type"><option>Customer</option><option>Agent</option></select>
            <input id="cut-cust-id" type="number" placeholder="customer/agent id">
            <button class="btn" onclick="cutLoadBillables()">Load items</button>
        </div>
        <div id="cut-billables" style="margin-top:10px"></div>
        <button class="btn" onclick="cutMarkBilled(true)">Mark selected billed</button>
        <button class="btn" onclick="cutMarkBilled(false)">Unmark selected</button>

        <h3 style="margin-top:18px">Lock</h3>
        <button class="btn" onclick="cutToggleLock()" id="cut-lock-btn">Mark cutover complete (lock)</button>
    </div>
</div>
```

- [ ] **Step 3: Add the JS** — before the closing `</script>` that contains `showTab`/`admin` functions, add:

```javascript
async function cutLoadState() {
    const r = await fetch('/admin/api/cutover/state');
    const s = await r.json();
    document.getElementById('cutover-locked-banner').style.display = s.locked ? 'block' : 'none';
    document.getElementById('cut-lock-btn').textContent = s.locked ? 'Unlock cutover' : 'Mark cutover complete (lock)';
    const tb = document.querySelector('#cutSeedTable tbody');
    tb.innerHTML = (s.seeds || []).map(x =>
        `<tr><td>${x.seed_type}</td><td>${x.doc_series||''}</td><td>${x.financial_year||''}</td><td>${x.start_seq}</td><td>${x.updated_by||x.created_by||''}</td></tr>`).join('');
}

async function _cutPost(url, body) {
    const r = await fetch(url, {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(body)});
    const j = await r.json();
    if (!j.success) { alert(j.error || 'Failed'); return null; }
    return j;
}

async function cutSetInvoiceSeed() {
    const ok = await _cutPost('/admin/api/cutover/invoice-seed', {
        doc_series: document.getElementById('cut-inv-series').value,
        financial_year: document.getElementById('cut-inv-fy').value,
        start_seq: parseInt(document.getElementById('cut-inv-start').value, 10),
    });
    if (ok) cutLoadState();
}

async function cutSetBillSeed() {
    const ok = await _cutPost('/admin/api/cutover/bill-seed', {
        start_seq: parseInt(document.getElementById('cut-bill-start').value, 10),
    });
    if (ok) cutLoadState();
}

async function cutLoadBillables() {
    const type = document.getElementById('cut-cust-type').value;
    const id = document.getElementById('cut-cust-id').value;
    const r = await fetch(`/api/module/FIN01/customer-billables/${type}/${id}`);
    const data = await r.json();
    const cargo = data.cargo_handling || [];
    const svcs = data.other_services || [];
    let html = '<b>Cargo</b><br>';
    html += cargo.map(c =>
        `<label><input type="checkbox" class="cut-cargo" data-st="${c.cargo_source_type}" data-id="${c.cargo_source_id}"> ${c.doc_label||''} — ${c.cargo_name||''} (${c.billable_quantity})</label><br>`).join('');
    html += '<br><b>Services</b><br>';
    html += svcs.map(s =>
        `<label><input type="checkbox" class="cut-svc" data-id="${s.id}"> ${s.service_name||''} (${s.id})</label><br>`).join('');
    document.getElementById('cut-billables').innerHTML = html;
}

async function cutMarkBilled(billed) {
    const cargo_items = [...document.querySelectorAll('.cut-cargo:checked')].map(e => ({source_type: e.dataset.st, id: parseInt(e.dataset.id,10)}));
    const service_ids = [...document.querySelectorAll('.cut-svc:checked')].map(e => parseInt(e.dataset.id,10));
    if (!cargo_items.length && !service_ids.length) { alert('Select at least one item'); return; }
    if (!confirm(`${billed ? 'Mark' : 'Unmark'} ${cargo_items.length} cargo + ${service_ids.length} services?`)) return;
    const ok = await _cutPost('/admin/api/cutover/mark-billed', {cargo_items, service_ids, billed});
    if (ok) { alert('Done'); cutLoadBillables(); }
}

async function cutToggleLock() {
    const locked = document.getElementById('cut-lock-btn').textContent.startsWith('Mark');
    if (!confirm(locked ? 'Lock cutover? Writes will be blocked.' : 'Unlock cutover?')) return;
    await _cutPost('/admin/api/cutover/lock', {locked});
    cutLoadState();
}

document.addEventListener('DOMContentLoaded', cutLoadState);
```

> Note: the billables item fields used here (`cargo_source_type`, `cargo_source_id`,
> `billable_quantity`, `doc_label`, `cargo_name`, service `id`/`service_name`) come
> from `_build_cargo_item` and the `other_services` list in
> `modules/FIN01/views.py` `get_customer_billables`. If a field name differs there,
> use that module's actual key.

- [ ] **Step 4: Manual smoke (optional, requires running app + admin login)**

Run the app, open `/admin`, click **Cutover**. Confirm: the tab loads, the seed table renders, and (without locking) setting an invoice start below the current max returns the guardrail error.

- [ ] **Step 5: Commit**

```bash
git add templates/admin.html
git commit -m "feat(cutover): admin Cutover tab UI (seeds, mark-billed, lock)"
```

---

## Task 9: Lock-gate test + final verification

**Files:**
- Test: `test_cutover.py`

- [ ] **Step 1: Add a lock-gate test (monkeypatched, no DB)**

```python
def test_set_invoice_seed_blocked_when_locked(monkeypatch):
    monkeypatch.setattr(cutover, 'is_locked', lambda: True)
    ok, msg = cutover.set_invoice_seed('DPPL', '26-27', 4568, 'tester')
    assert ok is False and 'locked' in msg.lower()


def test_mark_items_billed_blocked_when_locked(monkeypatch):
    monkeypatch.setattr(cutover, 'is_locked', lambda: True)
    ok, msg, counts = cutover.mark_items_billed([{'source_type':'VCN_IMPORT','id':1}], [], 'tester')
    assert ok is False and 'locked' in msg.lower() and counts == {}
```

- [ ] **Step 2: Run the full cutover test file**

Run: `python -m pytest test_cutover.py -v -W ignore::DeprecationWarning`
Expected: PASS — all tests (next_from_seed ×4, validate ×3, cargo_source ×2, _apply_billed ×2, lock-gate ×2).

- [ ] **Step 3: Full smoke (app build + whole suite + single head)**

Run:
```bash
python -c "import app as a; print('routes', len(list(a.app.url_map.iter_rules())))"
python -m pytest -q -W ignore::DeprecationWarning
python -m alembic heads
```
Expected: app builds; all tests pass; `alembic heads` shows the single head `d5e6f7a8b9c0`.

- [ ] **Step 4: Commit**

```bash
git add test_cutover.py
git commit -m "test(cutover): lock-gate coverage for seed + mark-billed"
```

---

## Self-Review (completed during authoring)

- **Spec coverage:** invoice seed (Tasks 2,3,5), bill seed (Tasks 3,5), exact-number model with `GREATEST` floor (Task 2), per-`(series,fy)` keying (Task 3,5), guardrail > current max (Tasks 4,5), mark-billed per-item whole-line, no invoice/SAP (Task 6), unmark (Task 6), admin-only + lock + audit (Tasks 5,7), UI tab (Task 8), tables + single head (Task 1), tests (Tasks 2,4,6,9). FDCN seeding intentionally out of scope (spec).
- **Placeholder scan:** no TBD/TODO; every code step shows full code; commands have expected output.
- **Type/name consistency:** `next_from_seed`, `lookup_seed`, `next_invoice_seq`, `validate_start_seq`, `cargo_source`, `CARGO_SOURCES`, `_apply_billed`, `mark_items_billed`, `set_invoice_seed`, `set_bill_seed`, `is_locked`, `set_lock`, `write_audit`, `get_seeds` are used consistently across tasks and routes.
- **Known assumption to verify at execution:** the `other_services`/cargo field names returned by `get_customer_billables` (Task 8 note) — confirm against `modules/FIN01/views.py` when wiring the UI.
