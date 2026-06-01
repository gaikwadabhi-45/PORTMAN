# LUEU01 Quantity & Time-Overlap Validation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Block LUEU01 rows from saving a quantity that exceeds the barge/MBC remaining trip quantity, or a From/To time that overlaps another operation on the same equipment+date — by rejecting only the offending field(s), saving the rest, and showing a popup.

**Architecture:** All decision logic lives in pure, DB-free functions in `modules/LUEU01/model.py` (`_hhmm_to_minutes`, `_intervals_overlap`, `compute_rejections`) that are unit-tested directly. Thin DB helpers (`_resolve_trip_quantity`, `_overlap_candidates`) fetch the numbers, and `save_line` orchestrates: fetch → decide → blank rejected fields → write → return `{'id', 'rejections'}`. The Flask `save_data` view passes the dict through; the Tabulator client (`lueu01.html`) blanks the rejected cells and shows one summary popup.

**Tech Stack:** Python 3 + Flask + psycopg (PostgreSQL), Tabulator.js, pytest (root-level test files, pure functions, no live DB — mirrors `test_sap_builder.py`).

---

## File Structure

- **Modify** `modules/LUEU01/model.py` — add 5 functions; wire validation into `save_line` and change its return type.
- **Modify** `modules/LUEU01/views.py` — `save_data` returns the full result dict.
- **Modify** `modules/LUEU01/lueu01.html` — `saveAll` reacts to `rejections`; add a rejection popup modal + `showRejectionPopup`.
- **Create** `test_lueu01_validation.py` (repo root) — pure-function unit tests.

---

### Task 1: Pure time-overlap helpers

**Files:**
- Modify: `modules/LUEU01/model.py` (add functions near top, after the `from datetime ...` import on line 2)
- Test: `test_lueu01_validation.py` (repo root)

- [ ] **Step 1: Write the failing test**

Create `test_lueu01_validation.py`:

```python
"""
Pure-function unit tests for LUEU01 save-time validation:
- time-range overlap math (overnight aware)
- quantity-over-trip and overlap rejection decisions

These import only pure helpers from modules.LUEU01.model and never touch the
database (mirrors test_sap_builder.py).
"""
from modules.LUEU01 import model


# ── _hhmm_to_minutes ────────────────────────────────────────────────────────

def test_hhmm_to_minutes_parses_valid():
    assert model._hhmm_to_minutes('06:30') == 390
    assert model._hhmm_to_minutes('00:00') == 0
    assert model._hhmm_to_minutes('23:59') == 1439

def test_hhmm_to_minutes_rejects_bad():
    assert model._hhmm_to_minutes('') is None
    assert model._hhmm_to_minutes(None) is None
    assert model._hhmm_to_minutes('7') is None
    assert model._hhmm_to_minutes('25:00') is None
    assert model._hhmm_to_minutes('ab:cd') is None


# ── _intervals_overlap ──────────────────────────────────────────────────────

def test_overlap_true_when_ranges_intersect():
    assert model._intervals_overlap('06:00', '08:00', '07:00', '09:00') is True

def test_overlap_false_when_adjacent():
    # back-to-back, no shared minute
    assert model._intervals_overlap('06:00', '08:00', '08:00', '10:00') is False

def test_overlap_false_when_separate():
    assert model._intervals_overlap('06:00', '07:00', '09:00', '10:00') is False

def test_overlap_overnight_wrap():
    # 23:00-02:00 wraps midnight and must overlap 01:00-03:00
    assert model._intervals_overlap('23:00', '02:00', '01:00', '03:00') is True

def test_overlap_false_when_incomplete():
    assert model._intervals_overlap('06:00', '', '07:00', '09:00') is False
    assert model._intervals_overlap('06:00', '08:00', None, '09:00') is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest test_lueu01_validation.py -v`
Expected: FAIL — `AttributeError: module 'modules.LUEU01.model' has no attribute '_hhmm_to_minutes'`

- [ ] **Step 3: Write minimal implementation**

In `modules/LUEU01/model.py`, immediately after line 2 (`from datetime import datetime, date, timedelta`), add:

```python


def _hhmm_to_minutes(t):
    """'HH:MM' -> minutes since midnight, or None if not parseable."""
    if not t or not isinstance(t, str):
        return None
    parts = t.strip().split(':')
    if len(parts) < 2:
        return None
    try:
        h = int(parts[0]); m = int(parts[1])
    except ValueError:
        return None
    if not (0 <= h <= 23 and 0 <= m <= 59):
        return None
    return h * 60 + m


def _intervals_overlap(from_a, to_a, from_b, to_b):
    """True if two HH:MM ranges on the same date intersect.

    A range whose end <= start is treated as crossing midnight (end += 1440),
    matching the overnight convention used by calcDiffHrs in the template.
    Returns False if either range is incomplete/unparseable.
    """
    fa = _hhmm_to_minutes(from_a); ta = _hhmm_to_minutes(to_a)
    fb = _hhmm_to_minutes(from_b); tb = _hhmm_to_minutes(to_b)
    if fa is None or ta is None or fb is None or tb is None:
        return False
    if ta <= fa:
        ta += 1440
    if tb <= fb:
        tb += 1440
    return fa < tb and fb < ta
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest test_lueu01_validation.py -v`
Expected: PASS (8 tests)

- [ ] **Step 5: Commit**

```bash
git add modules/LUEU01/model.py test_lueu01_validation.py
git commit -m "feat(LUEU01): pure time-overlap helpers with overnight handling"
```

---

### Task 2: Pure rejection-decision orchestrator

**Files:**
- Modify: `modules/LUEU01/model.py` (add `compute_rejections` after `_intervals_overlap`)
- Test: `test_lueu01_validation.py`

- [ ] **Step 1: Write the failing test**

Append to `test_lueu01_validation.py`:

```python
# ── compute_rejections ──────────────────────────────────────────────────────

def _row(**over):
    base = {
        'source_type': 'VCN', 'source_id': 1, 'source_display': 'VCN1 / SHIP',
        'barge_name': 'BARGE-A / 1', 'equipment_name': 'CRANE-1',
        'entry_date': '2026-06-01', 'from_time': '06:00', 'to_time': '08:00',
        'quantity': 100.0,
    }
    base.update(over)
    return base

def test_quantity_under_remaining_is_kept():
    clean, rej = model.compute_rejections(_row(quantity=50.0),
                                          trip_expected=200.0, trip_handled=100.0,
                                          overlap_candidates=[])
    assert clean['quantity'] == 50.0
    assert rej == []

def test_quantity_over_remaining_is_blanked_and_reported():
    clean, rej = model.compute_rejections(_row(quantity=150.0),
                                          trip_expected=200.0, trip_handled=100.0,
                                          overlap_candidates=[])
    assert clean['quantity'] is None
    assert len(rej) == 1
    assert rej[0]['field'] == 'quantity'
    assert rej[0]['remaining'] == 100.0
    assert rej[0]['attempted'] == 150.0

def test_quantity_not_checked_when_no_expected():
    clean, rej = model.compute_rejections(_row(quantity=9999.0),
                                          trip_expected=0.0, trip_handled=0.0,
                                          overlap_candidates=[])
    assert clean['quantity'] == 9999.0
    assert rej == []

def test_quantity_exactly_remaining_is_kept():
    clean, rej = model.compute_rejections(_row(quantity=100.0),
                                          trip_expected=200.0, trip_handled=100.0,
                                          overlap_candidates=[])
    assert clean['quantity'] == 100.0
    assert rej == []

def test_time_overlap_blanks_both_times_and_reports():
    clean, rej = model.compute_rejections(
        _row(from_time='07:00', to_time='09:00'),
        trip_expected=0.0, trip_handled=0.0,
        overlap_candidates=[('06:00', '08:00')])
    assert clean['from_time'] is None
    assert clean['to_time'] is None
    assert len(rej) == 1
    assert rej[0]['field'] == 'time'
    assert rej[0]['conflict'] == {'from_time': '06:00', 'to_time': '08:00'}

def test_no_time_overlap_keeps_times():
    clean, rej = model.compute_rejections(
        _row(from_time='09:00', to_time='10:00'),
        trip_expected=0.0, trip_handled=0.0,
        overlap_candidates=[('06:00', '08:00')])
    assert clean['from_time'] == '09:00'
    assert clean['to_time'] == '10:00'
    assert rej == []

def test_both_rejections_can_fire_together():
    clean, rej = model.compute_rejections(
        _row(quantity=150.0, from_time='07:00', to_time='09:00'),
        trip_expected=200.0, trip_handled=100.0,
        overlap_candidates=[('06:00', '08:00')])
    fields = {r['field'] for r in rej}
    assert fields == {'quantity', 'time'}
    assert clean['quantity'] is None
    assert clean['from_time'] is None
    assert clean['to_time'] is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest test_lueu01_validation.py -k compute_rejections -v`
Expected: FAIL — `AttributeError: module 'modules.LUEU01.model' has no attribute 'compute_rejections'`

- [ ] **Step 3: Write minimal implementation**

In `modules/LUEU01/model.py`, immediately after the `_intervals_overlap` function added in Task 1, add:

```python


def compute_rejections(data, trip_expected, trip_handled, overlap_candidates):
    """Pure decision logic for save-time validation.

    Args:
        data: the row dict about to be written.
        trip_expected: total qty basis for the barge/MBC; <= 0 means 'no basis,
            skip the quantity check'.
        trip_handled: qty already handled for that barge/MBC EXCLUDING this row.
        overlap_candidates: list of (from_time, to_time) tuples for other rows on
            the same equipment + entry_date (already excludes this row).

    Returns (clean_data, rejections):
        clean_data: a copy of `data` with rejected fields set to None.
        rejections: list of dicts describing each rejected field.
    """
    clean = dict(data)
    rejections = []

    # ── Quantity vs remaining trip quantity ──────────────────────────────────
    qty = clean.get('quantity')
    if qty is not None and str(qty).strip() != '' and trip_expected and float(trip_expected) > 0:
        try:
            qv = float(qty)
        except (TypeError, ValueError):
            qv = None
        if qv is not None:
            remaining = float(trip_expected) - float(trip_handled or 0)
            if qv > remaining + 1e-9:
                clean['quantity'] = None
                rejections.append({
                    'field': 'quantity',
                    'reason': 'exceeds_trip_qty',
                    'label': clean.get('barge_name') or clean.get('source_display') or '',
                    'attempted': round(qv, 3),
                    'remaining': round(remaining, 3),
                })

    # ── Time overlap vs same equipment + same date ───────────────────────────
    ft = clean.get('from_time'); tt = clean.get('to_time')
    if _hhmm_to_minutes(ft) is not None and _hhmm_to_minutes(tt) is not None:
        for cf, ct in overlap_candidates:
            if _intervals_overlap(ft, tt, cf, ct):
                clean['from_time'] = None
                clean['to_time'] = None
                rejections.append({
                    'field': 'time',
                    'reason': 'overlap',
                    'conflict': {'from_time': cf, 'to_time': ct},
                })
                break

    return clean, rejections
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest test_lueu01_validation.py -v`
Expected: PASS (all Task 1 + Task 2 tests)

- [ ] **Step 5: Commit**

```bash
git add modules/LUEU01/model.py test_lueu01_validation.py
git commit -m "feat(LUEU01): pure rejection-decision orchestrator"
```

---

### Task 3: DB helpers to resolve trip quantity and overlap candidates

**Files:**
- Modify: `modules/LUEU01/model.py` (add `_resolve_trip_quantity` and `_overlap_candidates` after `compute_rejections`)

No automated test — these are thin SQL wrappers verified manually in Task 4. They reuse the exact aggregation logic already proven in `get_vcn_barges` (barge `discharge_quantity` by `barge_name`/`trip_number`) and `get_mbc_options` (MBC bl/customer-details qty).

- [ ] **Step 1: Add the DB helpers**

In `modules/LUEU01/model.py`, immediately after the `compute_rejections` function, add:

```python


def _resolve_trip_quantity(cur, data, exclude_id):
    """Return (expected, handled_excluding_self) for the barge/MBC this row targets.

    expected == 0.0 means there is no basis to check (skip the quantity block).
    `exclude_id` is the row's own id (None for new rows); it is excluded from the
    handled sum so re-saving an existing row never double-counts itself.
    """
    source_type = data.get('source_type')
    source_id = data.get('source_id')
    barge_name = data.get('barge_name')
    if not source_type or not source_id:
        return 0.0, 0.0

    if source_type == 'VCN':
        if not barge_name:
            return 0.0, 0.0
        cur.execute('SELECT id FROM ldud_header WHERE vcn_id = %s', [source_id])
        ldud = cur.fetchone()
        if not ldud:
            return 0.0, 0.0
        cur.execute('''
            SELECT barge_name, trip_number,
                   COALESCE(SUM(discharge_quantity), 0) AS expected_qty
            FROM ldud_barge_lines
            WHERE ldud_id = %s AND barge_name IS NOT NULL AND barge_name != ''
            GROUP BY barge_name, trip_number
        ''', [ldud['id']])
        expected = 0.0
        for r in cur.fetchall():
            trip = r['trip_number'] or ''
            display = f"{r['barge_name']} / {trip}" if trip else r['barge_name']
            if display == barge_name:
                expected = float(r['expected_qty'] or 0)
                break
        cur.execute('''
            SELECT COALESCE(SUM(quantity), 0) AS handled
            FROM lueu_lines
            WHERE source_type = 'VCN' AND source_id = %s AND barge_name = %s
              AND (is_deleted IS NOT TRUE) AND id != %s
        ''', [source_id, barge_name, exclude_id or 0])
        handled = float(cur.fetchone()['handled'] or 0)
        return expected, handled

    if source_type == 'MBC':
        cur.execute('''
            SELECT CASE WHEN COUNT(cd.id) > 0 THEN COALESCE(SUM(cd.quantity), 0)
                        ELSE COALESCE(m.bl_quantity, 0) END AS bl_qty
            FROM mbc_header m
            LEFT JOIN mbc_customer_details cd ON cd.mbc_id = m.id
            WHERE m.id = %s
            GROUP BY m.id, m.bl_quantity
        ''', [source_id])
        row = cur.fetchone()
        expected = float(row['bl_qty'] or 0) if row else 0.0
        cur.execute('''
            SELECT COALESCE(SUM(quantity), 0) AS handled
            FROM lueu_lines
            WHERE source_type = 'MBC' AND source_id = %s
              AND (is_deleted IS NOT TRUE) AND id != %s
        ''', [source_id, exclude_id or 0])
        handled = float(cur.fetchone()['handled'] or 0)
        return expected, handled

    return 0.0, 0.0


def _overlap_candidates(cur, data, exclude_id):
    """Return [(from_time, to_time), ...] for other rows on the same
    equipment_name + entry_date that have both times set (excludes this row)."""
    equipment_name = data.get('equipment_name')
    entry_date = data.get('entry_date')
    if not equipment_name or not entry_date:
        return []
    cur.execute('''
        SELECT from_time, to_time FROM lueu_lines
        WHERE equipment_name = %s AND entry_date = %s
          AND (is_deleted IS NOT TRUE) AND id != %s
          AND from_time IS NOT NULL AND from_time != ''
          AND to_time IS NOT NULL AND to_time != ''
    ''', [equipment_name, entry_date, exclude_id or 0])
    return [(r['from_time'], r['to_time']) for r in cur.fetchall()]
```

- [ ] **Step 2: Sanity-check import (no syntax errors)**

Run: `python -c "from modules.LUEU01 import model; print('ok')"`
Expected: prints `ok`

- [ ] **Step 3: Commit**

```bash
git add modules/LUEU01/model.py
git commit -m "feat(LUEU01): DB helpers for trip-quantity and overlap candidates"
```

---

### Task 4: Wire validation into save_line and the save view

**Files:**
- Modify: `modules/LUEU01/model.py:113` area (inside `save_line`)
- Modify: `modules/LUEU01/model.py:157` (`return line_id`)
- Modify: `modules/LUEU01/views.py:44-54` (`save_data`)

- [ ] **Step 1: Insert validation in save_line**

In `modules/LUEU01/model.py`, find this block inside `save_line` (currently around line 110-115):

```python
    data['quantity']  = _num('quantity')
    data['source_id'] = _num('source_id')

    line_id = data.get('id')

    if line_id:
```

Replace it with:

```python
    data['quantity']  = _num('quantity')
    data['source_id'] = _num('source_id')

    line_id = data.get('id')

    # ── Validation: blank (but reject) over-limit quantity and overlapping times ──
    trip_expected, trip_handled = _resolve_trip_quantity(cur, data, line_id)
    overlap = _overlap_candidates(cur, data, line_id)
    data, rejections = compute_rejections(data, trip_expected, trip_handled, overlap)

    if line_id:
```

- [ ] **Step 2: Change save_line's return value**

In `modules/LUEU01/model.py`, find the end of `save_line` (currently around line 155-157):

```python
    conn.commit()
    conn.close()
    return line_id
```

Replace with:

```python
    conn.commit()
    conn.close()
    return {'id': line_id, 'rejections': rejections}
```

- [ ] **Step 3: Update the save_data view to pass the dict through**

In `modules/LUEU01/views.py`, replace the body of `save_data` (lines 44-54):

```python
@bp.route('/api/module/LUEU01/save', methods=['POST'])
@login_required
def save_data():
    perms = get_perms()
    if not perms.get('can_add') and not perms.get('can_edit'):
        return jsonify({'error': 'No permission'}), 403

    data = request.json
    data['created_by'] = session.get('username')
    line_id = model.save_line(data)
    return jsonify({'id': line_id})
```

with:

```python
@bp.route('/api/module/LUEU01/save', methods=['POST'])
@login_required
def save_data():
    perms = get_perms()
    if not perms.get('can_add') and not perms.get('can_edit'):
        return jsonify({'error': 'No permission'}), 403

    data = request.json
    data['created_by'] = session.get('username')
    result = model.save_line(data)   # {'id': ..., 'rejections': [...]}
    return jsonify(result)
```

- [ ] **Step 4: Verify the existing unit tests still pass**

Run: `python -m pytest test_lueu01_validation.py -v`
Expected: PASS (unchanged — pure functions untouched)

- [ ] **Step 5: Manual verification against the running app**

Start the app the usual way for this project, log in, open LUEU01, pick an equipment, and confirm:

1. **Quantity block (VCN):** add a row for a VCN + barge, enter a quantity larger than that barge trip's remaining discharge qty, Save. Expected: row saves, the Quantity cell becomes blank, server response (Network tab) shows `rejections:[{field:'quantity',...}]`.
2. **Quantity OK:** enter a quantity within remaining. Expected: saves with quantity intact, `rejections:[]`.
3. **Edit down:** open an existing saved row and lower its quantity. Expected: not falsely blocked (self excluded from handled).
4. **Time overlap:** add a second row for the same equipment + same date whose From/To intersects an existing row. Save. Expected: From/To blank out, Diff (Hrs) clears, response shows `rejections:[{field:'time', conflict:{...}}]`.
5. **No overlap:** non-intersecting From/To saves normally.

(Client popup is added in Task 5; here verify only the data/response behavior.)

- [ ] **Step 6: Commit**

```bash
git add modules/LUEU01/model.py modules/LUEU01/views.py
git commit -m "feat(LUEU01): enforce qty/time validation in save_line + view"
```

---

### Task 5: Client reaction — blank rejected cells and show one popup

**Files:**
- Modify: `modules/LUEU01/lueu01.html` (add a popup modal after the delete modal ~line 282; add `showRejectionPopup`; update `saveAll` lines 1039-1074)

- [ ] **Step 1: Add the rejection popup modal markup**

In `modules/LUEU01/lueu01.html`, immediately after the closing `</div>` of the Delete Password Modal (the block ending at line 282, just before `<!-- BL Progress Popup -->`), add:

```html
<!-- Rejection Popup Modal -->
<div id="rejectModal" style="display:none;position:fixed;inset:0;z-index:9200;background:rgba(0,0,0,0.45);align-items:center;justify-content:center;">
    <div style="background:#fff;border-radius:8px;box-shadow:0 8px 30px rgba(0,0,0,0.25);width:460px;max-width:92vw;overflow:hidden;">
        <div style="background:#b45309;color:#fff;padding:12px 16px;font-weight:600;font-size:13px;display:flex;align-items:center;gap:8px;">
            <span>&#9888;</span> Some values were not saved
        </div>
        <div style="padding:16px;">
            <p style="font-size:12px;color:#4a5568;margin-bottom:10px;">The rest of each row was saved. Please correct the highlighted fields:</p>
            <ul id="rejectList" style="margin:0;padding-left:18px;font-size:12px;color:#2d3748;display:flex;flex-direction:column;gap:6px;"></ul>
        </div>
        <div style="padding:0 16px 14px;display:flex;justify-content:flex-end;">
            <button onclick="document.getElementById('rejectModal').style.display='none'" style="padding:5px 14px;background:#b45309;color:#fff;border:none;border-radius:4px;font-size:12px;cursor:pointer;">OK</button>
        </div>
    </div>
</div>
```

- [ ] **Step 2: Add the showRejectionPopup function**

In `modules/LUEU01/lueu01.html`, inside the `{% block scripts %}` section, immediately before the `async function saveAll() {` definition (line 1039), add:

```javascript
    function showRejectionPopup(messages) {
        const list = document.getElementById('rejectList');
        list.innerHTML = messages.map(function(m) {
            return '<li>' + m + '</li>';
        }).join('');
        document.getElementById('rejectModal').style.display = 'flex';
    }

```

- [ ] **Step 3: Update saveAll to handle rejections**

In `modules/LUEU01/lueu01.html`, replace the entire `saveAll` function (lines 1039-1074):

```javascript
    async function saveAll() {
        cancelAutoSave();
        showStatus('Saving...', 'saving');
        const rows = table.getRows();
        let savedCount = 0;

        for (const row of rows) {
            const data = row.getData();
            if (!data.id && !data.entry_date) continue;
            // Skip unchanged existing rows
            if (data.id && !dirtyRowIds.has(String(data.id))) continue;

            // Set equipment name
            data.equipment_name = currentEquipment;

            // Determine source type and id
            const vcnMatch = vcnOptions.find(v => v.label === data.source_display);
            const mbcMatch = mbcOptions.find(m => m.label === data.source_display);
            if (vcnMatch) { data.source_type = 'VCN'; data.source_id = vcnMatch.id; }
            else if (mbcMatch) { data.source_type = 'MBC'; data.source_id = mbcMatch.id; }

            // Strip local-only flag before sending to server
            const payload = Object.assign({}, data);
            delete payload._is_new;

            const res = await fetch("/api/module/LUEU01/save", {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify(payload)
            });
            const result = await res.json();
            if (result.id) { row.update({id: result.id, _is_new: false}); savedCount++; }
        }
        dirtyRowIds.clear();
        showStatus(`Saved ${savedCount} row(s)`, 'saved');
    }
```

with:

```javascript
    async function saveAll() {
        cancelAutoSave();
        showStatus('Saving...', 'saving');
        const rows = table.getRows();
        let savedCount = 0;
        let rejectionMessages = [];

        for (const row of rows) {
            const data = row.getData();
            if (!data.id && !data.entry_date) continue;
            // Skip unchanged existing rows
            if (data.id && !dirtyRowIds.has(String(data.id))) continue;

            // Set equipment name
            data.equipment_name = currentEquipment;

            // Determine source type and id
            const vcnMatch = vcnOptions.find(v => v.label === data.source_display);
            const mbcMatch = mbcOptions.find(m => m.label === data.source_display);
            if (vcnMatch) { data.source_type = 'VCN'; data.source_id = vcnMatch.id; }
            else if (mbcMatch) { data.source_type = 'MBC'; data.source_id = mbcMatch.id; }

            // Strip local-only flag before sending to server
            const payload = Object.assign({}, data);
            delete payload._is_new;

            const res = await fetch("/api/module/LUEU01/save", {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify(payload)
            });
            const result = await res.json();
            if (result.id) {
                const upd = {id: result.id, _is_new: false};
                const label = data.barge_name || data.source_display || '—';
                (result.rejections || []).forEach(function(rej) {
                    if (rej.field === 'quantity') {
                        upd.quantity = null;
                        rejectionMessages.push(
                            `${currentEquipment} / ${label}: quantity ${rej.attempted} exceeds remaining ${rej.remaining} — quantity not saved.`);
                    } else if (rej.field === 'time') {
                        upd.from_time = null;
                        upd.to_time = null;
                        upd.diff_hrs = '';
                        const c = rej.conflict || {};
                        rejectionMessages.push(
                            `${currentEquipment} / ${label}: time overlaps existing ${c.from_time || '?'}–${c.to_time || '?'} — From/To not saved.`);
                    }
                });
                row.update(upd);
                savedCount++;
            }
        }
        dirtyRowIds.clear();
        showStatus(`Saved ${savedCount} row(s)`, 'saved');
        if (rejectionMessages.length) showRejectionPopup(rejectionMessages);
    }
```

- [ ] **Step 4: Manual verification in the browser**

Reload LUEU01 (hard refresh to bust the template cache) and repeat the Task 4 scenarios. Now confirm:

1. Over-limit quantity → row saves, Quantity cell blanks, and the amber "Some values were not saved" popup lists the quantity message.
2. Time overlap → From/To and Diff (Hrs) blank, popup lists the overlap message naming the conflicting times.
3. Two rejected rows in one save → popup lists both as separate bullets.
4. A clean save → no popup appears.

- [ ] **Step 5: Commit**

```bash
git add modules/LUEU01/lueu01.html
git commit -m "feat(LUEU01): blank rejected cells and show rejection popup on save"
```

---

## Self-Review

**Spec coverage:**
- Quantity block on per-barge/MBC trip qty → Task 2 (`compute_rejections`) + Task 3 (`_resolve_trip_quantity`) + Task 4 wiring. ✓
- Reject quantity but save rest (blank field) → `compute_rejections` sets `quantity=None`, insert/update uses cleaned data. ✓
- Time overlap, same equipment+date, overnight aware → Task 1 (`_intervals_overlap`) + Task 3 (`_overlap_candidates`). ✓
- Reject From/To (both) but save rest → `compute_rejections` nulls both; client clears `diff_hrs`. ✓
- Exclude self when editing → `exclude_id or 0` in both DB helpers; test `test_quantity_exactly_remaining_is_kept` + manual "edit down". ✓
- Server-authoritative response contract `{'id','rejections'}` → Task 4. ✓
- Client blanks cells + single summary popup → Task 5. ✓
- Split exempt → `split_line` is untouched (no task modifies it). ✓
- Soft-deleted excluded → `is_deleted IS NOT TRUE` in both DB helpers. ✓

**Placeholder scan:** No TBD/TODO/"add validation"; every code step shows full code. ✓

**Type consistency:** `compute_rejections(data, trip_expected, trip_handled, overlap_candidates)` signature is identical across Task 2 definition, Task 2 tests, and Task 4 call site. `_resolve_trip_quantity(cur, data, exclude_id)` and `_overlap_candidates(cur, data, exclude_id)` match between Task 3 and Task 4. Rejection dict keys (`field`, `attempted`, `remaining`, `conflict.from_time`, `conflict.to_time`) match between `compute_rejections`, the tests, and the client in Task 5. `save_line` now returns a dict everywhere it's consumed (only `views.save_data`). ✓
