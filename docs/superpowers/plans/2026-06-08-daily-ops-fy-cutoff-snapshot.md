# Daily-Ops FY × Cargo-Type Cutoff Snapshot Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remodel the RP01 daily-ops cutoff to store an auto-computed Financial-Year × cargo-type throughput snapshot (FY 2012-2013 → cutoff FY) instead of the old `mbc_cargo` / `cargo_handled` grids, restricted to admins.

**Architecture:** Pure FY-bucketing helpers live in a new `daily_ops/model.py` (TDD'd, no DB). The cutoff POST handler computes the snapshot from `rp01_historical_lueu` + `lueu_lines` (joined to `vessel_cargo` for cargo type) and stores it as JSON in the existing single-row `daily_ops_cutoff` table. An Alembic data migration clears the incompatible old payload. The frontend shows an admin-only read-only matrix.

**Tech Stack:** Python 3, Flask, psycopg2 (dict cursor), Alembic, PostgreSQL, vanilla JS, pytest.

**Spec:** `docs/superpowers/specs/2026-06-08-daily-ops-fy-cutoff-snapshot-design.md`

---

## File Structure

- **Create** `modules/RP01/RP01/daily_ops/model.py` — pure helpers: `fy_label(start_year)`, `build_fy_throughput(rows)`. No DB, no Flask.
- **Create** `test_daily_ops_cutoff.py` (repo root) — pure-function unit tests for the helpers.
- **Modify** `modules/RP01/RP01/daily_ops/views.py` — add `_compute_fy_throughput()`, reshape GET/POST cutoff handlers (admin guard + snapshot), simplify `_fetch_cargo_handled()`, remove `_load_cutoff()`, pass `is_admin` to the template.
- **Create** `alembic/versions/<rev>_daily_ops_cutoff_fy_throughput.py` — data migration reshaping `cutoff_values`.
- **Modify** `modules/RP01/RP01/daily_ops/daily_ops.html` — admin-only button, FY-matrix modal, rewritten JS.

**Testing discipline (matches this repo):** pure logic is TDD'd with pytest; DB / Flask / Alembic / browser integration is verified manually (the repo has no Flask-client or DB tests — see `test_rp01_historical.py`, `test_cutover.py`).

---

## Task 1: Pure FY-bucketing helpers (TDD)

**Files:**
- Create: `modules/RP01/RP01/daily_ops/model.py`
- Test: `test_daily_ops_cutoff.py`

- [ ] **Step 1: Write the failing tests**

Create `test_daily_ops_cutoff.py`:

```python
"""Pure-function unit tests for daily-ops FY cutoff helpers (no DB)."""
from modules.RP01.RP01.daily_ops import model


# ── fy_label ─────────────────────────────────────────────────────────────────
def test_fy_label_basic():
    assert model.fy_label(2012) == '2012-2013'

def test_fy_label_cutoff_year():
    assert model.fy_label(2026) == '2026-2027'


# ── build_fy_throughput ──────────────────────────────────────────────────────
def test_build_fy_throughput_nests_by_fy_and_cargo_type():
    rows = [
        {'fy_start': 2012, 'cargo_type': 'IBRM',   'qty': 100},
        {'fy_start': 2012, 'cargo_type': 'Fluxes', 'qty': 50},
        {'fy_start': 2026, 'cargo_type': 'IBRM',   'qty': 5},
    ]
    out = model.build_fy_throughput(rows)
    assert out == {
        '2012-2013': {'IBRM': 100.0, 'Fluxes': 50.0},
        '2026-2027': {'IBRM': 5.0},
    }

def test_build_fy_throughput_coerces_to_float():
    out = model.build_fy_throughput([{'fy_start': 2020, 'cargo_type': 'CBRM', 'qty': '12'}])
    assert out == {'2020-2021': {'CBRM': 12.0}}

def test_build_fy_throughput_skips_zero_and_none():
    rows = [
        {'fy_start': 2020, 'cargo_type': 'CBRM', 'qty': 0},
        {'fy_start': 2020, 'cargo_type': 'IBRM', 'qty': None},
        {'fy_start': 2020, 'cargo_type': 'Clinker', 'qty': 7},
    ]
    assert model.build_fy_throughput(rows) == {'2020-2021': {'Clinker': 7.0}}

def test_build_fy_throughput_null_cargo_type_becomes_others():
    out = model.build_fy_throughput([{'fy_start': 2019, 'cargo_type': None, 'qty': 3}])
    assert out == {'2019-2020': {'OTHERS': 3.0}}

def test_build_fy_throughput_empty_rows():
    assert model.build_fy_throughput([]) == {}
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest test_daily_ops_cutoff.py -v`
Expected: FAIL — `ModuleNotFoundError`/`AttributeError` (no `model` module / functions).

- [ ] **Step 3: Write the minimal implementation**

Create `modules/RP01/RP01/daily_ops/model.py`:

```python
"""Pure helpers for the daily-ops FY cutoff snapshot. No DB, no Flask."""


def fy_label(start_year):
    """Financial-year label for an April-start FY, e.g. 2012 -> '2012-2013'."""
    start_year = int(start_year)
    return f"{start_year}-{start_year + 1}"


def build_fy_throughput(rows):
    """Nest aggregated rows into {fy_label: {cargo_type: float_qty}}.

    rows: iterable of mappings with keys 'fy_start' (int April-start year),
    'cargo_type' (str or None) and 'qty' (number-ish). Zero/None quantities
    are skipped; a missing cargo_type becomes 'OTHERS'.
    """
    out = {}
    for r in rows:
        qty = float(r.get('qty') or 0)
        if qty == 0:
            continue
        cargo_type = r.get('cargo_type') or 'OTHERS'
        label = fy_label(r['fy_start'])
        bucket = out.setdefault(label, {})
        bucket[cargo_type] = bucket.get(cargo_type, 0.0) + qty
    return out
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest test_daily_ops_cutoff.py -v`
Expected: PASS (7 passed).

- [ ] **Step 5: Commit**

```bash
git add modules/RP01/RP01/daily_ops/model.py test_daily_ops_cutoff.py
git commit -m "feat(rp01): pure FY-bucketing helpers for cutoff snapshot"
```

---

## Task 2: Snapshot computation + cutoff API reshape

**Files:**
- Modify: `modules/RP01/RP01/daily_ops/views.py` (imports near top; `daily_ops_cutoff_get` ~82-105; `daily_ops_cutoff_save` ~108-132; add `_compute_fy_throughput` after the cutoff helper block)

- [ ] **Step 1: Import the pure helpers**

At the top of `views.py`, after `from database import get_db, get_cursor` (line 8), add:

```python
from .model import build_fy_throughput
```

- [ ] **Step 2: Add the snapshot computation helper**

Insert after the existing `_load_cutoff()` block (just before `# ── Data fetchers ──`, ~line 151). This mirrors the join/FY logic already used in `_fetch_cargo_type_throughput`:

```python
def _compute_fy_throughput(cutoff_date):
    """Aggregate quantity by (financial year, cargo type) up to cutoff_date.

    Unions historical (rp01_historical_lueu) and live (lueu_lines) rows, maps
    cargo_name -> cargo_type via the VCG01 vessel_cargo master, buckets by
    April-start financial year, and returns {fy_label: {cargo_type: qty}}.
    The cutoff FY is naturally partial (entry_date <= cutoff_date).
    """
    conn = get_db()
    cur = get_cursor(conn)
    cur.execute("""
        WITH throughput AS (
            SELECT
                COALESCE(vc.cargo_type, 'OTHERS') AS cargo_type,
                (EXTRACT(YEAR FROM TO_DATE(l.entry_date, 'YYYY-MM-DD'))::int
                    - CASE WHEN EXTRACT(MONTH FROM TO_DATE(l.entry_date, 'YYYY-MM-DD')) < 4
                           THEN 1 ELSE 0 END) AS fy_start,
                COALESCE(l.quantity, 0) AS quantity
            FROM lueu_lines l
            LEFT JOIN vessel_cargo vc
                ON UPPER(TRIM(vc.cargo_name)) = UPPER(TRIM(l.cargo_name))
            WHERE l.is_deleted = false
              AND l.cargo_name IS NOT NULL
              AND TO_DATE(l.entry_date, 'YYYY-MM-DD') <= %s::date

            UNION ALL

            SELECT
                COALESCE(vc.cargo_type, 'OTHERS') AS cargo_type,
                (EXTRACT(YEAR FROM h.entry_date)::int
                    - CASE WHEN EXTRACT(MONTH FROM h.entry_date) < 4
                           THEN 1 ELSE 0 END) AS fy_start,
                COALESCE(h.quantity, 0) AS quantity
            FROM rp01_historical_lueu h
            LEFT JOIN vessel_cargo vc
                ON UPPER(TRIM(vc.cargo_name)) = UPPER(TRIM(h.cargo_name))
            WHERE h.entry_date <= %s::date
        )
        SELECT
            fy_start,
            cargo_type,
            SUM(quantity) AS qty
        FROM throughput
        GROUP BY fy_start, cargo_type
        ORDER BY fy_start, cargo_type
    """, (cutoff_date, cutoff_date))

    rows = cur.fetchall()
    conn.close()
    return build_fy_throughput(rows)
```

- [ ] **Step 3: Reshape the GET handler**

Replace the empty-default branch in `daily_ops_cutoff_get` (the `return jsonify({...})` at ~lines 101-105) so the default uses the new shape:

```python
    return jsonify({
        'id':            None,
        'cutoff_date':   '',
        'cutoff_values': {'fy_throughput': {}},
    })
```

(The `if row:` branch is unchanged — it returns the stored JSON as-is.)

- [ ] **Step 4: Reshape the POST handler with admin guard + snapshot**

Replace the body of `daily_ops_cutoff_save` (~lines 110-132) with:

```python
def daily_ops_cutoff_save():
    """Admin-only: set the cutoff date and store the computed FY snapshot."""
    if not session.get('is_admin'):
        return Response('Admin access required', status=403)

    data        = request.get_json(force=True)
    cutoff_date = data.get('cutoff_date', '')

    if not cutoff_date:
        return Response('cutoff_date is required', status=400)

    fy_throughput = _compute_fy_throughput(cutoff_date)
    values_json   = json.dumps({'fy_throughput': fy_throughput})
    user          = session.get('username', '')

    conn = get_db()
    cur  = get_cursor(conn)
    # Single-row table: clear then insert.
    cur.execute("DELETE FROM daily_ops_cutoff")
    cur.execute("""
        INSERT INTO daily_ops_cutoff (cutoff_date, cutoff_values, created_by)
        VALUES (%s, %s, %s)
    """, (cutoff_date, values_json, user))
    conn.commit()
    conn.close()
    return jsonify({'ok': True, 'fy_throughput': fy_throughput})
```

- [ ] **Step 5: Verify pure tests still pass and imports resolve**

Run: `python -m pytest test_daily_ops_cutoff.py -v`
Expected: PASS (unchanged).

Run: `python -c "import ast; ast.parse(open('modules/RP01/RP01/daily_ops/views.py', encoding='utf-8').read()); print('views.py parses OK')"`
Expected: `views.py parses OK`

- [ ] **Step 6: Manual verification (DB + Flask)**

Start the app, log in as an admin, open the daily-ops page, and (using the rewritten UI from Task 5, or a curl with an admin session cookie) POST a cutoff date:

```bash
# As admin session:
curl -s -X POST http://localhost:5000/api/module/RP01/daily-ops/cutoff \
  -H 'Content-Type: application/json' --cookie "$COOKIE" \
  -d '{"cutoff_date":"2026-05-01"}'
```
Expected: `200` with `{"ok": true, "fy_throughput": {"2012-2013": {...}, ..., "2026-2027": {...}}}`.
Then `GET` the same endpoint → `cutoff_values.fy_throughput` matches.
As a **non-admin** session, the same POST returns `403`.

> If app start / login differs locally, verify by calling `_compute_fy_throughput('2026-05-01')` in a Flask shell and asserting FY keys span `2012-2013` through `2026-2027` with the last FY truncated at the cutoff.

- [ ] **Step 7: Commit**

```bash
git add modules/RP01/RP01/daily_ops/views.py
git commit -m "feat(rp01): compute + store FY cutoff snapshot, admin-only"
```

---

## Task 3: Retire the route-cutoff merge in `_fetch_cargo_handled`

**Files:**
- Modify: `modules/RP01/RP01/daily_ops/views.py` (`_load_cutoff` ~137-150; `_fetch_cargo_handled` ~1091-1232)

- [ ] **Step 1: Remove the cutoff load + merge from `_fetch_cargo_handled`**

In `_fetch_cargo_handled`, delete these blocks:
- The cutoff load lines (`cutoff_date_str, cutoff_vals = _load_cutoff()` through the `use_cutoff = (...)` assignment, ~lines 1091-1112).
- The entire `if use_cutoff: ... else:` branch around month-data (~lines 1191-1231).

Replace the month-data section so `month_dict` is always the live query. The Month Data block becomes:

```python
    # Day Data (Previous Date)
    day_dict = _group_routes(
        _period(ws_str, we_str)
    )

    # Month Data (live, 1st of month -> report date)
    month_dict = _group_routes(
        _period(
            month_start.strftime('%Y-%m-%d %H:%M:%S'),
            report_date.strftime('%Y-%m-%d 23:59:59')
        )
    )

    conn.close()

    day_rows = sorted(day_dict.items())
    month_rows = sorted(month_dict.items())

    return day_rows, month_rows
```

(Leave the `_period` and `_group_routes` inner helpers and the window/`month_start` setup above them unchanged.)

- [ ] **Step 2: Remove the now-unused `_load_cutoff` helper**

Delete the `_load_cutoff()` function (~lines 137-150) and its `# ── Cutoff helper ──` comment header. (Its only live caller was the block removed in Step 1; the other reference is inside a commented-out block.)

- [ ] **Step 3: Verify the module still parses and references are gone**

Run: `python -c "import ast; ast.parse(open('modules/RP01/RP01/daily_ops/views.py', encoding='utf-8').read()); print('OK')"`
Expected: `OK`

Run: `git grep -n "_load_cutoff(" -- modules/RP01/RP01/daily_ops/views.py`
Expected: no output, OR only matches inside the commented-out block (lines prefixed with `#`). Confirm there is no live (uncommented) call.

- [ ] **Step 4: Manual verification**

In a Flask shell (or via the daily-ops preview), call `_fetch_cargo_handled(date(2026,5,2))`.
Expected: returns `(day_rows, month_rows)` of `(route_group, qty)` tuples with no exception and no dependence on `daily_ops_cutoff`.

- [ ] **Step 5: Commit**

```bash
git add modules/RP01/RP01/daily_ops/views.py
git commit -m "refactor(rp01): drop route-cutoff merge from cargo-handled (now live)"
```

---

## Task 4: Alembic data migration

**Files:**
- Create: `alembic/versions/c1d2e3f4a5b6_daily_ops_cutoff_fy_throughput.py`

- [ ] **Step 1: Create the migration**

Create `alembic/versions/c1d2e3f4a5b6_daily_ops_cutoff_fy_throughput.py` (down_revision is the current head `f1a2b3c4d5e6`):

```python
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
```

- [ ] **Step 2: Verify Alembic sees a single linear head**

Run: `python -m alembic heads`
Expected: `c1d2e3f4a5b6 (head)` (single head).

Run: `python -m alembic history -r f1a2b3c4d5e6:c1d2e3f4a5b6`
Expected: shows `f1a2b3c4d5e6 -> c1d2e3f4a5b6`.

- [ ] **Step 3: Apply, roll back, re-apply (manual DB verification)**

```bash
python -m alembic upgrade head
```
Then in psql / a DB shell:
```sql
SELECT cutoff_date, cutoff_values FROM daily_ops_cutoff;
```
Expected: each row's `cutoff_values` is `{"fy_throughput": {}}`; `cutoff_date` unchanged.

```bash
python -m alembic downgrade -1   # cutoff_values -> {"mbc_cargo": {}, "cargo_handled": {}}
python -m alembic upgrade head   # back to {"fy_throughput": {}}
```
Expected: both complete without error.

- [ ] **Step 4: Commit**

```bash
git add alembic/versions/c1d2e3f4a5b6_daily_ops_cutoff_fy_throughput.py
git commit -m "migrate(rp01): reshape daily_ops_cutoff to fy_throughput payload"
```

---

## Task 5: Frontend — admin-only FY-matrix cutoff modal

**Files:**
- Modify: `modules/RP01/RP01/daily_ops/views.py` (`daily_ops_index` ~60-63)
- Modify: `modules/RP01/RP01/daily_ops/daily_ops.html` (button ~225; modal body ~257-282; JS ~291-end)

- [ ] **Step 1: Pass `is_admin` to the template**

In `daily_ops_index` (~line 62), update the render call:

```python
@bp.route('/module/RP01/daily-ops/')
@login_required
def daily_ops_index():
    return render_template(
        'daily_ops/daily_ops.html',
        username=session.get('username'),
        is_admin=session.get('is_admin'),
    )
```

- [ ] **Step 2: Gate the Cutoff Settings button on admin**

In `daily_ops.html`, wrap the button at line 225:

```html
    {% if is_admin %}
    <button type="button" class="filter-btn secondary" onclick="openCutoffModal()">Cutoff Settings</button>
    {% endif %}
```

- [ ] **Step 3: Replace the modal body (grids → FY matrix preview)**

Replace the two grid blocks (the `<!-- MBC Cargo Handling -->` through the end of the `route-cutoff-grid` table, ~lines 257-282) with:

```html
            <div class="cutoff-hint">
                Saving recomputes the FY &times; cargo-type throughput snapshot from
                all data up to the cutoff date. The cutoff financial year is partial
                (1 April &rarr; cutoff date).
            </div>

            <div class="modal-section-title">FY &times; Cargo Type Throughput (up to cutoff)</div>
            <div id="fy-cutoff-preview" style="overflow-x:auto;">
                <p class="cutoff-hint">Pick a cutoff date and click Save to compute the snapshot.</p>
            </div>
```

Also replace the old date hint at ~lines 252-255 if it still references "MTD totals up to this date's 07:00 AM" — it is superseded by the hint above; delete the stale one so only the new hint remains.

- [ ] **Step 4: Rewrite the modal JavaScript**

Replace the JS from `const MBC_OWNERS = ...` (line 292) down through `collectRouteData()` and `saveCutoff()` (the whole cutoff section ending ~line 500) with the following. Keep `downloadReport()` and `previewReport()` untouched; keep the `DOMContentLoaded` handler but it already calls `loadCutoffStatus()`.

```javascript
/* ── Cutoff Modal (FY x cargo-type snapshot) ───────────────────────────── */

function loadCutoffStatus() {
    fetch('/api/module/RP01/daily-ops/cutoff')
        .then(r => r.json())
        .then(data => {
            const bar = document.getElementById('cutoff-status-bar');
            if (data.cutoff_date) {
                bar.innerHTML = `<div class="cutoff-status saved">Cutoff active: FY snapshot stored up to <b>${data.cutoff_date}</b>.</div>`;
            } else {
                bar.innerHTML = `<div class="cutoff-status none">No cutoff configured — FY values come from live database queries.</div>`;
            }
        })
        .catch(() => {});
}

function openCutoffModal() {
    document.getElementById('cutoff-modal').classList.add('open');
    fetch('/api/module/RP01/daily-ops/cutoff')
        .then(r => r.json())
        .then(data => {
            document.getElementById('cutoff-date').value = data.cutoff_date || '';
            renderFYPreview((data.cutoff_values || {}).fy_throughput || {});
        })
        .catch(() => renderFYPreview({}));
}

function closeCutoffModal() {
    document.getElementById('cutoff-modal').classList.remove('open');
}

// Close modal on overlay click
document.addEventListener('click', function(e) {
    if (e.target.id === 'cutoff-modal') closeCutoffModal();
});

function renderFYPreview(fyData) {
    const host = document.getElementById('fy-cutoff-preview');
    const fys = Object.keys(fyData).sort();
    if (fys.length === 0) {
        host.innerHTML = '<p class="cutoff-hint">No snapshot yet. Pick a cutoff date and click Save.</p>';
        return;
    }
    // Union of cargo types across all FYs, as columns.
    const typeSet = new Set();
    fys.forEach(fy => Object.keys(fyData[fy]).forEach(t => typeSet.add(t)));
    const types = Array.from(typeSet).sort();

    const fmt = n => (Number(n) || 0).toLocaleString('en-IN', { maximumFractionDigits: 0 });

    let html = '<table class="cutoff-grid"><thead><tr><th>FY</th>';
    types.forEach(t => { html += `<th>${t}</th>`; });
    html += '<th>Total</th></tr></thead><tbody>';

    types.forEach(() => {});  // no-op to keep types in scope
    const colTotals = {};
    types.forEach(t => colTotals[t] = 0);
    let grand = 0;

    fys.forEach(fy => {
        let rowTotal = 0;
        html += `<tr><td>${fy}</td>`;
        types.forEach(t => {
            const v = fyData[fy][t] || 0;
            rowTotal += v; colTotals[t] += v; grand += v;
            html += `<td style="text-align:right">${fmt(v)}</td>`;
        });
        html += `<td style="text-align:right"><b>${fmt(rowTotal)}</b></td></tr>`;
    });

    html += '<tr style="font-weight:bold;background:#f2f2f2"><td>Total</td>';
    types.forEach(t => { html += `<td style="text-align:right">${fmt(colTotals[t])}</td>`; });
    html += `<td style="text-align:right">${fmt(grand)}</td></tr>`;
    html += '</tbody></table>';
    host.innerHTML = html;
}

function saveCutoff() {
    const cutoffDate = document.getElementById('cutoff-date').value;
    if (!cutoffDate) {
        alert('Please select a cutoff date.');
        return;
    }

    const btn = document.getElementById('save-cutoff-btn');
    btn.disabled = true;
    btn.textContent = 'Computing…';

    fetch('/api/module/RP01/daily-ops/cutoff', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ cutoff_date: cutoffDate })
    })
    .then(r => {
        if (r.status === 403) throw new Error('Admin access required.');
        if (!r.ok) throw new Error('Save failed');
        return r.json();
    })
    .then(data => {
        renderFYPreview(data.fy_throughput || {});
        loadCutoffStatus();
    })
    .catch(err => {
        alert(err.message || 'Failed to save cutoff settings.');
    })
    .finally(() => {
        btn.disabled = false;
        btn.textContent = 'Save';
    });
}
```

> Note: the `types.forEach(() => {});` no-op line in the draft above is unnecessary — omit it when implementing. The `colTotals`/`grand` accumulation is the real logic.

- [ ] **Step 5: Manual verification (browser)**

1. As **admin**: load `/module/RP01/daily-ops/`. The "Cutoff Settings" button is visible. Open it → existing snapshot (if any) renders as an FY × cargo-type table. Pick `2026-05-01`, Save → button shows "Computing…", then the table fills with FY rows `2012-2013` … `2026-2027` (last row partial), column + grand totals, and the status bar reads "Cutoff active … up to 2026-05-01".
2. As **non-admin**: load the page → the "Cutoff Settings" button is absent. (A direct POST returns 403 per Task 2.)
3. Confirm the daily-ops Excel/preview download still works (regression check — unaffected code path).

- [ ] **Step 6: Commit**

```bash
git add modules/RP01/RP01/daily_ops/views.py modules/RP01/RP01/daily_ops/daily_ops.html
git commit -m "feat(rp01): admin-only FY x cargo-type cutoff modal"
```

---

## Task 6: Mark spec done + full test run

**Files:**
- Modify: `docs/superpowers/specs/2026-06-08-daily-ops-fy-cutoff-snapshot-design.md`

- [ ] **Step 1: Update spec status**

Change the `**Status:**` line near the top from `Design — pending user review` to `Implemented 2026-06-08`.

- [ ] **Step 2: Run the full pure-test suite**

Run: `python -m pytest -q`
Expected: all tests pass (existing suite + the 7 new `test_daily_ops_cutoff.py` tests).

- [ ] **Step 3: Commit**

```bash
git add docs/superpowers/specs/2026-06-08-daily-ops-fy-cutoff-snapshot-design.md
git commit -m "docs(rp01): mark FY cutoff snapshot spec implemented"
```

---

## Self-Review notes

- **Spec coverage:** data model (Task 2/4 JSON shape), migration (Task 4), snapshot compute + admin guard + GET/POST reshape (Task 2), `_fetch_cargo_handled` retirement (Task 3), frontend admin-only matrix (Task 5), FY convention + 2012-13→cutoff range (Task 1 helpers + Task 2 SQL), testing (Task 1 pure tests + manual steps) — all covered.
- **Types/names consistent:** `fy_label`, `build_fy_throughput`, `_compute_fy_throughput`, JSON key `fy_throughput`, row keys `fy_start`/`cargo_type`/`qty`, JS `renderFYPreview` — used identically across tasks.
- **Revision id** `c1d2e3f4a5b6` is illustrative; if it collides with an existing file, generate a fresh id and keep `down_revision = 'f1a2b3c4d5e6'`.
