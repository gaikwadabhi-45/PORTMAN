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
    import io, csv as _csv
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
