# RP01 Historical Data Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an admin-only "Historical Data" feature in RP01 that loads backdated LUEU data from an uploaded CSV/Excel into a dedicated table (full-replace), with master reconciliation, and exposes it as a separate "LUEU (incl. historical)" pivot data source for the report designer and dashboard widgets.

**Architecture:** A new `rp01_historical_lueu` table mirrors the base columns of `lueu_lines`. A new RP01 sub-package `historical_data` provides a page + endpoints (template export, two-phase preview/apply). A new pivot source `lueu-historical` UNIONs live `lueu_lines` with the historical table, sharing the existing cargo/delay enrichment. Existing `lueu-equipment` source is untouched.

**Tech Stack:** Python 3.14, Flask, psycopg2 (RealDictCursor), Alembic, openpyxl, pytest. Spec: `docs/superpowers/specs/2026-06-03-rp01-historical-data-design.md`.

---

## File Structure

- Create: `alembic/versions/<rev>_rp01_historical_lueu.py` — table migration.
- Create: `modules/RP01/RP01/historical_data/__init__.py` — empty package marker.
- Create: `modules/RP01/RP01/historical_data/model.py` — pure helpers (column spec, validators, fuzzy suggest, parser) + DB functions (masters, reconcile, full-replace, template build, status).
- Create: `modules/RP01/RP01/historical_data/views.py` — routes (page, template, preview, apply), admin-gated, registered on `bp`.
- Create: `modules/RP01/RP01/historical_data/historical_data.html` — upload UI + reconciliation preview.
- Modify: `modules/RP01/RP01/views.py` — import the new sub-package views.
- Modify: `modules/RP01/RP01/rp01.html` — admin-only card.
- Modify: `modules/RP01/RP01/custom_report/views.py` — add `lueu-historical` source.
- Modify: `modules/RP01/RP01/custom_report/custom_report.html` — source option + date-col config + label map.
- Modify: `modules/RP01/RP01/dashboard/dashboard.html` — source option + date-col config + label map.
- Create: `test_rp01_historical.py` (repo root) — pure-function unit tests.

**Column spec (single source of truth, defined in Task 2):** the ordered list of base fields is
`entry_date, shift, equipment_name, from_time, to_time, source_display, barge_name, cargo_name, delay_name, system_name, route_name, berth_name, shift_incharge, operator_name, quantity, quantity_uom, remarks`.

---

## Task 1: Alembic migration — `rp01_historical_lueu` table

**Files:**
- Create: `alembic/versions/f1a2b3c4d5e6_rp01_historical_lueu.py`

- [ ] **Step 1: Confirm the current head**

Run: `python -m alembic heads`
Expected: `e7f8a9b0c1d2 (head)`. Use that value as `down_revision` below. If it differs, use whatever this command prints.

- [ ] **Step 2: Write the migration file**

Create `alembic/versions/f1a2b3c4d5e6_rp01_historical_lueu.py`:

```python
"""RP01: rp01_historical_lueu table for historical LUEU reference data

Revision ID: f1a2b3c4d5e6
Revises: e7f8a9b0c1d2
Create Date: 2026-06-03
"""
from typing import Sequence, Union
from alembic import op

revision: str = 'f1a2b3c4d5e6'
down_revision: Union[str, None] = 'e7f8a9b0c1d2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS rp01_historical_lueu (
            id              SERIAL PRIMARY KEY,
            entry_date      DATE NOT NULL,
            shift           VARCHAR(20),
            equipment_name  VARCHAR(200) NOT NULL,
            from_time       VARCHAR(5),
            to_time         VARCHAR(5),
            source_display  VARCHAR(300),
            barge_name      VARCHAR(300),
            cargo_name      VARCHAR(200),
            delay_name      VARCHAR(200),
            system_name     VARCHAR(200),
            route_name      VARCHAR(200),
            berth_name      VARCHAR(200),
            shift_incharge  VARCHAR(200),
            operator_name   VARCHAR(200),
            quantity        NUMERIC,
            quantity_uom    VARCHAR(50),
            remarks         TEXT,
            uploaded_by     INTEGER,
            uploaded_at     TIMESTAMP DEFAULT NOW()
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_rp01_hist_entry_date ON rp01_historical_lueu (entry_date)")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS rp01_historical_lueu")
```

- [ ] **Step 3: Apply the migration**

Run: `python -m alembic upgrade head`
Expected: log line `Running upgrade e7f8a9b0c1d2 -> f1a2b3c4d5e6`.

- [ ] **Step 4: Verify the table exists**

Run:
```bash
python -c "from database import get_db,get_cursor; c=get_db(); cur=get_cursor(c); cur.execute(\"SELECT column_name FROM information_schema.columns WHERE table_name='rp01_historical_lueu' ORDER BY ordinal_position\"); print([r['column_name'] for r in cur.fetchall()]); c.close()"
```
Expected: prints the full column list ending with `uploaded_by`, `uploaded_at`.

- [ ] **Step 5: Commit**

```bash
git add alembic/versions/f1a2b3c4d5e6_rp01_historical_lueu.py
git commit -m "feat(RP01): add rp01_historical_lueu table"
```

---

## Task 2: Pure helpers in `model.py` (column spec, validators, fuzzy, parser)

**Files:**
- Create: `modules/RP01/RP01/historical_data/__init__.py`
- Create: `modules/RP01/RP01/historical_data/model.py`
- Test: `test_rp01_historical.py`

- [ ] **Step 1: Create the package marker**

Create `modules/RP01/RP01/historical_data/__init__.py` (empty file):

```python
```

- [ ] **Step 2: Write the failing tests**

Create `test_rp01_historical.py`:

```python
"""Pure-function unit tests for RP01 historical data parsing/validation.
No DB access (mirrors test_lueu01_validation.py)."""
from modules.RP01.RP01.historical_data import model


# ── parse_date ──────────────────────────────────────────────────────────────
def test_parse_date_iso():
    assert model.parse_date('2025-04-15') == '2025-04-15'

def test_parse_date_datetime_text():
    assert model.parse_date('2025-04-15 00:00:00') == '2025-04-15'

def test_parse_date_blank_is_none():
    assert model.parse_date('') is None
    assert model.parse_date(None) is None

def test_parse_date_bad_raises():
    import pytest
    with pytest.raises(ValueError):
        model.parse_date('15/04/2025')


# ── parse_hhmm ──────────────────────────────────────────────────────────────
def test_parse_hhmm_ok():
    assert model.parse_hhmm('06:30') == '06:30'
    assert model.parse_hhmm('06:30:00') == '06:30'

def test_parse_hhmm_blank_is_none():
    assert model.parse_hhmm('') is None
    assert model.parse_hhmm(None) is None

def test_parse_hhmm_bad_raises():
    import pytest
    with pytest.raises(ValueError):
        model.parse_hhmm('25:99')


# ── parse_number ────────────────────────────────────────────────────────────
def test_parse_number_ok():
    assert model.parse_number('700') == 700.0
    assert model.parse_number(700) == 700.0
    assert model.parse_number('4.5') == 4.5

def test_parse_number_blank_is_none():
    assert model.parse_number('') is None
    assert model.parse_number(None) is None

def test_parse_number_bad_raises():
    import pytest
    with pytest.raises(ValueError):
        model.parse_number('abc')


# ── suggest_matches ───────────────────────────────────────────────────────────
def test_suggest_matches_finds_close():
    masters = ['BARGE UNLOADER 1', 'BARGE UNLOADER 2', 'BU 1 & BU 2']
    out = model.suggest_matches('BARGE UNLOADER1', masters)
    assert 'BARGE UNLOADER 1' in out

def test_suggest_matches_case_insensitive():
    out = model.suggest_matches('limestone', ['Limestone', 'Dolomite'])
    assert 'Limestone' in out

def test_suggest_matches_empty_when_nothing_close():
    out = model.suggest_matches('zzzzzz', ['Limestone', 'Dolomite'])
    assert out == []


# ── parse_rows ────────────────────────────────────────────────────────────────
def test_parse_rows_maps_headers_and_skips_blank():
    headers = ['entry_date', 'equipment_name', 'quantity']
    raw = [
        ['2025-04-01', 'BU 1', '700'],
        ['', '', ''],                       # fully blank → skipped
        ['2025-04-02', 'BU 2', ''],
    ]
    rows, errors = model.parse_rows(headers, raw)
    assert errors == []
    assert len(rows) == 2
    assert rows[0]['entry_date'] == '2025-04-01'
    assert rows[0]['equipment_name'] == 'BU 1'
    assert rows[0]['quantity'] == 700.0
    assert rows[1]['quantity'] is None

def test_parse_rows_collects_format_errors():
    headers = ['entry_date', 'equipment_name', 'quantity']
    raw = [['bad-date', 'BU 1', 'oops']]
    rows, errors = model.parse_rows(headers, raw)
    assert rows == []
    assert any('entry_date' in e['message'] for e in errors)

def test_parse_rows_requires_equipment_and_date():
    headers = ['entry_date', 'equipment_name', 'quantity']
    raw = [['2025-04-01', '', '5']]
    rows, errors = model.parse_rows(headers, raw)
    assert rows == []
    assert any('equipment_name' in e['message'] for e in errors)
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `python -m pytest test_rp01_historical.py -q`
Expected: FAIL — `ModuleNotFoundError`/`AttributeError` (model functions not defined yet).

- [ ] **Step 4: Implement the pure helpers**

Create `modules/RP01/RP01/historical_data/model.py`:

```python
"""RP01 Historical Data — parsing, validation, reconciliation, full-replace,
template export. Pure helpers have no DB dependency; DB functions open their
own connection (mirrors the rest of the codebase)."""
import difflib
from datetime import date, datetime

from database import get_db, get_cursor

# Ordered base columns (one source of truth). Header labels in the template
# match these exact keys so upload mapping is a direct dict lookup.
COLUMNS = [
    'entry_date', 'shift', 'equipment_name', 'from_time', 'to_time',
    'source_display', 'barge_name', 'cargo_name', 'delay_name', 'system_name',
    'route_name', 'berth_name', 'shift_incharge', 'operator_name',
    'quantity', 'quantity_uom', 'remarks',
]

# Master-backed columns → (master table, master column). barge_name is special
# (matches barge OR mbc master) and handled separately in reconcile().
MASTER_MAP = {
    'equipment_name': ('equipment', 'name'),
    'cargo_name':     ('vessel_cargo', 'cargo_name'),
    'delay_name':     ('port_delay_types', 'name'),
    'route_name':     ('conveyor_routes', 'route_name'),
    'system_name':    ('port_systems', 'name'),
    'berth_name':     ('port_berth_master', 'berth_name'),
    'operator_name':  ('port_shift_operators', 'name'),
    'shift_incharge': ('port_shift_incharge', 'name'),
    'source_display': ('vessels', 'vessel_name'),
}


# ── Pure validators ──────────────────────────────────────────────────────────
def _blank(v):
    return v is None or (isinstance(v, str) and v.strip() == '')


def parse_date(v):
    """'YYYY-MM-DD' (optionally with time) → 'YYYY-MM-DD'. Blank→None. Else ValueError."""
    if _blank(v):
        return None
    if isinstance(v, (datetime, date)):
        return v.strftime('%Y-%m-%d')
    s = str(v).strip()
    for fmt in ('%Y-%m-%d %H:%M:%S', '%Y-%m-%d %H:%M', '%Y-%m-%d'):
        try:
            return datetime.strptime(s, fmt).strftime('%Y-%m-%d')
        except ValueError:
            pass
    raise ValueError(f"invalid date '{s}' (expected YYYY-MM-DD)")


def parse_hhmm(v):
    """'HH:MM' or 'HH:MM:SS' → 'HH:MM'. Blank→None. Else ValueError."""
    if _blank(v):
        return None
    if isinstance(v, (datetime,)):
        return v.strftime('%H:%M')
    s = str(v).strip()
    parts = s.split(':')
    if len(parts) >= 2:
        try:
            h, m = int(parts[0]), int(parts[1])
            if 0 <= h <= 23 and 0 <= m <= 59:
                return f"{h:02d}:{m:02d}"
        except ValueError:
            pass
    raise ValueError(f"invalid time '{s}' (expected HH:MM)")


def parse_number(v):
    """Numeric → float. Blank→None. Else ValueError."""
    if _blank(v):
        return None
    try:
        return float(str(v).strip().replace(',', ''))
    except (TypeError, ValueError):
        raise ValueError(f"invalid number '{v}'")


def suggest_matches(value, master_values, n=3, cutoff=0.6):
    """Closest master values to `value` (case-insensitive), up to n."""
    if not value or not master_values:
        return []
    lower_map = {}
    for m in master_values:
        lower_map.setdefault(str(m).lower(), m)
    hits = difflib.get_close_matches(str(value).lower(), list(lower_map.keys()), n=n, cutoff=cutoff)
    return [lower_map[h] for h in hits]


def parse_rows(headers, raw_rows):
    """Map raw spreadsheet rows to field dicts using `headers`.

    Returns (rows, errors). A row is skipped if every cell is blank.
    Required: entry_date (valid), equipment_name (non-empty). Format errors on
    entry_date/from_time/to_time/quantity are collected per row (1-based, +1 for
    the header row → matches the spreadsheet row number).
    """
    idx = {h: i for i, h in enumerate(headers) if h in COLUMNS}
    rows, errors = [], []

    def cell(r, key):
        i = idx.get(key)
        return r[i] if (i is not None and i < len(r)) else None

    for n, r in enumerate(raw_rows, start=2):  # +1 header, +1 to 1-base
        if all(_blank(c) for c in r):
            continue
        rec, row_errs = {}, []
        # text fields straight through (trimmed)
        for key in COLUMNS:
            if key in ('entry_date', 'from_time', 'to_time', 'quantity'):
                continue
            v = cell(r, key)
            rec[key] = (str(v).strip() if not _blank(v) else None)
        # typed fields
        try:
            rec['entry_date'] = parse_date(cell(r, 'entry_date'))
        except ValueError as e:
            row_errs.append(str(e))
        try:
            rec['from_time'] = parse_hhmm(cell(r, 'from_time'))
        except ValueError as e:
            row_errs.append(str(e))
        try:
            rec['to_time'] = parse_hhmm(cell(r, 'to_time'))
        except ValueError as e:
            row_errs.append(str(e))
        try:
            rec['quantity'] = parse_number(cell(r, 'quantity'))
        except ValueError as e:
            row_errs.append(str(e))
        # required
        if not rec.get('entry_date'):
            row_errs.append('entry_date is required')
        if _blank(rec.get('equipment_name')):
            row_errs.append('equipment_name is required')

        if row_errs:
            errors.append({'row': n, 'message': '; '.join(row_errs)})
        else:
            rows.append(rec)
    return rows, errors
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `python -m pytest test_rp01_historical.py -q`
Expected: PASS (all tests green).

- [ ] **Step 6: Commit**

```bash
git add modules/RP01/RP01/historical_data/__init__.py modules/RP01/RP01/historical_data/model.py test_rp01_historical.py
git commit -m "feat(RP01): historical data pure parsers + validators with tests"
```

---

## Task 3: DB functions in `model.py` (masters, reconcile, replace, template, status)

**Files:**
- Modify: `modules/RP01/RP01/historical_data/model.py` (append functions)

- [ ] **Step 1: Append the DB + template functions**

Append to `modules/RP01/RP01/historical_data/model.py`:

```python
# ── Master lookups ───────────────────────────────────────────────────────────
def _fetch_master(cur, table, col, extra_where=''):
    cur.execute(f"SELECT {col} AS v FROM {table} {extra_where} ORDER BY {col}")
    return [r['v'] for r in cur.fetchall() if r['v'] not in (None, '')]


def get_all_masters():
    """Return {logical_name: [values]} for every list shown in the template
    Masters sheet and used for reconciliation."""
    conn = get_db()
    cur = get_cursor(conn)
    try:
        masters = {
            'equipment_name': _fetch_master(cur, 'equipment', 'name'),
            'cargo_name':     _fetch_master(cur, 'vessel_cargo', 'cargo_name'),
            'delay_name':     _fetch_master(cur, 'port_delay_types', 'name'),
            'route_name':     _fetch_master(cur, 'conveyor_routes', 'route_name', 'WHERE is_active = 1'),
            'system_name':    _fetch_master(cur, 'port_systems', 'name'),
            'berth_name':     _fetch_master(cur, 'port_berth_master', 'berth_name'),
            'operator_name':  _fetch_master(cur, 'port_shift_operators', 'name'),
            'shift_incharge': _fetch_master(cur, 'port_shift_incharge', 'name'),
            'barge_name':     _fetch_master(cur, 'barges', 'barge_name'),
            'mbc_name':       _fetch_master(cur, 'mbc_master', 'mbc_name'),
            'source_display': _fetch_master(cur, 'vessels', 'vessel_name'),
        }
    finally:
        conn.close()
    return masters


def reconcile(rows, masters):
    """For each master-backed column, split the rows' distinct values into
    recognized vs unknown (with fuzzy suggestions). barge_name matches against
    barge OR mbc master. Returns {column: {recognized:[...], unknown:[{value,count,suggestions}]}}."""
    out = {}
    cols = list(MASTER_MAP.keys()) + ['barge_name']
    for col in cols:
        # distinct values + counts
        counts = {}
        for r in rows:
            v = r.get(col)
            if v:
                counts[v] = counts.get(v, 0) + 1
        if col == 'barge_name':
            valid = {str(x).lower() for x in masters.get('barge_name', [])}
            valid |= {str(x).lower() for x in masters.get('mbc_name', [])}
            suggest_pool = list(masters.get('barge_name', [])) + list(masters.get('mbc_name', []))
        else:
            mvals = masters.get(col, [])
            valid = {str(x).lower() for x in mvals}
            suggest_pool = mvals
        recognized, unknown = [], []
        for value, count in sorted(counts.items()):
            if str(value).lower() in valid:
                recognized.append(value)
            else:
                unknown.append({'value': value, 'count': count,
                                'suggestions': suggest_matches(value, suggest_pool)})
        out[col] = {'recognized': recognized, 'unknown': unknown}
    return out


# ── Full replace ─────────────────────────────────────────────────────────────
def replace_all(rows, uploaded_by):
    """TRUNCATE then bulk-insert all rows, in one transaction. Returns inserted count."""
    conn = get_db()
    cur = get_cursor(conn)
    try:
        cur.execute("TRUNCATE rp01_historical_lueu RESTART IDENTITY")
        insert_cols = COLUMNS + ['uploaded_by']
        placeholders = ', '.join(['%s'] * len(insert_cols))
        sql = (f"INSERT INTO rp01_historical_lueu ({', '.join(insert_cols)}) "
               f"VALUES ({placeholders})")
        for r in rows:
            cur.execute(sql, [r.get(c) for c in COLUMNS] + [uploaded_by])
        conn.commit()
        return len(rows)
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def get_status():
    """Return {count, uploaded_by, uploaded_at} for the current dataset."""
    conn = get_db()
    cur = get_cursor(conn)
    try:
        cur.execute("SELECT COUNT(*) AS c, MAX(uploaded_at) AS at, MAX(uploaded_by) AS by_id FROM rp01_historical_lueu")
        r = cur.fetchone()
        at = r['at']
        return {'count': r['c'], 'uploaded_by': r['by_id'],
                'uploaded_at': at.isoformat() if at else None}
    finally:
        conn.close()


# ── Template / upload parsing ────────────────────────────────────────────────
def build_template_workbook():
    """Return an openpyxl Workbook: 'Data' sheet (headers + instructions +
    dropdowns) and 'Masters' sheet (live master values)."""
    import openpyxl
    from openpyxl.worksheet.datavalidation import DataValidation
    from openpyxl.utils import get_column_letter

    masters = get_all_masters()
    wb = openpyxl.Workbook()

    # Masters sheet first (so we can reference its ranges)
    ms = wb.create_sheet('Masters')
    # column order on Masters sheet → maps to a Data column for dropdowns
    master_order = ['equipment_name', 'cargo_name', 'delay_name', 'route_name',
                    'system_name', 'berth_name', 'operator_name', 'shift_incharge',
                    'barge_name', 'mbc_name', 'source_display']
    master_col_letter = {}
    for ci, key in enumerate(master_order, start=1):
        col = get_column_letter(ci)
        master_col_letter[key] = col
        ms.cell(1, ci, key)
        for ri, val in enumerate(masters.get(key, []), start=2):
            ms.cell(ri, ci, val)

    # Data sheet
    ds = wb.active
    ds.title = 'Data'
    note = ("Date=YYYY-MM-DD (required) | From/To=HH:MM (24h) | quantity=number | "
            "equipment_name required | source_display=vessel name (vessel ops) | "
            "barge_name=barge name (vessel ops) OR MBC name (MBC ops). Pick from dropdowns where available.")
    ds.cell(1, 1, note)
    ds.merge_cells(start_row=1, start_column=1, end_row=1, end_column=len(COLUMNS))
    for ci, key in enumerate(COLUMNS, start=1):
        ds.cell(2, ci, key)
        ds.column_dimensions[get_column_letter(ci)].width = max(14, len(key) + 2)
    ds.freeze_panes = 'A3'

    # Dropdowns: Data column key → master sheet column (skip mbc_name; barge uses barge list)
    dropdown_for = {
        'equipment_name': 'equipment_name', 'cargo_name': 'cargo_name',
        'delay_name': 'delay_name', 'route_name': 'route_name',
        'system_name': 'system_name', 'berth_name': 'berth_name',
        'operator_name': 'operator_name', 'shift_incharge': 'shift_incharge',
        'barge_name': 'barge_name', 'source_display': 'source_display',
    }
    for data_key, master_key in dropdown_for.items():
        if data_key not in COLUMNS:
            continue
        mcol = master_col_letter[master_key]
        dcol = get_column_letter(COLUMNS.index(data_key) + 1)
        dv = DataValidation(type='list',
                            formula1=f"Masters!${mcol}$2:${mcol}$2000",
                            allow_blank=True, showErrorMessage=False)
        ds.add_data_validation(dv)
        dv.add(f"{dcol}3:{dcol}10000")
    return wb


def parse_upload(file_storage):
    """Parse a werkzeug FileStorage (.xlsx/.xls/.csv) → (rows, errors).
    Reads the 'Data' sheet for workbooks (falls back to the active sheet).
    Header row is the row whose first cell equals 'entry_date'."""
    import io, os, csv as _csv
    name = (file_storage.filename or '').lower()
    raw = file_storage.read()

    def from_matrix(matrix):
        header_idx = None
        for i, row in enumerate(matrix):
            if row and str(row[0]).strip() == 'entry_date':
                header_idx = i
                break
        if header_idx is None:
            return [], [{'row': 0, 'message': "Could not find a header row starting with 'entry_date'"}]
        headers = [str(c).strip() if c is not None else '' for c in matrix[header_idx]]
        return parse_rows(headers, matrix[header_idx + 1:])

    if name.endswith('.csv'):
        text = raw.decode('utf-8-sig', errors='replace')
        matrix = list(_csv.reader(io.StringIO(text)))
        return from_matrix(matrix)
    else:
        import openpyxl
        wb = openpyxl.load_workbook(io.BytesIO(raw), read_only=True, data_only=True)
        ws = wb['Data'] if 'Data' in wb.sheetnames else wb.active
        matrix = [list(r) for r in ws.iter_rows(values_only=True)]
        return from_matrix(matrix)
```

- [ ] **Step 2: Smoke-test the template build and masters (DB-backed)**

Run:
```bash
python -c "from modules.RP01.RP01.historical_data import model; m=model.get_all_masters(); print('master keys:', list(m)); wb=model.build_template_workbook(); print('sheets:', wb.sheetnames); print('status:', model.get_status())"
```
Expected: prints master keys, `sheets: ['Masters', 'Data']` (order may vary), and a status dict with `count: 0`.

- [ ] **Step 3: Smoke-test reconcile**

Run:
```bash
python -c "
from modules.RP01.RP01.historical_data import model
masters={'equipment_name':['BARGE UNLOADER 1'],'barge_name':['Kingfisher'],'mbc_name':[]}
rows=[{'equipment_name':'BARGE UNLOADER1','barge_name':'Kingfisher'}]
import json; print(json.dumps(model.reconcile(rows, masters), indent=1))
"
```
Expected: `equipment_name.unknown` contains `BARGE UNLOADER1` with a suggestion `BARGE UNLOADER 1`; `barge_name.recognized` contains `Kingfisher`.

- [ ] **Step 4: Commit**

```bash
git add modules/RP01/RP01/historical_data/model.py
git commit -m "feat(RP01): historical data masters, reconcile, replace, template build"
```

---

## Task 4: Endpoints in `views.py` (page, template, preview, apply)

**Files:**
- Create: `modules/RP01/RP01/historical_data/views.py`
- Modify: `modules/RP01/RP01/views.py`

- [ ] **Step 1: Write the views module**

Create `modules/RP01/RP01/historical_data/views.py`:

```python
import io
from functools import wraps
from flask import render_template, request, jsonify, session, redirect, url_for, Response

from .. import bp
from . import model


def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated


def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('login'))
        if not session.get('is_admin'):
            return jsonify({'error': 'Admin only'}), 403
        return f(*args, **kwargs)
    return decorated


@bp.route('/module/RP01/historical-data/')
@login_required
def historical_data_index():
    if not session.get('is_admin'):
        return render_template('no_access.html'), 403
    return render_template('historical_data/historical_data.html',
                           username=session.get('username'),
                           status=model.get_status())


@bp.route('/api/module/RP01/historical/template')
@admin_required
def historical_template():
    wb = model.build_template_workbook()
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return Response(
        buf.getvalue(),
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        headers={'Content-Disposition': 'attachment; filename="RP01_historical_template.xlsx"'},
    )


@bp.route('/api/module/RP01/historical/preview', methods=['POST'])
@admin_required
def historical_preview():
    f = request.files.get('file')
    if not f:
        return jsonify({'error': 'No file provided'}), 400
    rows, errors = model.parse_upload(f)
    masters = model.get_all_masters()
    recon = model.reconcile(rows, masters)
    return jsonify({'total_rows': len(rows), 'format_errors': errors,
                    'reconciliation': recon})


@bp.route('/api/module/RP01/historical/apply', methods=['POST'])
@admin_required
def historical_apply():
    f = request.files.get('file')
    if not f:
        return jsonify({'error': 'No file provided'}), 400
    rows, errors = model.parse_upload(f)
    if errors:
        return jsonify({'error': 'Fix format errors before applying',
                        'format_errors': errors}), 400
    inserted = model.replace_all(rows, session.get('user_id'))
    return jsonify({'inserted': inserted})
```

- [ ] **Step 2: Register the sub-package in RP01/views.py**

In `modules/RP01/RP01/views.py`, add this import line alongside the other feature imports (after the `cargo_report` import on line 18):

```python
from .historical_data import views as _historical_data_views  # noqa: registers historical-data routes on bp
```

- [ ] **Step 3: Verify the app imports and routes register**

Run:
```bash
python -c "import app; c=[r.rule for r in app.app.url_map.iter_rules() if 'historical' in r.rule]; print(c)"
```
Expected: lists `/module/RP01/historical-data/`, `/api/module/RP01/historical/template`, `/api/module/RP01/historical/preview`, `/api/module/RP01/historical/apply`.
(If the Flask app object is not `app.app`, use the project's documented entrypoint; the import must succeed without error.)

- [ ] **Step 4: Commit**

```bash
git add modules/RP01/RP01/historical_data/views.py modules/RP01/RP01/views.py
git commit -m "feat(RP01): historical data endpoints (template/preview/apply, admin-gated)"
```

---

## Task 5: Upload UI page `historical_data.html`

**Files:**
- Create: `modules/RP01/RP01/historical_data/historical_data.html`

- [ ] **Step 1: Write the page**

Create `modules/RP01/RP01/historical_data/historical_data.html`:

```html
{% extends "base.html" %}
{% block title %}Historical Data - RP01{% endblock %}
{% block content %}
<div class="module-header">
    <h2>Historical Data (LUEU)</h2>
    <span class="module-code">RP01</span>
</div>

<div style="padding:14px;max-width:1100px;">
    <div style="background:#fffbeb;border:1px solid #f6e05e;color:#744210;padding:10px 14px;border-radius:6px;font-size:13px;margin-bottom:14px;">
        ⚠ Uploading <b>replaces ALL</b> historical data with the file's contents (delete &amp; insert).
        Keep one master file with all backdated rows. Live LUEU data is never touched.
    </div>

    <div style="margin-bottom:14px;font-size:13px;">
        Current dataset:
        <b id="hd-count">{{ status.count }}</b> rows
        {% if status.uploaded_at %}<span style="color:#718096;">— last upload {{ status.uploaded_at }}</span>{% endif %}
    </div>

    <div style="display:flex;gap:10px;flex-wrap:wrap;margin-bottom:16px;">
        <a class="btn" href="/api/module/RP01/historical/template"
           style="background:#3182ce;color:white;padding:8px 16px;border-radius:4px;text-decoration:none;font-size:13px;">⬇ Download Template</a>
        <input type="file" id="hd-file" accept=".xlsx,.xls,.csv" style="font-size:13px;">
        <button class="btn" onclick="hdPreview()" style="background:#805ad5;color:white;border:none;padding:8px 16px;border-radius:4px;cursor:pointer;font-size:13px;">Preview &amp; Reconcile</button>
        <button class="btn" id="hd-apply" onclick="hdApply()" disabled style="background:#48bb78;color:white;border:none;padding:8px 16px;border-radius:4px;cursor:pointer;font-size:13px;opacity:0.5;">Apply (Replace All)</button>
    </div>

    <div id="hd-result" style="font-size:13px;"></div>
</div>

<script>
let hdValid = false;

function hdFile() {
    const f = document.getElementById('hd-file').files[0];
    if (!f) { alert('Choose a file first.'); return null; }
    const fd = new FormData(); fd.append('file', f); return fd;
}

async function hdPreview() {
    const fd = hdFile(); if (!fd) return;
    document.getElementById('hd-result').innerHTML = 'Checking…';
    const res = await fetch('/api/module/RP01/historical/preview', {method:'POST', body:fd});
    const d = await res.json();
    if (d.error) { document.getElementById('hd-result').innerHTML = `<span style="color:#c53030">${d.error}</span>`; return; }
    let html = `<div style="margin-bottom:8px;">Parsed <b>${d.total_rows}</b> data row(s).</div>`;
    if (d.format_errors.length) {
        hdValid = false;
        html += `<div style="background:#fed7d7;color:#c53030;padding:8px 12px;border-radius:4px;margin-bottom:10px;">
            <b>${d.format_errors.length} format error(s)</b> — fix these before applying:<ul style="margin:6px 0 0;">`
            + d.format_errors.slice(0,50).map(e=>`<li>Row ${e.row}: ${e.message}</li>`).join('') + `</ul></div>`;
    } else {
        hdValid = true;
        html += `<div style="background:#c6f6d5;color:#276749;padding:8px 12px;border-radius:4px;margin-bottom:10px;">No format errors — ready to apply.</div>`;
    }
    // reconciliation
    for (const [col, info] of Object.entries(d.reconciliation)) {
        if (!info.unknown.length) continue;
        html += `<div style="margin-bottom:8px;"><b>${col}</b> — ${info.unknown.length} value(s) not in master:`
            + `<table style="font-size:12px;border-collapse:collapse;margin-top:4px;"><tr style="background:#edf2f7;"><th style="border:1px solid #cbd5e0;padding:3px 8px;">Value</th><th style="border:1px solid #cbd5e0;padding:3px 8px;">Count</th><th style="border:1px solid #cbd5e0;padding:3px 8px;">Did you mean?</th></tr>`
            + info.unknown.slice(0,100).map(u=>`<tr><td style="border:1px solid #cbd5e0;padding:3px 8px;">${u.value}</td><td style="border:1px solid #cbd5e0;padding:3px 8px;text-align:right;">${u.count}</td><td style="border:1px solid #cbd5e0;padding:3px 8px;color:#3182ce;">${(u.suggestions||[]).join(', ')}</td></tr>`).join('')
            + `</table></div>`;
    }
    html += `<div style="color:#718096;font-size:12px;margin-top:6px;">Unknown values are allowed (stored as-is) — they just won't match cargo/delay enrichment or filters. Fix spellings in the file and re-preview if needed.</div>`;
    document.getElementById('hd-result').innerHTML = html;
    const applyBtn = document.getElementById('hd-apply');
    applyBtn.disabled = !hdValid;
    applyBtn.style.opacity = hdValid ? '1' : '0.5';
}

async function hdApply() {
    if (!hdValid) return;
    if (!confirm('This will DELETE all existing historical rows and insert this file. Continue?')) return;
    const fd = hdFile(); if (!fd) return;
    const res = await fetch('/api/module/RP01/historical/apply', {method:'POST', body:fd});
    const d = await res.json();
    if (d.error) { alert(d.error + (d.format_errors ? '\n' + d.format_errors.slice(0,10).map(e=>`Row ${e.row}: ${e.message}`).join('\n') : '')); return; }
    document.getElementById('hd-count').textContent = d.inserted;
    document.getElementById('hd-result').innerHTML = `<div style="background:#c6f6d5;color:#276749;padding:10px 14px;border-radius:4px;">✓ Replaced historical data — ${d.inserted} row(s) inserted.</div>`;
}
</script>
{% endblock %}
```

- [ ] **Step 2: Manual check (after app is running)**

Log in as an **admin**, open `/module/RP01/historical-data/`. Expected: page renders with row count, Download Template works (opens a 2-sheet xlsx with dropdowns), Preview shows parsed count + reconciliation, Apply enables only when there are no format errors.

- [ ] **Step 3: Commit**

```bash
git add modules/RP01/RP01/historical_data/historical_data.html
git commit -m "feat(RP01): historical data upload + reconciliation UI"
```

---

## Task 6: Admin-only card on `rp01.html`

**Files:**
- Modify: `modules/RP01/RP01/rp01.html`

- [ ] **Step 1: Add the card**

In `modules/RP01/RP01/rp01.html`, inside the `<div class="report-cards">` block (after the existing cards, e.g. right after the `live-dashboard` card near line 137), add:

```html
    {% if session.get('is_admin') %}
    <a class="report-card" href="/module/RP01/historical-data/">
        <div class="card-icon">
            <span style="font-size:28px;">🗄️</span>
        </div>
        <div class="card-title">Historical Data (Admin)</div>
        <div class="card-desc">
            Upload backdated LUEU data (CSV/Excel) for use in reports &amp; dashboards.
        </div>
        <div class="card-arrow">Open &rarr;</div>
    </a>
    {% endif %}
```

- [ ] **Step 2: Verify visibility**

Log in as admin → card appears. Log in as non-admin → card absent, and visiting `/module/RP01/historical-data/` returns the no-access page.

- [ ] **Step 3: Commit**

```bash
git add modules/RP01/RP01/rp01.html
git commit -m "feat(RP01): admin-only Historical Data card on landing page"
```

---

## Task 7: New pivot data source `lueu-historical`

**Files:**
- Modify: `modules/RP01/RP01/custom_report/views.py`

- [ ] **Step 1: Register the source in the config dicts**

In `modules/RP01/RP01/custom_report/views.py`:

Change `VALID_SOURCES` (line 67) to include the new source:

```python
VALID_SOURCES = {'mbc-ops', 'vessel-ops', 'vessel-barge', 'lueu-equipment', 'lueu-historical', 'mbc-tat'}
```

Add to `DATE_COL_FILTERS` (after the `'lueu-equipment'` entry, around line 95):

```python
    'lueu-historical': {
        'entry_date': ("entry_date", False),
    },
```

Add to `DATE_COL_DEFAULTS` (around line 107):

```python
    'lueu-historical': 'entry_date',
```

- [ ] **Step 2: Add the query branch**

In the `pivot_data` function, add an `elif` branch after the `lueu-equipment` branch (after its closing `""", where_params)` near line 338). It runs the SAME column projection over a UNION of live + historical via a CTE. Note `where_params` is passed twice (one set per UNION leg):

```python
        elif source == 'lueu-historical':
            cur.execute(f"""
                WITH base AS (
                    SELECT equipment_name, shift, source_display, barge_name, cargo_name,
                           delay_name, system_name, route_name, berth_name, shift_incharge,
                           operator_name, quantity_uom, quantity, from_time, to_time, entry_date
                    FROM lueu_lines
                    WHERE {where_clause}
                    UNION ALL
                    SELECT equipment_name, shift, source_display, barge_name, cargo_name,
                           delay_name, system_name, route_name, berth_name, shift_incharge,
                           operator_name, quantity_uom, quantity, from_time, to_time, entry_date
                    FROM rp01_historical_lueu
                    WHERE {where_clause}
                )
                SELECT
                    COALESCE(b.equipment_name, '')      AS "Equipment",
                    COALESCE(b.shift, '')               AS "Shift",
                    COALESCE(b.source_display, '')      AS "VCN / MBC",
                    COALESCE(b.barge_name, '')          AS "Barge / MBC Name",
                    COALESCE(b.cargo_name, '')          AS "Cargo",
                    COALESCE(b.delay_name, '')          AS "Delay",
                    COALESCE(b.system_name, '')         AS "System",
                    COALESCE(b.route_name, '')          AS "Route",
                    COALESCE(b.berth_name, '')          AS "Berth",
                    COALESCE(b.shift_incharge, '')      AS "Shift Incharge",
                    COALESCE(b.operator_name, '')       AS "Operator",
                    COALESCE(b.quantity_uom, '')        AS "UOM",
                    COALESCE(CAST(b.quantity AS TEXT), '') AS "Quantity",
                    COALESCE(b.from_time, '')           AS "_from_time",
                    COALESCE(b.to_time, '')             AS "_to_time",
                    COALESCE(pdt.to_sof, '')               AS "Delay To SOF",
                    COALESCE(pdt.type, '')                  AS "Delay Type",
                    COALESCE(vc.cargo_type, '')             AS "Cargo Type",
                    COALESCE(vc.cargo_category, '')         AS "Cargo Category",
                    COALESCE(vc.cargo_category_2, '')       AS "Cargo Category 2",
                    COALESCE(vc.cargo_sub_category, '')     AS "Cargo Sub Category",
                    COALESCE(vc.cargo_sub_category_2, '')   AS "Cargo Sub Category 2",
                    COALESCE(b.entry_date::TEXT, '')        AS "Date",
                    COALESCE(LEFT(b.entry_date::TEXT, 4), '') AS "Year",
                    COALESCE(LEFT(b.entry_date::TEXT, 7), '') AS "Year-Month"
                FROM base b
                LEFT JOIN LATERAL (
                    SELECT to_sof, type
                    FROM port_delay_types WHERE name = b.delay_name LIMIT 1
                ) pdt ON TRUE
                LEFT JOIN LATERAL (
                    SELECT cargo_type, cargo_category, cargo_category_2, cargo_sub_category, cargo_sub_category_2
                    FROM vessel_cargo WHERE cargo_name = b.cargo_name LIMIT 1
                ) vc ON TRUE
                ORDER BY b.entry_date DESC
                LIMIT 10000
            """, where_params + where_params)
```

- [ ] **Step 3: Extend the Diff Hrs post-processing to cover the new source**

In `pivot_data`, change the post-processing guard (line 378) from:

```python
    if source == 'lueu-equipment':
```
to:
```python
    if source in ('lueu-equipment', 'lueu-historical'):
```

- [ ] **Step 4: Smoke-test the new source query**

First insert one out-of-range historical row, then query:
```bash
python -c "
from modules.RP01.RP01.historical_data import model
model.replace_all([{'entry_date':'2024-01-15','equipment_name':'BU 1','shift':'A','from_time':'06:00','to_time':'08:00','quantity':500,'cargo_name':None,'delay_name':None,'source_display':'TEST VESSEL','barge_name':'TESTBARGE','system_name':None,'route_name':None,'berth_name':None,'shift_incharge':None,'operator_name':None,'quantity_uom':'MT','remarks':None}], None)
print('seeded 1 historical row')
"
```
Expected (seed): `seeded 1 historical row`. (The actual query is exercised via the Flask test client in Step 5.)

- [ ] **Step 5: Verify columns match `lueu-equipment` and historical row appears**

Use the Flask test client (handles routing; bypass auth by setting a session user). Run:
```bash
python -c "
import app
c = app.app.test_client()
with c.session_transaction() as s:
    s['user_id'] = 1; s['is_admin'] = True
r = c.get('/api/module/RP01/pivot/data/lueu-historical?from_date=2024-01-01&to_date=2024-01-31')
rows = r.get_json()
print('rows:', len(rows))
assert rows, 'expected the seeded 2024 historical row'
assert 'Diff Hrs' in rows[0] and 'Equipment' in rows[0] and 'Date' in rows[0], rows[0].keys()
print('sample:', {k: rows[0][k] for k in ['Equipment','Date','Quantity','Diff Hrs','VCN / MBC']})
print('OK')
"
```
Expected: `rows: 1` (or more), the assert passes, and the sample shows `Date: 2024-01-15`, `Diff Hrs: 2.0`, `Equipment: BU 1`.
(If login is enforced differently, adapt the session keys to match `login_required`'s expectations — it only checks `session['user_id']`.)

- [ ] **Step 6: Clean up the seed row**

Run: `python -c "from modules.RP01.RP01.historical_data import model; model.replace_all([], None); print('cleared')"`
Expected: `cleared`.

- [ ] **Step 7: Commit**

```bash
git add modules/RP01/RP01/custom_report/views.py
git commit -m "feat(RP01): lueu-historical pivot source (live + historical union)"
```

---

## Task 8: Add the source to the report designer + dashboard dropdowns

**Files:**
- Modify: `modules/RP01/RP01/custom_report/custom_report.html`
- Modify: `modules/RP01/RP01/dashboard/dashboard.html`

- [ ] **Step 1: custom_report.html — add the option**

In `modules/RP01/RP01/custom_report/custom_report.html`, after the `lueu-equipment` option (line 550):

```html
            <option value="lueu-historical">LUEU (incl. historical)</option>
```

- [ ] **Step 2: custom_report.html — add the date-col config**

In the date-column config object (the block containing `'lueu-equipment': { filterable: [ { key: 'entry_date', ... } ] }` near line 676), add a sibling entry:

```javascript
    'lueu-historical': {
        filterable: [
            { key: 'entry_date', label: 'Date' },
        ],
    },
```

- [ ] **Step 3: custom_report.html — add the source label**

In the source-label map (the object containing `'lueu-equipment': 'LUEU - Equipment Utilization'` near line 1357), add:

```javascript
        'lueu-historical': 'LUEU (incl. historical)',
```

- [ ] **Step 4: dashboard.html — add the option**

In `modules/RP01/RP01/dashboard/dashboard.html`, after the `lueu-equipment` option (line 263):

```html
            <option value="lueu-historical">LUEU (incl. historical)</option>
```

- [ ] **Step 5: dashboard.html — add the date-col config**

In the config object near line 410 (with `'lueu-equipment': { filterable: [ { key: 'entry_date', ... } ] }`), add:

```javascript
    'lueu-historical': {
        filterable: [
            { key: 'entry_date', label: 'Date' },
        ],
    },
```

- [ ] **Step 6: dashboard.html — add the source label**

In the label map near line 944 (with `'lueu-equipment': 'LUEU - Equipment Utilization'`), add:

```javascript
        'lueu-historical': 'LUEU (incl. historical)',
```

- [ ] **Step 7: Manual check**

Open the custom report designer and the dashboard widget config; "LUEU (incl. historical)" appears in the Data Source dropdown, selecting it loads data with the same columns as LUEU - Equipment Utilization, and the date filter offers "Date".

- [ ] **Step 8: Commit**

```bash
git add modules/RP01/RP01/custom_report/custom_report.html modules/RP01/RP01/dashboard/dashboard.html
git commit -m "feat(RP01): expose lueu-historical source in report designer + dashboard"
```

---

## Task 9: End-to-end verification

- [ ] **Step 1: Run the unit tests**

Run: `python -m pytest test_rp01_historical.py -q`
Expected: all PASS.

- [ ] **Step 2: Full round-trip via the app (admin)**

1. Download the template from the Historical Data page.
2. Fill 3–4 Data rows (some with a deliberately misspelled cargo/equipment to exercise reconciliation), save as `.xlsx`.
3. Preview → confirm parsed count, format errors (if any), and the "did you mean" suggestions for the misspelled values.
4. Apply → confirm row count updates.
5. In the custom report designer, choose "LUEU (incl. historical)", set the date range to cover the historical dates, and confirm the rows appear with Cargo Type/Diff Hrs populated for recognized cargos.
6. Confirm the original "LUEU - Equipment Utilization" source still returns only live rows (no historical dates).

- [ ] **Step 3: Final commit (if any docs/tweaks)**

```bash
git add -A
git commit -m "test(RP01): historical data end-to-end verification notes" --allow-empty
```

---

## Notes for the implementer
- **Connections:** every DB function opens and closes its own connection via `get_db()`/`get_cursor()` (RealDictCursor) — follow that pattern; don't share connections across requests.
- **No `lueu_lines` writes anywhere** in this feature — historical data is isolated in `rp01_historical_lueu`.
- **Full replace is destructive by design** (TRUNCATE) — that's the agreed behavior; the UI confirms before applying.
- **Admin gate**: `historical_data` page + all three API endpoints check `session.get('is_admin')`; the rp01.html card is wrapped in `{% if session.get('is_admin') %}`.
- If the Flask entrypoint isn't `app.app`, substitute the correct module in the verification commands; the assertions themselves are entrypoint-independent.
