from flask import render_template, request, jsonify, session, redirect, url_for, Response
from functools import wraps
from datetime import date, datetime
from collections import defaultdict
import io
import re

from .. import bp
from database import get_db, get_cursor

# ── Excel colour / style constants ─────────────────────────────────────────
XL_GREY    = 'C0C0C0'
XL_LAVEND  = 'CCCCFF'
XL_CYAN    = 'CCFFFF'
XL_WHITE   = 'FFFFFF'
XL_TITLE_SZ = 14
XL_NORM_SZ  = 10
XL_SMALL_SZ = 9

from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter

_thin   = Side(style='thin',   color='000000')
_med    = Side(style='medium', color='000000')
_bdr    = Border(left=_thin,  right=_thin,  top=_thin,  bottom=_thin)
_bdr_ml = Border(left=_med,   right=_thin,  top=_thin,  bottom=_thin)
_ctr    = Alignment(horizontal='center', vertical='center', wrap_text=True)
_left   = Alignment(horizontal='left',   vertical='center', wrap_text=True)
_right  = Alignment(horizontal='right',  vertical='center', wrap_text=True)

ACCOUNT_LABELS = {
    'Vessel Account':      'On Mother Vessel Account',
    'Port Account':        'On Port / Exim Team Account',
    'Weather Account':     'Bad Weather / Force Majeure',
    'Shipper Account':     'On Shipper Account',
    'Receiver Account':    'On Receiver Account',
    'Third Party Account': 'Third Party Account',
}


def _fill(hex_color):
    return PatternFill('solid', fgColor=hex_color)


def _font(bold=False, size=XL_NORM_SZ, color='000000'):
    return Font(name='Calibri', bold=bold, size=size, color=color)


def _fmt_mins(total_mins):
    if total_mins is None:
        return '—'
    h, m = divmod(int(round(abs(total_mins))), 60)
    return f'{h}:{m:02d}'


def _fmt_modu(total_mins):
    """Format minutes as HH.MM (e.g. 290 -> '04.50')"""
    if not total_mins:
        return ''
    h, m = divmod(int(round(abs(float(total_mins)))), 60)
    return f'{h:02d}.{m:02d}'


def _parse_dt(val):
    if not val:
        return None
    if isinstance(val, datetime):
        return val
    try:
        return datetime.fromisoformat(str(val))
    except Exception:
        return None


def _fmt_dt(val):
    dt = _parse_dt(val)
    return dt.strftime('%d-%m-%Y %H:%M') if dt else ''


def _day_key(val):
    dt = _parse_dt(val)
    return dt.date() if dt else None


def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated


# ── Data fetch ──────────────────────────────────────────────────────────────

_DATE_FIELDS = {
    'discharge_commenced': 'h.discharge_commenced',
    'discharge_completed': 'h.discharge_completed',
    'nor_tendered':        'h.nor_tendered',
}
_DATE_FIELD_DEFAULT = 'discharge_commenced'


def _fetch_list(from_date, to_date, date_field=None):
    date_col = _DATE_FIELDS.get(date_field or _DATE_FIELD_DEFAULT,
                                _DATE_FIELDS[_DATE_FIELD_DEFAULT])
    conn = get_db()
    cur = get_cursor(conn)
    # Build WHERE: date range applies only when the chosen field is not NULL
    cur.execute(f"""
        SELECT
            h.id,
            h.doc_num,
            h.vcn_doc_num,
            h.vessel_name,
            h.discharge_commenced,
            h.discharge_completed,
            h.nor_tendered,
            h.doc_status,
            v.vessel_agent_name,
            v.operation_type,
            COALESCE(SUM(cd.bl_quantity), 0) AS bl_qty,
            STRING_AGG(DISTINCT cd.cargo_name, ', ') AS cargo_names
        FROM ldud_header h
        LEFT JOIN vcn_header v ON v.id = h.vcn_id
        LEFT JOIN vcn_cargo_declaration cd ON cd.vcn_id = h.vcn_id
        WHERE LOWER(h.operation_type) = 'import'
          AND ({date_col} IS NULL
               OR (DATE({date_col}) >= %s AND DATE({date_col}) <= %s))
        GROUP BY h.id, h.doc_num, h.vcn_doc_num, h.vessel_name,
                 h.discharge_commenced, h.discharge_completed, h.nor_tendered,
                 h.doc_status, v.vessel_agent_name, v.operation_type
        ORDER BY {date_col} DESC NULLS LAST
    """, (from_date, to_date))
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return rows


def _fetch_vessel_data(ldud_id):
    conn = get_db()
    cur = get_cursor(conn)

    cur.execute("SELECT * FROM ldud_header WHERE id = %s", (ldud_id,))
    header_row = cur.fetchone()
    if not header_row:
        conn.close()
        return {'header': {}, 'vcn': {}, 'cargo_list': [],
                'delays': [], 'vessel_ops': [], 'barge_lines': [], 'anchorages': []}
    header = dict(header_row)

    vcn_id = header.get('vcn_id')
    vcn = {}
    cargo_list = []
    if vcn_id:
        cur.execute("SELECT * FROM vcn_header WHERE id = %s", (vcn_id,))
        row = cur.fetchone()
        if row:
            vcn = dict(row)
        cur.execute(
            "SELECT cargo_name, bl_quantity, quantity_uom, bl_no FROM vcn_cargo_declaration WHERE vcn_id = %s",
            (vcn_id,))
        cargo_list = [dict(r) for r in cur.fetchall()]

    cur.execute("SELECT * FROM ldud_delays WHERE ldud_id = %s ORDER BY start_datetime", (ldud_id,))
    delays = [dict(r) for r in cur.fetchall()]

    cur.execute("SELECT * FROM ldud_vessel_operations WHERE ldud_id = %s ORDER BY start_time", (ldud_id,))
    vessel_ops = [dict(r) for r in cur.fetchall()]

    cur.execute("SELECT * FROM ldud_barge_lines WHERE ldud_id = %s ORDER BY along_side_vessel", (ldud_id,))
    barge_lines = [dict(r) for r in cur.fetchall()]

    cur.execute("SELECT * FROM ldud_anchorage WHERE ldud_id = %s ORDER BY id", (ldud_id,))
    anchorages = [dict(r) for r in cur.fetchall()]

    conn.close()
    return {
        'header': header,
        'vcn': vcn,
        'cargo_list': cargo_list,
        'delays': delays,
        'vessel_ops': vessel_ops,
        'barge_lines': barge_lines,
        'anchorages': anchorages,
    }


# ── Excel builder ───────────────────────────────────────────────────────────

def _write_vessel_sheet(ws, data):
    """Write one vessel's discharged report onto an existing worksheet."""
    header      = data['header']
    vcn         = data['vcn']
    cargo_list  = data['cargo_list']
    delays      = data['delays']
    vessel_ops  = data['vessel_ops']
    barge_lines = data['barge_lines']

    vessel_name    = header.get('vessel_name', 'Vessel')
    doc_num        = header.get('doc_num', '')
    bl_qty         = sum(float(c.get('bl_quantity') or 0) for c in cargo_list)
    cargo_nm       = ', '.join(c['cargo_name'] for c in cargo_list if c.get('cargo_name')) or ''
    type_of_disc   = vcn.get('type_of_discharge', '')
    vessel_agent   = vcn.get('vessel_agent_name', '')
    operation_type = header.get('operation_type', '')
    arrived_mfl    = _fmt_dt(header.get('arrived_mfl'))
    arrived_mbpt   = _fmt_dt(header.get('arrived_mbpt'))
    disc_start_str = _fmt_dt(header.get('discharge_commenced'))
    disc_end_str   = _fmt_dt(header.get('discharge_completed'))

    # 7 columns: A=20, B=8, C=8, D=14, E=8, F=18, G=22
    for i, w in enumerate([20, 8, 8, 14, 8, 18, 22], 1):
        ws.column_dimensions[get_column_letter(i)].width = w

    NC = 7
    _nw = Alignment(horizontal='left', vertical='center', wrap_text=False)

    def _mbdr(row, c1, c2, fill=XL_WHITE):
        """Apply perimeter thin borders to every cell in a horizontal merged range."""
        for ci in range(c1, c2 + 1):
            b = Border(
                left   = _thin if ci == c1 else None,
                right  = _thin if ci == c2 else None,
                top    = _thin,
                bottom = _thin,
            )
            try:
                ws.cell(row, ci).border = b
                ws.cell(row, ci).fill   = _fill(fill)
            except AttributeError:
                pass  # MergedCell in older openpyxl

    # ── Row 1: Title ─────────────────────────────────────────────────────────
    ws.row_dimensions[1].height = 28
    ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=NC)
    c = ws.cell(1, 1, 'MOTHER VESSSEL DISCHARGED REPORT')
    c.font = _font(bold=True, size=XL_TITLE_SZ)
    c.fill = _fill(XL_WHITE); c.alignment = _ctr; c.border = _bdr
    _mbdr(1, 1, NC)

    # ── Row 2: Blank ─────────────────────────────────────────────────────────
    ws.row_dimensions[2].height = 10
    ws.merge_cells(start_row=2, start_column=1, end_row=2, end_column=NC)
    c = ws.cell(2, 1, '')
    c.fill = _fill(XL_WHITE); c.border = _bdr
    _mbdr(2, 1, NC)

    # ── Rows 3-10: Header block ───────────────────────────────────────────────
    # A:B merged = left label (bold) | C:E merged = left value | F = right label | G = right value
    def _hdr2(row, ll, lv, rl, rv, height=16):
        ws.row_dimensions[row].height = height
        ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=2)
        c = ws.cell(row, 1, ll)
        c.font = _font(bold=True); c.fill = _fill(XL_WHITE); c.alignment = _left; c.border = _bdr
        _mbdr(row, 1, 2)
        ws.merge_cells(start_row=row, start_column=3, end_row=row, end_column=5)
        c = ws.cell(row, 3, lv if lv is not None else '')
        c.font = _font(); c.fill = _fill(XL_WHITE); c.alignment = _left; c.border = _bdr
        _mbdr(row, 3, 5)
        c = ws.cell(row, 6, rl)
        c.font = _font(bold=True); c.fill = _fill(XL_WHITE); c.alignment = _left; c.border = _bdr
        c = ws.cell(row, 7, rv if rv is not None else '')
        c.font = _font(); c.fill = _fill(XL_WHITE); c.alignment = _left; c.border = _bdr

    _hdr2(3,  'Mother Vessel',                                               vessel_name,    'Mother Vessel Sr. No', doc_num)
    _hdr2(4,  'Cargo Handler',                                               type_of_disc,   'Stevedores',           vessel_agent)
    _hdr2(5,  'Cargo Type',                                                  cargo_nm,       'Quantity as B/L',      bl_qty or '')
    _hdr2(6,  'Charter Type',                                                operation_type, 'Custom Cleared',       'N/A')
    _hdr2(7,  'Arrived at MFL',                                              arrived_mfl,    'Arrived at MbPT',      arrived_mbpt)
    _hdr2(8,  'Discharge Commenced',                                         disc_start_str, 'Discharge Completed',  disc_end_str)
    _hdr2(9,  'Committed Discharge Rate as per Charter Party Agreement',     '',             'Demurrage Rate',       '', height=28)
    _hdr2(10, 'Committed Discharge Rate\nas per Barge Owner Agreement',      '',             'Despatch Rate',        '', height=32)

    # ── Row 11: Blank separator ───────────────────────────────────────────────
    ws.row_dimensions[11].height = 8
    for ci in range(1, NC + 1):
        c = ws.cell(11, ci, '')
        c.fill = _fill(XL_WHITE); c.border = _bdr

    # ── Row 12: Day-wise section headers ──────────────────────────────────────
    ws.row_dimensions[12].height = 18
    c = ws.cell(12, 1, 'Day Wise Discharge : ')
    c.font = _font(bold=True); c.fill = _fill(XL_GREY); c.alignment = _nw; c.border = _bdr
    ws.merge_cells(start_row=12, start_column=2, end_row=12, end_column=3)
    c = ws.cell(12, 2, 'M. V. Discharge')
    c.font = _font(bold=True); c.alignment = _ctr
    _mbdr(12, 2, 3, XL_GREY)
    c = ws.cell(12, 4, 'REMARKS')
    c.font = _font(bold=True); c.fill = _fill(XL_GREY); c.alignment = _ctr; c.border = _bdr
    c = ws.cell(12, 5, '')
    c.fill = _fill(XL_WHITE); c.border = _bdr
    c = ws.cell(12, 6, 'Day Wise Barge Discharge : ')
    c.font = _font(bold=True); c.fill = _fill(XL_GREY); c.alignment = _nw; c.border = _bdr
    c = ws.cell(12, 7, 'Jetty Discharge')
    c.font = _font(bold=True); c.fill = _fill(XL_GREY); c.alignment = _ctr; c.border = _bdr

    # ── Day-wise data ─────────────────────────────────────────────────────────
    mv_by_day = defaultdict(float)
    for op in vessel_ops:
        k = _day_key(op.get('start_time'))
        if k:
            mv_by_day[k] += float(op.get('quantity') or 0)

    bg_by_day = defaultdict(float)
    for bl in barge_lines:
        k = _day_key(bl.get('completed_discharge_berth') or bl.get('along_side_vessel'))
        if k:
            bg_by_day[k] += float(bl.get('discharge_quantity') or 0)

    mv_dates = sorted(mv_by_day.keys())
    bg_dates = sorted(bg_by_day.keys())
    max_rows = max(len(mv_dates), len(bg_dates), 1)
    mv_total = bg_total = 0.0

    r = 13
    for i in range(max_rows):
        ws.row_dimensions[r].height = 15
        if i < len(mv_dates):
            dk = mv_dates[i]; qty = mv_by_day[dk]; mv_total += qty
            c = ws.cell(r, 1, dk.strftime('%d-%b-%y').upper())
            c.font = _font(); c.fill = _fill(XL_WHITE); c.alignment = _ctr; c.border = _bdr
            ws.merge_cells(start_row=r, start_column=2, end_row=r, end_column=3)
            c = ws.cell(r, 2, int(round(qty)))
            c.font = _font(); c.alignment = _ctr
            _mbdr(r, 2, 3)
        else:
            c = ws.cell(r, 1, '')
            c.fill = _fill(XL_WHITE); c.border = _bdr
            ws.merge_cells(start_row=r, start_column=2, end_row=r, end_column=3)
            c = ws.cell(r, 2, '')
            _mbdr(r, 2, 3)
        c = ws.cell(r, 4, '')
        c.fill = _fill(XL_WHITE); c.border = _bdr
        c = ws.cell(r, 5, '')
        c.fill = _fill(XL_WHITE); c.border = _bdr
        if i < len(bg_dates):
            dk = bg_dates[i]; qty = bg_by_day[dk]; bg_total += qty
            c = ws.cell(r, 6, dk.strftime('%d-%b-%y').upper())
            c.font = _font(); c.fill = _fill(XL_WHITE); c.alignment = _ctr; c.border = _bdr
            c = ws.cell(r, 7, int(round(qty)))
            c.font = _font(); c.fill = _fill(XL_WHITE); c.alignment = _ctr; c.border = _bdr
        else:
            c = ws.cell(r, 6, '')
            c.fill = _fill(XL_WHITE); c.border = _bdr
            c = ws.cell(r, 7, '')
            c.fill = _fill(XL_WHITE); c.border = _bdr
        r += 1

    # ── Total row (numbers only, no "TOTAL" label) ────────────────────────────
    ws.row_dimensions[r].height = 16
    c = ws.cell(r, 1, '')
    c.fill = _fill(XL_WHITE); c.border = _bdr
    ws.merge_cells(start_row=r, start_column=2, end_row=r, end_column=3)
    c = ws.cell(r, 2, int(round(mv_total)))
    c.font = _font(bold=True); c.alignment = _ctr
    _mbdr(r, 2, 3)
    c = ws.cell(r, 4, '')
    c.fill = _fill(XL_WHITE); c.border = _bdr
    c = ws.cell(r, 5, '')
    c.fill = _fill(XL_WHITE); c.border = _bdr
    c = ws.cell(r, 6, '')
    c.fill = _fill(XL_WHITE); c.border = _bdr
    c = ws.cell(r, 7, int(round(bg_total)))
    c.font = _font(bold=True); c.fill = _fill(XL_WHITE); c.alignment = _ctr; c.border = _bdr
    r += 1

    # ── Blank row ─────────────────────────────────────────────────────────────
    ws.row_dimensions[r].height = 8
    for ci in range(1, NC + 1):
        c = ws.cell(r, ci, '')
        c.fill = _fill(XL_WHITE); c.border = _bdr
    r += 1

    # ── Delay log ─────────────────────────────────────────────────────────────
    ws.row_dimensions[r].height = 18
    ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=NC)
    c = ws.cell(r, 1, ' Average Mother Vessel Delays : ')
    c.font = _font(bold=True); c.alignment = _left
    _mbdr(r, 1, NC, XL_GREY)
    r += 1

    ws.row_dimensions[r].height = 16
    ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=2)
    c = ws.cell(r, 1, 'DELAY TYPE NAME')
    c.font = _font(bold=True); c.alignment = _ctr
    _mbdr(r, 1, 2, XL_GREY)
    ws.merge_cells(start_row=r, start_column=3, end_row=r, end_column=5)
    c = ws.cell(r, 3, 'DELAY DESCRIPTION')
    c.font = _font(bold=True); c.alignment = _ctr
    _mbdr(r, 3, 5, XL_GREY)
    c = ws.cell(r, 6, 'MODU')
    c.font = _font(bold=True); c.fill = _fill(XL_GREY); c.alignment = _ctr; c.border = _bdr
    c = ws.cell(r, 7, '')
    c.fill = _fill(XL_GREY); c.border = _bdr
    r += 1

    total_delay_mins = 0.0
    for d in delays:
        ws.row_dimensions[r].height = 15
        mins = float(d.get('total_time_mins') or 0)
        total_delay_mins += mins
        ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=2)
        c = ws.cell(r, 1, d.get('delay_name', ''))
        c.font = _font(size=XL_SMALL_SZ); c.alignment = _left
        _mbdr(r, 1, 2)
        ws.merge_cells(start_row=r, start_column=3, end_row=r, end_column=5)
        c = ws.cell(r, 3, d.get('equipment_name', ''))
        c.font = _font(size=XL_SMALL_SZ); c.alignment = _left
        _mbdr(r, 3, 5)
        c = ws.cell(r, 6, _fmt_modu(mins))
        c.font = _font(size=XL_SMALL_SZ); c.fill = _fill(XL_WHITE); c.alignment = _ctr; c.border = _bdr
        c = ws.cell(r, 7, '')
        c.fill = _fill(XL_WHITE); c.border = _bdr
        r += 1

    # Total delays: A:E right-aligned, F = total MODU
    ws.row_dimensions[r].height = 16
    ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=5)
    c = ws.cell(r, 1, 'Total Delays')
    c.font = _font(bold=True); c.alignment = _right
    _mbdr(r, 1, 5)
    c = ws.cell(r, 6, _fmt_modu(total_delay_mins))
    c.font = _font(bold=True); c.fill = _fill(XL_WHITE); c.alignment = _ctr; c.border = _bdr
    c = ws.cell(r, 7, '')
    c.fill = _fill(XL_WHITE); c.border = _bdr
    r += 1

    # ── Delay classification ──────────────────────────────────────────────────
    PRE_CATS = [
        ('Port Account',        'A. On Exim Team Account',     XL_CYAN,  True),
        ('Shipper Account',     'B. On Shipping Team Account', XL_WHITE, False),
        ('Third Party Account', 'C. Miscellaneous',            XL_WHITE, False),
    ]
    POST_CATS = [
        ('Vessel Account',   'On Mother Vessel Account',   XL_CYAN,  True),
        ('Receiver Account', 'On Barge Owner Account',     XL_WHITE, False),
        ('RM Procurement',   'RM Procurement',             XL_WHITE, False),
        ('Weather Account',  'Other Delays/Force Majeure', XL_WHITE, False),
    ]

    pre_groups  = defaultdict(list)
    post_groups = defaultdict(list)
    pre_keys  = {k for k, _, _, _ in PRE_CATS}
    post_keys = {k for k, _, _, _ in POST_CATS}
    for d in delays:
        acct = d.get('delay_account_type', '')
        if acct in pre_keys:
            pre_groups[acct].append(d)
        elif acct in post_keys:
            post_groups[acct].append(d)

    # Pre-Berthing header (lavender, full width)
    ws.row_dimensions[r].height = 18
    ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=NC)
    c = ws.cell(r, 1, 'Pre-Berthing Delays')
    c.font = _font(bold=True); c.alignment = _ctr
    _mbdr(r, 1, NC, XL_LAVEND)
    r += 1

    def _cls_cat(row, items, cat_label, fill, bold, seq=None):
        """Write one classification category. Returns (next_row, seq)."""
        if not items:
            ws.row_dimensions[row].height = 15
            ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=2)
            c = ws.cell(row, 1, cat_label)
            c.font = _font(bold=bold); c.alignment = _left
            _mbdr(row, 1, 2, fill)
            ws.merge_cells(start_row=row, start_column=3, end_row=row, end_column=5)
            c = ws.cell(row, 3, '')
            _mbdr(row, 3, 5, fill)
            c = ws.cell(row, 6, '')
            c.fill = _fill(fill); c.border = _bdr
            c = ws.cell(row, 7, '')
            c.fill = _fill(fill); c.border = _bdr
            return row + 1, seq
        for idx, d in enumerate(items):
            ws.row_dimensions[row].height = 15
            mins = float(d.get('total_time_mins') or 0)
            hrs  = round(mins / 60.0, 3) if mins else ''
            desc = d.get('equipment_name', '') or d.get('delay_name', '')
            if seq is not None:
                desc = f'{desc} ({seq})'
                seq += 1
            ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=2)
            c = ws.cell(row, 1, cat_label if idx == 0 else '')
            c.font = _font(bold=(bold and idx == 0)); c.alignment = _left
            _mbdr(row, 1, 2, fill)
            ws.merge_cells(start_row=row, start_column=3, end_row=row, end_column=5)
            c = ws.cell(row, 3, desc)
            c.font = _font(size=XL_SMALL_SZ); c.alignment = _left
            _mbdr(row, 3, 5, fill)
            c = ws.cell(row, 6, hrs)
            c.font = _font(size=XL_SMALL_SZ); c.fill = _fill(fill); c.alignment = _ctr; c.border = _bdr
            c = ws.cell(row, 7, '')
            c.fill = _fill(fill); c.border = _bdr
            row += 1
        return row, seq

    for acct, lbl, fill, bold in PRE_CATS:
        r, _ = _cls_cat(r, pre_groups[acct], lbl, fill, bold)

    # Post-Berthing label row (A:B only, white, not bold)
    ws.row_dimensions[r].height = 15
    ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=2)
    c = ws.cell(r, 1, 'Post-Berthing Delays')
    c.font = _font(bold=False); c.alignment = _left
    _mbdr(r, 1, 2)
    ws.merge_cells(start_row=r, start_column=3, end_row=r, end_column=5)
    c = ws.cell(r, 3, '')
    _mbdr(r, 3, 5)
    c = ws.cell(r, 6, '')
    c.fill = _fill(XL_WHITE); c.border = _bdr
    c = ws.cell(r, 7, '')
    c.fill = _fill(XL_WHITE); c.border = _bdr
    r += 1

    seq = 1
    for acct, lbl, fill, bold in POST_CATS:
        r, seq = _cls_cat(r, post_groups[acct], lbl, fill, bold, seq)

    # Classification total row
    ws.row_dimensions[r].height = 15
    ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=2)
    c = ws.cell(r, 1, 'Total Delays')
    c.font = _font(bold=True); c.alignment = _left
    _mbdr(r, 1, 2)
    ws.merge_cells(start_row=r, start_column=3, end_row=r, end_column=5)
    c = ws.cell(r, 3, '')
    _mbdr(r, 3, 5)
    c = ws.cell(r, 6, '')
    c.fill = _fill(XL_WHITE); c.border = _bdr
    c = ws.cell(r, 7, '')
    c.fill = _fill(XL_WHITE); c.border = _bdr
    r += 1

    # ── Performance section ───────────────────────────────────────────────────
    disc_com = _parse_dt(header.get('discharge_commenced'))
    disc_cmp = _parse_dt(header.get('discharge_completed'))
    actual_days = None
    gross_rate  = None
    if disc_com and disc_cmp:
        delta = (disc_cmp - disc_com).total_seconds() / 86400
        actual_days = round(delta, 3)
        if actual_days and bl_qty:
            gross_rate = round(bl_qty / actual_days, 2)

    # Lavender separator row
    ws.row_dimensions[r].height = 12
    ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=NC)
    c = ws.cell(r, 1, '')
    _mbdr(r, 1, NC, XL_LAVEND)
    r += 1

    def _perf(row, ll, dval, rl, rv, ll_fill=XL_WHITE):
        ws.row_dimensions[row].height = 16
        ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=3)
        c = ws.cell(row, 1, ll)
        c.font = _font(); c.alignment = _nw
        _mbdr(row, 1, 3, ll_fill)
        c = ws.cell(row, 4, dval if dval is not None else '')
        c.font = _font(); c.fill = _fill(XL_WHITE); c.alignment = _ctr; c.border = _bdr
        ws.merge_cells(start_row=row, start_column=5, end_row=row, end_column=6)
        c = ws.cell(row, 5, rl)
        c.font = _font(); c.alignment = _nw
        _mbdr(row, 5, 6)
        c = ws.cell(row, 7, rv if rv is not None else '')
        c.font = _font(); c.fill = _fill(XL_WHITE); c.alignment = _ctr; c.border = _bdr

    _perf(r, '',                                              actual_days, 'Gross Discharge Rate Achieved (A/G)', gross_rate)
    r += 1
    _perf(r, 'Savings / Delay Calculations',                  '',          'As per Charter Party Agreement',       '')
    r += 1
    _perf(r, '',                                              '',          'Net Discharge Rate Achieved (A/G)',     '')
    r += 1
    _perf(r, 'Time allowed since MV reported at anchorage',   '',          '',                                      '', XL_LAVEND)
    r += 1
    _perf(r, 'Time Saved (+)   /   Delayed (-) ',             '',          '',                                      '')
    r += 1
    _perf(r, '',                                              '',          '',                                      '')
    r += 1

    # Remarks: A:D merged over 2 rows, E:F merged per row, G per row
    ws.merge_cells(start_row=r, start_column=1, end_row=r + 1, end_column=4)
    c = ws.cell(r, 1, 'Remarks ')
    c.font = _font(); c.alignment = _left
    _mbdr(r,     1, 4)
    _mbdr(r + 1, 1, 4)
    for rr in (r, r + 1):
        ws.row_dimensions[rr].height = 16
        ws.merge_cells(start_row=rr, start_column=5, end_row=rr, end_column=6)
        c = ws.cell(rr, 5, '')
        _mbdr(rr, 5, 6)
        c = ws.cell(rr, 7, '')
        c.fill = _fill(XL_WHITE); c.border = _bdr
    r += 2

    # Two blank rows
    for _ in range(2):
        ws.row_dimensions[r].height = 16
        ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=NC)
        c = ws.cell(r, 1, '')
        _mbdr(r, 1, NC)
        r += 1


def _build_excel(data):
    """Build a single-vessel workbook."""
    from openpyxl import Workbook
    wb = Workbook()
    ws = wb.active
    vessel_name = data['header'].get('vessel_name', 'Vessel')
    safe_title = re.sub(r'[\\/*?\[\]:]', '_', vessel_name)[:31]
    ws.title = safe_title or 'Report'
    _write_vessel_sheet(ws, data)
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf


def _build_all_excel(vessels_data):
    """Build a multi-sheet workbook — one sheet per vessel."""
    from openpyxl import Workbook
    wb = Workbook()
    first = True
    for data in vessels_data:
        vessel_name = data['header'].get('vessel_name', 'Vessel')
        safe_title = re.sub(r'[\\/*?\[\]:]', '_', vessel_name)[:31] or 'Sheet'
        if first:
            ws = wb.active
            ws.title = safe_title
            first = False
        else:
            ws = wb.create_sheet(safe_title)
        _write_vessel_sheet(ws, data)
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf


# ── Routes ──────────────────────────────────────────────────────────────────

@bp.route('/module/RP01/vessel-discharged/')
@login_required
def vessel_discharged_index():
    return render_template('vessel_discharged/vessel_discharged_list.html',
                           username=session.get('username'))


@bp.route('/api/module/RP01/vessel-discharged/data')
@login_required
def vessel_discharged_data():
    from_date  = request.args.get('from_date',  date.today().replace(day=1).strftime('%Y-%m-%d'))
    to_date    = request.args.get('to_date',    date.today().strftime('%Y-%m-%d'))
    date_field = request.args.get('date_field', _DATE_FIELD_DEFAULT)
    rows = _fetch_list(from_date, to_date, date_field)
    for row in rows:
        for k, v in row.items():
            if hasattr(v, 'isoformat'):
                row[k] = v.isoformat()
    return jsonify(rows)


@bp.route('/api/module/RP01/vessel-discharged/download-all')
@login_required
def vessel_discharged_download_all():
    from_date  = request.args.get('from_date',  date.today().replace(day=1).strftime('%Y-%m-%d'))
    to_date    = request.args.get('to_date',    date.today().strftime('%Y-%m-%d'))
    date_field = request.args.get('date_field', _DATE_FIELD_DEFAULT)
    rows = _fetch_list(from_date, to_date, date_field)
    if not rows:
        return Response('No records in selected range', status=404)
    vessels_data = [_fetch_vessel_data(r['id']) for r in rows]
    buf = _build_all_excel(vessels_data)
    fname = f'MVDischarged_{from_date}_to_{to_date}.xlsx'
    return Response(
        buf.getvalue(),
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        headers={'Content-Disposition': f'attachment; filename="{fname}"'},
    )


@bp.route('/api/module/RP01/vessel-discharged/<int:ldud_id>/download')
@login_required
def vessel_discharged_download(ldud_id):
    data = _fetch_vessel_data(ldud_id)
    if not data['header']:
        return jsonify({'error': 'Record not found'}), 404
    buf = _build_excel(data)
    vessel = re.sub(r'[^A-Za-z0-9_\-]', '_', data['header'].get('vessel_name', 'vessel'))
    fname  = f'MVDischarged_{vessel}.xlsx'
    return Response(
        buf.getvalue(),
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        headers={'Content-Disposition': f'attachment; filename="{fname}"'},
    )
