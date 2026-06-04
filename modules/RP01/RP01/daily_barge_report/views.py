from flask import render_template, request, jsonify, session, redirect, url_for, Response
from functools import wraps
from datetime import date, datetime, timedelta
from collections import defaultdict
import io
import re

from .. import bp
from database import get_db, get_cursor

# ── Excel colour / style constants ─────────────────────────────────────────
XL_GREY     = 'C0C0C0'
XL_LAVEND   = 'CCCCFF'
XL_CYAN     = 'CCFFFF'
XL_WHITE    = 'FFFFFF'
XL_NAVY     = '1F4E78'
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


def _fill(hex_color):
    return PatternFill('solid', fgColor=hex_color)


def _font(bold=False, size=XL_NORM_SZ, color='000000'):
    return Font(name='Calibri', bold=bold, size=size, color=color)


def _fmt_dt(val):

    if not val:
        return ''

    try:
        dt = _parse_tat_datetime(val)
        return dt.strftime('%d-%m-%Y %H:%M')

    except Exception:
        return ''

def _safe_float(val):
    try:
        return float(val or 0)
    except (TypeError, ValueError):
        return 0.0

def _parse_tat_datetime(val):

    if not val:
        return None

    val = str(val)

    if "T24:" in val:

        date_part, time_part = val.split("T")

        dt = datetime.strptime(
            date_part,
            "%Y-%m-%d"
        ) + timedelta(days=1)

        hh, mm = time_part.split(":")

        return dt.replace(
            hour=0,
            minute=int(mm)
        )

    return datetime.fromisoformat(val)


def _calc_tat(start_val, end_val):

    try:


        start_dt = _parse_tat_datetime(start_val)
        end_dt = _parse_tat_datetime(end_val)



        diff = end_dt - start_dt

        return f"{round(diff.total_seconds()/3600,2)} Hrs"

    except Exception as e:

       

        return ''
    
def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated


# ── Data fetch 

_STATUS_FILTERS = {
    
    'all': {

'date_col': 'l.trip_start',

'condition': """
    (
        -- IN TRANSIT
        (
            l.trip_start IS NOT NULL
            AND TRIM(l.trip_start) <> ''
            AND (
                l.along_side_vessel IS NULL
                OR TRIM(l.along_side_vessel) = ''
            )
        )

        OR

        -- CURRENTLY LOADING
        (
            l.commenced_loading IS NOT NULL
            AND TRIM(l.commenced_loading) <> ''
            AND (
                l.completed_loading IS NULL
                OR TRIM(l.completed_loading) = ''
            )
        )

        OR

        -- AT GULL ISLAND LOADED: cast off MV, not yet alongside berth
        (
            l.cast_off_mv IS NOT NULL
            AND TRIM(l.cast_off_mv) <> ''
            AND (
                l.along_side_berth IS NULL
                OR TRIM(l.along_side_berth) = ''
            )
        )

        OR

        -- WAITING AT JETTY: alongside berth, discharge not started
        (
            l.along_side_berth IS NOT NULL
            AND TRIM(l.along_side_berth) <> ''
            AND (
                l.commence_discharge_berth IS NULL
                OR TRIM(l.commence_discharge_berth) = ''
            )
        )

        OR

        -- UNDER DISCHARGE
        (
            l.commence_discharge_berth IS NOT NULL
            AND TRIM(l.commence_discharge_berth) <> ''
            AND (
                l.completed_discharge_berth IS NULL
                OR TRIM(l.completed_discharge_berth) = ''
            )
        )

        OR

        -- COMPLETED DISCHARGE
        (
            l.completed_discharge_berth IS NOT NULL
            AND TRIM(l.completed_discharge_berth) <> ''
        )
    )
    """
    },

'loaded_transit': {

    'date_col': 'l.cast_off_mv',

    'condition': """
        l.cast_off_mv IS NOT NULL
        AND TRIM(l.cast_off_mv) <> ''
        AND (
            l.along_side_berth IS NULL
            OR TRIM(l.along_side_berth) = ''
        )
    """
},

'waiting_discharge': {

    'date_col': 'l.along_side_berth',

    'condition': """
        l.along_side_berth IS NOT NULL
        AND TRIM(l.along_side_berth) <> ''
        AND (
            l.commence_discharge_berth IS NULL
            OR TRIM(l.commence_discharge_berth) = ''
        )
    """
},

    'currently_loading': {

        'date_col': 'l.commenced_loading',

        'condition': """
            commenced_loading IS NOT NULL
            AND TRIM(commenced_loading) <> ''
            AND (
                l.completed_loading IS NULL
                OR TRIM(l.completed_loading) = ''
            )
        """
    },

    'loaded_waiting': {

        'date_col': 'l.completed_loading',

        'condition': """
            completed_loading IS NOT NULL
            AND TRIM(completed_loading) <> ''
            AND (
                l.commence_discharge_berth IS NULL
                OR TRIM(l.commence_discharge_berth) = ''
            )
        """
    },

    'under_discharge': {

        'date_col': 'l.commence_discharge_berth',

        'condition': """
            l.commence_discharge_berth IS NOT NULL
            AND TRIM(l.commence_discharge_berth) <> ''
            AND (
                l.completed_discharge_berth IS NULL
                OR TRIM(l.completed_discharge_berth) = ''
            )
        """
    },

    'completed_discharge': {

        'date_col': 'l.completed_discharge_berth',

        'condition': """
            l.completed_discharge_berth IS NOT NULL
            AND TRIM(l.completed_discharge_berth) <> ''
        """
    }
}



def _fetch_list(from_date, to_date):

    conn = get_db()
    cur = get_cursor(conn)

    cur.execute(f"""

    WITH all_trip_data AS (

    SELECT

        l.id,

        l.barge_name AS original_barge_name,

        CONCAT(
            l.barge_name,
            '/',
            COALESCE(l.trip_number::text, '1')
        ) AS barge_name,

        l.trip_number,

        h.vessel_name AS mother_vessel_name,
        h.vcn_id AS vcn_id,

        l.cargo_name AS cargo_type,

        l.bpt_bfl AS mbpt_pla,

        COALESCE(l.discharge_quantity, 0) AS qty_mt,

        l.trip_start,

        l.anchored_gull_island,
        l.aweigh_gull_island,
        l.along_side_vessel,
        l.commenced_loading,
        l.completed_loading,
        l.cast_off_mv,
        l.amf_at_port,
        l.along_side_berth,
        l.commence_discharge_berth,
        l.completed_discharge_berth,
        l.anchored_gull_island_empty,
        l.aweigh_gull_island_empty,

        l.cast_off_berth AS cast_off_berth_nt,

        l.cast_off_port,

        l.port_crane AS unloaded_by,
        (
        SELECT ll.shift
        FROM lueu_lines ll
        WHERE
            ll.source_type = 'VCN'
            AND ll.source_id = h.vcn_id
            AND ll.is_deleted IS NOT TRUE
        ORDER BY ll.id DESC
        LIMIT 1
    ) AS shift

    FROM ldud_barge_lines l

    LEFT JOIN ldud_header h
        ON l.ldud_id = h.id

    WHERE
        l.barge_name IS NOT NULL
        AND TRIM(l.barge_name) <> ''
        AND l.trip_start IS NOT NULL
        AND TRIM(l.trip_start) <> ''

        
        AND l.trip_start::timestamp <= %s::timestamp

        -- ✅ completed_discharge_berth:
       
        AND (
            l.completed_discharge_berth IS NULL
            OR TRIM(l.completed_discharge_berth) = ''
            OR l.completed_discharge_berth::timestamp <= %s::timestamp
        )

    ),

    balance_data AS (

    SELECT

        *,

        COALESCE(qty_mt, 0)
        -
        COALESCE(discharge_done_qty, 0)
        AS qty_balance

    FROM (

        SELECT

            atd.*,

            COALESCE(
    (
        SELECT SUM(ll.quantity)
        FROM lueu_lines ll
        WHERE
            ll.barge_name = CONCAT(
                atd.original_barge_name,
                ' / ',
                COALESCE(atd.trip_number::text, '1')
            )
            AND ll.source_id = atd.vcn_id
            AND ll.source_type = 'VCN'
            AND (ll.is_deleted IS NOT TRUE)
            AND TO_DATE(ll.entry_date,'YYYY-MM-DD')
                <= %s::date
    ),
    0
    ) AS discharge_done_qty

        FROM all_trip_data atd

    ) z

    )

    SELECT *

    FROM balance_data

    ORDER BY
        trip_start,
        id

    """,
    # ✅ params: to_date (trip_start filter), to_date (completed_discharge filter), to_date (balance qty date)
    (to_date, to_date, to_date))

    raw_rows = [dict(r) for r in cur.fetchall()]
    conn.close()

    return raw_rows

def safe_dt(value):

    if not value:
        return None

    if isinstance(value, datetime):
        return value

    value = str(value).strip()

    formats = [
        "%d-%m-%Y %H:%M",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%dT%H:%M"
    ]

    for fmt in formats:

        try:
            return datetime.strptime(
                value,
                fmt
            )
        except:
            pass

    return None

def get_barge_status(row, from_dt, to_dt):

    trip_start          = safe_dt(row.get('trip_start'))
    completed_discharge = safe_dt(row.get('completed_discharge_berth'))
    discharge_start     = safe_dt(row.get('commence_discharge_berth'))
    loading_end         = safe_dt(row.get('completed_loading'))
    loading_start       = safe_dt(row.get('commenced_loading'))
    vessel_side         = safe_dt(row.get('along_side_vessel'))
    qty_balance         = float(row.get('qty_balance') or 0)

    if not trip_start:
        return None

  
    if trip_start > to_dt:
        return None

    
    if completed_discharge and completed_discharge < from_dt:
        return None

   
    if qty_balance <= 0.001 and completed_discharge and completed_discharge < from_dt:
        return None

    qty_mt = float(row.get('qty_mt') or 0)
    qty_balance = float(row.get('qty_balance') or 0)

    # COMPLETED DISCHARGE
    if qty_mt > 0 and qty_balance <= 0:
        return 'completed_discharge'

    # UNDER DISCHARGE
    if discharge_start:
        return 'under_discharge'

    # WAITING FOR DISCHARGE
    if (
        row.get('along_side_berth')
        and not row.get('commence_discharge_berth')
    ):
        return 'waiting_discharge'

    # CURRENTLY LOADING
    if loading_start and not loading_end:
        return 'currently_loading'

    # LOADED & TRANSIT
    if (
        row.get('cast_off_mv')
        and not row.get('along_side_berth')
    ):
        return 'loaded_transit'
    
    
    
    return None

def _fetch_barge_data(barge_line_id):

    conn = get_db()
    cur = get_cursor(conn)

    cur.execute(
        """
    SELECT
    l.*,

    h.vessel_name,

    l.port_crane AS unloaded_by,

    COALESCE(
        v.bl_quantity,
        0
    ) AS bl_quantity

    FROM ldud_barge_lines l

    LEFT JOIN ldud_header h
        ON l.ldud_id = h.id

    LEFT JOIN (

        SELECT
            vcn_id,
            cargo_name,
            SUM(bl_quantity) AS bl_quantity

        FROM vcn_cargo_declaration

        GROUP BY
            vcn_id,
            cargo_name

    ) v
        ON h.vcn_id = v.vcn_id
        AND l.cargo_name = v.cargo_name

    WHERE l.id = %s
        """,
        (barge_line_id,)
    )

    row = cur.fetchone()

    if not row:
        conn.close()
        return {}

    data = dict(row)

    # Mother vessel name
    ldud_id = data.get('ldud_id')

    if ldud_id:

        cur.execute(
            """
            SELECT vessel_name
            FROM ldud_header
            WHERE id = %s
            """,
            (ldud_id,)
        )

        hrow = cur.fetchone()

        data['mother_vessel_name'] = (
            hrow['vessel_name']
            if hrow else ''
        )

    else:

        data['mother_vessel_name'] = ''

    # Final values
    data['cargo_type'] = data.get('cargo_name', '')

    data['quantity_uom'] = 'MT'

    conn.close()

    return data

# ── Excel helpers ───────────────────────────────────────────────────────────

def _mbdr(ws, row, c1, c2, fill=XL_WHITE):
    """Apply perimeter thin borders + fill to every cell in a merged range."""
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
            pass


# ── Excel sheet writer ──────────────────────────────────────────────────────

def _write_barge_sheet(ws, data):
    """Write one barge line's report onto an existing worksheet."""

    barge_name         = data.get('barge_name', '')
    mother_vessel      = data.get('mother_vessel_name', '')
    cargo_type         = data.get('cargo_type', '')
    cargo_name         = data.get('cargo_name', '')
    mbpt_pla = data.get('bpt_bfl', '')
    bl_qty             = _safe_float(data.get('bl_quantity'))
    discharge_qty      = _safe_float(data.get('discharge_quantity'))
    qty_balance = max(
    bl_qty - discharge_qty,
    0
    )
    quantity_uom       = data.get('quantity_uom', 'MT')
    unloaded_by        = data.get('unloaded_by', '')

    trip_start              = _fmt_dt(data.get('trip_start'))
    anchored_gull_island = _fmt_dt(data.get('anchored_gull_island'))
    aweigh_gull_island   = _fmt_dt(data.get('aweigh_gull_island'))
    along_side_vessel       = _fmt_dt(data.get('along_side_vessel'))
    commenced_loading       = _fmt_dt(data.get('commenced_loading'))
    completed_loading       = _fmt_dt(data.get('completed_loading'))
    cast_off_mv             = _fmt_dt(data.get('cast_off_mv'))
    anchored_loaded = _fmt_dt(data.get('anchored_gull_island_empty'))
    aweigh_loaded   = _fmt_dt(data.get('aweigh_gull_island_empty'))
    amf_at_port             = _fmt_dt(data.get('amf_at_port'))
    along_side_berth        = _fmt_dt(data.get('along_side_berth'))
    commence_discharge_berth = _fmt_dt(data.get('commence_discharge_berth'))
    completed_discharge_berth = _fmt_dt(data.get('completed_discharge_berth'))
    cast_off_berth_nt       = _fmt_dt(data.get('cast_off_berth_nt'))
    cast_off_port           = _fmt_dt(data.get('cast_off_port'))

    # Column widths: A=22, B=22, C=22, D=22, E=22 (5 columns)
    NC = 5
    for i, w in enumerate([22, 22, 22, 22, 22], 1):
        ws.column_dimensions[get_column_letter(i)].width = w

    _nw = Alignment(horizontal='left', vertical='center', wrap_text=False)

    # ── Row 1: Title ──────────────────────────────────────────────────────────
    ws.row_dimensions[1].height = 30
    ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=NC)
    c = ws.cell(1, 1, 'MV BARGE DISCHARGE REPORT')
    c.font      = _font(bold=True, size=XL_TITLE_SZ, color='FFFFFF')
    c.fill      = _fill(XL_NAVY)
    c.alignment = _ctr
    c.border    = _bdr
    _mbdr(ws, 1, 1, NC, XL_NAVY)

    # ── Row 2: Blank ──────────────────────────────────────────────────────────
    ws.row_dimensions[2].height = 8
    ws.merge_cells(start_row=2, start_column=1, end_row=2, end_column=NC)
    c = ws.cell(2, 1, '')
    c.fill   = _fill(XL_WHITE)
    c.border = _bdr
    _mbdr(ws, 2, 1, NC)

    # ── Helper: 2-column label|value row ─────────────────────────────────────
    def _hdr2(row, label, value, height=16):
        ws.row_dimensions[row].height = height
        ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=2)
        c = ws.cell(row, 1, label)
        c.font      = _font(bold=True)
        c.fill      = _fill(XL_WHITE)
        c.alignment = _left
        c.border    = _bdr
        _mbdr(ws, row, 1, 2)
        ws.merge_cells(start_row=row, start_column=3, end_row=row, end_column=NC)
        c = ws.cell(row, 3, value if value is not None else '')
        c.font      = _font()
        c.fill      = _fill(XL_WHITE)
        c.alignment = _left
        c.border    = _bdr
        _mbdr(ws, row, 3, NC)

    # ── Rows 3-9: Header block ────────────────────────────────────────────────
    _hdr2(3,  'Barge / MBC Name',    barge_name)
    _hdr2(4,  'Mother Vessel Name',  mother_vessel)
    _hdr2(5,  'Cargo Type',          cargo_type)
    _hdr2(6,  'Cargo',               cargo_name)
    _hdr2(7,  'MBPT / PLA',          mbpt_pla)
    _hdr2(8,  f'Qty ({quantity_uom})',  bl_qty or '')
    _hdr2(9,  f'Qty Balance ({quantity_uom})', round(qty_balance, 2) if qty_balance else '')

    # ── Row 10: Blank separator ───────────────────────────────────────────────
    ws.row_dimensions[10].height = 8
    for ci in range(1, NC + 1):
        c = ws.cell(10, ci, '')
        c.fill   = _fill(XL_WHITE)
        c.border = _bdr

    # ── Row 11: Section header ────────────────────────────────────────────────
    ws.row_dimensions[11].height = 18
    ws.merge_cells(start_row=11, start_column=1, end_row=11, end_column=NC)
    c = ws.cell(11, 1, ' Barge Movement Timeline')
    c.font      = _font(bold=True)
    c.alignment = _left
    _mbdr(ws, 11, 1, NC, XL_GREY)

    # ── Row 12: Sub-header labels ─────────────────────────────────────────────
    ws.row_dimensions[12].height = 18
    headers_12 = ['EVENT', 'DATE / TIME', 'EVENT', 'DATE / TIME', '']
    for ci, h in enumerate(headers_12, 1):
        c = ws.cell(12, ci, h)
        c.font      = _font(bold=True)
        c.fill      = _fill(XL_GREY)
        c.alignment = _ctr
        c.border    = _bdr

    # ── Helper: side-by-side event row (col1|col2 | col3|col4 | col5 empty) ──
    def _evt2(row, lbl1, val1, lbl2='', val2='', height=15):
        ws.row_dimensions[row].height = height
        c = ws.cell(row, 1, lbl1)
        c.font      = _font(bold=True, size=XL_SMALL_SZ)
        c.fill      = _fill(XL_WHITE)
        c.alignment = _left
        c.border    = _bdr
        c = ws.cell(row, 2, val1)
        c.font      = _font(size=XL_SMALL_SZ)
        c.fill      = _fill(XL_WHITE)
        c.alignment = _ctr
        c.border    = _bdr
        c = ws.cell(row, 3, lbl2)
        c.font      = _font(bold=True, size=XL_SMALL_SZ)
        c.fill      = _fill(XL_WHITE)
        c.alignment = _left
        c.border    = _bdr
        c = ws.cell(row, 4, val2)
        c.font      = _font(size=XL_SMALL_SZ)
        c.fill      = _fill(XL_WHITE)
        c.alignment = _ctr
        c.border    = _bdr
        c = ws.cell(row, 5, '')
        c.fill      = _fill(XL_WHITE)
        c.border    = _bdr

    r = 13
    timeline_rows = [
        ('Trip Start',                  trip_start,               'Anchored Gull Island',          anchored_gull_island),
        ('Aweigh Gull Island',          aweigh_gull_island,        'Alongside Vessel (MV)',          along_side_vessel),
        ('Loading Start',               commenced_loading,        'Loading End',                    completed_loading),
        ('Cast Off MV',                 cast_off_mv,              'Anch. Gull Island (Loaded)',     anchored_loaded),
        ('Aweigh Gull Island (Loaded)', aweigh_loaded,            'AMF at Port',                    amf_at_port),
        ('Alongside Berth',             along_side_berth,         'Discharge Start (Berth)',         commence_discharge_berth),
        ('Discharge End (Berth)',        completed_discharge_berth,'Cast Off Berth NT',              cast_off_berth_nt),
        ('Cast Off Port',               cast_off_port,            'Unloaded By',                    unloaded_by),
    ]
    for lbl1, val1, lbl2, val2 in timeline_rows:
        _evt2(r, lbl1, val1, lbl2, val2)
        r += 1

    # ── Blank separator ───────────────────────────────────────────────────────
    ws.row_dimensions[r].height = 8
    for ci in range(1, NC + 1):
        c = ws.cell(r, ci, '')
        c.fill   = _fill(XL_WHITE)
        c.border = _bdr
    r += 1

    # ── Summary section header ────────────────────────────────────────────────
    ws.row_dimensions[r].height = 18
    ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=NC)
    c = ws.cell(r, 1, ' Quantity Summary')
    c.font      = _font(bold=True)
    c.alignment = _left
    _mbdr(ws, r, 1, NC, XL_CYAN)
    r += 1

    def _sumrow(row, label, value, height=15):
        ws.row_dimensions[row].height = height
        ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=3)
        c = ws.cell(row, 1, label)
        c.font      = _font(bold=True, size=XL_SMALL_SZ)
        c.alignment = _left
        _mbdr(ws, row, 1, 3)
        ws.merge_cells(start_row=row, start_column=4, end_row=row, end_column=NC)
        c = ws.cell(row, 4, value)
        c.font      = _font(size=XL_SMALL_SZ)
        c.alignment = _ctr
        _mbdr(ws, row, 4, NC)

    _sumrow(r, f'BL Quantity ({quantity_uom})',          bl_qty or 0)
    r += 1
    _sumrow(r, f'Discharge Quantity ({quantity_uom})',   round(discharge_qty, 2))
    r += 1
    _sumrow(r, f'Balance Quantity ({quantity_uom})',     round(qty_balance, 2))
    r += 1
    _sumrow(
    r,
    'TAT',
    _calc_tat(
        data.get('trip_start'),
        data.get('cast_off_port')
    )
    )
    r += 1

    # ── Lavender separator ────────────────────────────────────────────────────
    ws.row_dimensions[r].height = 10
    ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=NC)
    c = ws.cell(r, 1, '')
    _mbdr(ws, r, 1, NC, XL_LAVEND)
    r += 1

    # ── Remarks header ────────────────────────────────────────────────────────
    ws.row_dimensions[r].height = 18
    ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=NC)
    c = ws.cell(r, 1, 'Remarks')
    c.font      = _font(bold=True)
    c.alignment = _left
    _mbdr(ws, r, 1, NC, XL_LAVEND)
    r += 1

    for _ in range(3):
        ws.row_dimensions[r].height = 18
        ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=NC)
        c = ws.cell(r, 1, '')
        _mbdr(ws, r, 1, NC)
        r += 1


# ── Flat summary sheet (all barges) ─────────────────────────────────────────

from openpyxl.styles import Font, PatternFill, Border, Side, Alignment
from openpyxl.utils import get_column_letter

def _write_summary_sheet(ws, rows):

    from openpyxl.styles import Font, PatternFill, Border, Side, Alignment
    from openpyxl.utils import get_column_letter

    thin = Side(style='thin', color='D0D7E2')

    border = Border(
        left=thin,
        right=thin,
        top=thin,
        bottom=thin
    )

    header_fill = PatternFill(
        start_color='E6E6E6',
        end_color='E6E6E6',
        fill_type='solid'
    )

    header_font = Font(
        bold=True,
        size=10,
        color='000000'
    )

    body_font = Font(
        size=10,
        color='333333'
    )

    center = Alignment(
        horizontal='center',
        vertical='center',
        wrap_text=True
    )

    left = Alignment(
        horizontal='left',
        vertical='center'
    )

    headers = [
        'Sr#',
        'Barge / MBC Name',
        'Mother Vessel Name',
        'Cargo Type',
        'MBPT / PLA',
        'Qty (MT)',
        'Qty Balance (MT)',
        'Trip Start',
        'Anch. Gull Island',
        'Aweigh Gull Island',
        'Alongside Vessel (MV)',
        'Loading Start',
        'Loading End',
        'Cast Off MV',
        'Anch. Gull Island (Loaded)',
        'Aweigh Gull Island (Loaded)',
        'AMF at Port',
        'Alongside Berth',
        'Discharge Start (Berth)',
        'Discharge End (Berth)',
        'Cast Off Berth NT',
        'Cast Off Port',
        'Unloaded By',
        'TAT'
    ]

    widths = [
        8, 28, 28, 18, 12,
        14, 16, 20, 20, 20,
        22, 18, 18, 18, 24,
        24, 18, 18, 24, 24,
        20, 18, 18, 10
    ]

    # column widths
    for i, w in enumerate(widths, 1):
        ws.column_dimensions[get_column_letter(i)].width = w

    # header row
    ws.row_dimensions[1].height = 28

    for col_num, text in enumerate(headers, 1):

        cell = ws.cell(row=1, column=col_num)

        cell.value = text
        cell.fill = header_fill
        cell.font = header_font
        cell.border = border
        cell.alignment = center

    # data rows
    for idx, row in enumerate(rows, start=2):

        values = [
            idx - 1,

            row.get('barge_name', ''),
            row.get('mother_vessel_name', ''),
            row.get('cargo_type', ''),
            row.get('mbpt_pla', ''),

            row.get('qty_mt', ''),
            row.get('qty_balance', ''),

            _fmt_dt(row.get('trip_start')),
            _fmt_dt(row.get('anchored_gull_island')),
            _fmt_dt(row.get('aweigh_gull_island')),

            _fmt_dt(row.get('along_side_vessel')),

            _fmt_dt(row.get('commenced_loading')),
            _fmt_dt(row.get('completed_loading')),

            _fmt_dt(row.get('cast_off_mv')),

            _fmt_dt(row.get('anchored_gull_island_empty')),
            _fmt_dt(row.get('aweigh_gull_island_empty')),

            _fmt_dt(row.get('amf_at_port')),
            _fmt_dt(row.get('along_side_berth')),

            _fmt_dt(row.get('commence_discharge_berth')),

            _fmt_dt(row.get('completed_discharge_berth')),

            _fmt_dt(row.get('cast_off_berth_nt')),
            _fmt_dt(row.get('cast_off_port')),

            row.get('unloaded_by', ''),

            _calc_tat(
                row.get('trip_start'),
                row.get('cast_off_port')
            )
        ]
        
        row_fill = None

        if row.get('completed_discharge_berth'):
            row_fill = PatternFill(
                start_color='C6EFCE',
                end_color='C6EFCE',
                fill_type='solid'
            )

        elif row.get('commence_discharge_berth'):
            row_fill = PatternFill(
                start_color='FFE699',
                end_color='FFE699',
                fill_type='solid'
            )

        elif row.get('completed_loading'):
            row_fill = PatternFill(
                start_color='BDD7EE',
                end_color='BDD7EE',
                fill_type='solid'
            )

        elif row.get('commenced_loading'):
            row_fill = PatternFill(
                start_color='D9D2E9',
                end_color='D9D2E9',
                fill_type='solid'
            )

        elif row.get('trip_start'):
            row_fill = PatternFill(
                start_color='FFF2CC',
                end_color='FFF2CC',
                fill_type='solid'
            )
        
        for col_num, value in enumerate(values, 1):

            cell = ws.cell(row=idx, column=col_num)

            cell.value = value
            cell.font = body_font
            cell.border = border
            if row_fill:
                cell.fill = row_fill
            # Completed Discharge = Green
        

            if col_num in [1, 6, 7, 24]:
                cell.alignment = center
            else:
                cell.alignment = left

      # Freeze header row + first 3 data columns (Sr#, Barge Name, Mother Vessel Name, Cargo Type, MBPT/PLA)
            ws.freeze_panes = 'F2'   
    # filter
    ws.auto_filter.ref = ws.dimensions

# ── Excel builder ────────────────────────────────────────────────────────────



def _build_excel(data):

    from openpyxl import Workbook

    wb = Workbook()

    ws = wb.active

    barge_name = data.get('barge_name', 'Barge')

    safe_title = re.sub(r'[\\/*?\[\]:]', '_', barge_name)[:31]

    ws.title = safe_title or 'Report'

    # IMPORTANT
    _write_summary_sheet(ws, [data])

    buf = io.BytesIO()

    wb.save(buf)

    buf.seek(0)

    return buf


def _build_all_excel(list_rows, barge_data_list):

    from openpyxl import Workbook

    wb = Workbook()

    # =========================
    # SHEET 1 : Barge_MBC
    # =========================

    ws1 = wb.active

    ws1.title = 'Barge_MBC'

    _write_summary_sheet(ws1, list_rows)

    # =========================
    # SHEET 2 : Discharge
    # =========================

    ws2 = wb.create_sheet('Discharge')

    _write_discharge_sheet(ws2, list_rows)

    buf = io.BytesIO()

    wb.save(buf)

    buf.seek(0)

    return buf




def _write_discharge_sheet(ws, rows):

    from openpyxl.styles import Font, PatternFill, Border, Side, Alignment
    from openpyxl.utils import get_column_letter

    thin = Side(style='thin', color='000000')

    border = Border(
        left=thin,
        right=thin,
        top=thin,
        bottom=thin
    )

    center = Alignment(
        horizontal='center',
        vertical='center',
        wrap_text=True
    )

    green_fill = PatternFill(
        start_color='C6EFCE',
        end_color='C6EFCE',
        fill_type='solid'
    )

    yellow_fill = PatternFill(
        start_color='FFE699',
        end_color='FFE699',
        fill_type='solid'
    )

    blue_fill = PatternFill(
        start_color='BDD7EE',
        end_color='BDD7EE',
        fill_type='solid'
    )

    grey_fill = PatternFill(
        start_color='D9D9D9',
        end_color='D9D9D9',
        fill_type='solid'
    )

    bold = Font(bold=True)

    equipments = [
    'Barge Unloader 1',
    'Barge Unloader 2',
    'SANY 285-Exavator',
    'Sennebogen J1',
    'Sennebogen J5',
    'BUL-01',
    'BUL-02',
    'BUL-03',
    'BUL-04',
    'BUL-05'
]

    # =====================================
    # COLUMN WIDTHS
    # =====================================

    for col in range(1, 40):
        ws.column_dimensions[get_column_letter(col)].width = 10

    ws.column_dimensions['A'].width = 15

    # =====================================
    # TOP HEADERS
    # =====================================

    ws['A1'] = 'Eq Name'
    ws['A2'] = 'Details'

    ws['A1'].font = bold
    ws['A2'].font = bold

    ws['A1'].alignment = center
    ws['A2'].alignment = center

    ws['A1'].border = border
    ws['A2'].border = border

    current_col = 2

    for eq in equipments:

        ws.merge_cells(
            start_row=1,
            start_column=current_col,
            end_row=1,
            end_column=current_col + 3
        )

        cell = ws.cell(1, current_col)

        cell.value = eq
        cell.fill = green_fill
        cell.font = Font(bold=True, color='006100')
        cell.alignment = center
        cell.border = border

        sub_headers = ['Barge Name', 'Qty', 'Cargo', 'MV Name']

        for i, txt in enumerate(sub_headers):

            c = ws.cell(2, current_col + i)

            c.value = txt
            c.fill = grey_fill
            c.font = bold
            c.alignment = center
            c.border = border

        current_col += 4

    # =====================================
    # TOTAL DISCHARGE
    # =====================================

    ws.merge_cells(
        start_row=1,
        start_column=current_col,
        end_row=2,
        end_column=current_col
    )

    c = ws.cell(1, current_col)

    c.value = 'Total Discharge'
    # FREEZE HEADER
    ws.freeze_panes = 'B3'
    c.fill = blue_fill
    c.font = bold
    c.alignment = center
    c.border = border

    # =====================================
    # SHIFTS
    # =====================================

    start_row = 3

    shifts = ['A Shift', 'B Shift', 'C Shift']

    for shift in shifts:

        # shift rows
        ws.merge_cells(
            start_row=start_row,
            start_column=1,
            end_row=start_row + 4,
            end_column=1
        )

        c = ws.cell(start_row, 1)

        c.value = shift
        c.alignment = center
        c.font = bold
        c.border = border

        # blank cells
        for r in range(start_row, start_row + 5):

            for col in range(2, current_col + 1):

                cell = ws.cell(r, col)

                cell.border = border

        # total row
        total_row = start_row + 5

        tc = ws.cell(total_row, 1)

        tc.value = f'{shift} Total'

        tc.fill = yellow_fill
        tc.font = bold
        tc.border = border

        for col in range(2, current_col + 1):

            cell = ws.cell(total_row, col)

            cell.fill = yellow_fill
            cell.border = border

            # qty columns
            if (col - 3) % 4 == 0:
                cell.value = 0
                cell.alignment = center

        ws.cell(total_row, current_col).value = 0

        start_row += 7

     # =====================================
    # FILL SHIFT DATA
    # =====================================

    shift_positions = {
        'A Shift': 3,
        'B Shift': 10,
        'C Shift': 17
    }

    equipment_cols = {

    'Barge Unloader 1': 2,
    'Barge Unloader 2': 6,

    'SANY 285-Exavator': 10,

    'Sennebogen J1': 14,
    'Sennebogen J5': 18,

    'BUL-01': 22,
    'BUL-02': 26,
    'BUL-03': 30,
    'BUL-04': 34,
    'BUL-05': 38
    }

    shift_data = {
        'A Shift': [],
        'B Shift': [],
        'C Shift': []
    }

    for row in rows:

        shift = (row.get('shift') or '').strip()

        if shift in ['A Shift', 'A']:
            shift_data['A Shift'].append(row)

        elif shift in ['B Shift', 'B']:
            shift_data['B Shift'].append(row)

        elif shift in ['C Shift', 'C']:
            shift_data['C Shift'].append(row)

    # =========================
    # WRITE DATA TO EXCEL
    # =========================

    for shift_name, items in shift_data.items():

        row_pointer = shift_positions[shift_name]

        equipment_row_count = defaultdict(int)

        total_discharge = 0

        for item in items:

            equipments = item.get('unloaded_by', '')

            if not equipments:
                continue

            equipment_list = [
                e.strip()
                for e in equipments.split(',')
                if e.strip()
            ]

            qty = _safe_float(
                item.get('qty_mt')
            )
            print(
                "SHIFT=", item.get('shift'),
                "BARGE=", item.get('barge_name'),
                "QTY=", item.get('qty_mt'),
                "EQ=", item.get('unloaded_by')
            )
            for equipment in equipment_list:

                if equipment not in equipment_cols:
                    continue

                base_col = equipment_cols[equipment]

                current_row = (
                    row_pointer +
                    equipment_row_count[equipment]
                )

                if current_row > row_pointer + 4:
                    continue

                ws.cell(
                    current_row,
                    base_col
                ).value = item.get('barge_name', '')

                ws.cell(
                    current_row,
                    base_col + 1
                ).value = qty

                ws.cell(
                    current_row,
                    base_col + 2
                ).value = item.get('cargo_type', '')

                ws.cell(
                    current_row,
                    base_col + 3
                ).value = item.get(
                    'mother_vessel_name',
                    ''
                )

                for c in range(base_col, base_col + 4):

                    ws.cell(
                        current_row,
                        c
                    ).alignment = center

                    ws.cell(
                        current_row,
                        c
                    ).border = border

                equipment_row_count[equipment] += 1

                total_discharge += qty

        total_row = row_pointer + 5

        shift_total = 0

        for col in range(3, current_col, 4):

            col_total = 0

            for r in range(row_pointer, row_pointer + 5):

                val = ws.cell(r, col).value

                try:
                    col_total += float(val or 0)
                except:
                    pass

            ws.cell(
                total_row,
                col
            ).value = round(col_total, 2)

            shift_total += col_total

        ws.cell(
            total_row,
            current_col
        ).value = round(shift_total, 2)

        # =====================================
        # ALL SHIFT TOTAL
        # =====================================

        final_row = start_row

        fc = ws.cell(final_row, 1)

        fc.value = 'All Shift Total'

        fc.fill = blue_fill
        fc.font = bold
        fc.border = border

        for col in range(2, current_col + 1):

            cell = ws.cell(final_row, col)

            cell.fill = blue_fill
            cell.border = border

            if (col - 3) % 4 == 0:
                cell.value = 0
                cell.alignment = center

        ws.cell(final_row, current_col).value = 0

# ── Routes ───────────────────────────────────────────────────────────────────

@bp.route('/module/RP01/mv-barge-report/')
@login_required
def mv_barge_report_index():
    return render_template('daily_barge_report/mv_barge_report.html',
                           username=session.get('username'))


@bp.route('/api/module/RP01/mv-barge-report/data')
@login_required
def mv_barge_report_data():

    from_datetime = request.args.get('from_date', '')
    to_datetime = request.args.get('to_date', '')
    column_filter = request.args.get('column_filter', '')
    status_filter = request.args.get('status_filter', 'all')

    try:
        if from_datetime:
            from_dt = datetime.fromisoformat(from_datetime)
        else:
            from_dt = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)

        if to_datetime:
            to_dt = datetime.fromisoformat(to_datetime)
        else:
            to_dt = datetime.now().replace(hour=23, minute=59, second=59, microsecond=999999)
    except Exception as e:
        print(f"Date parsing error: {e}")
        return jsonify({'error': 'Invalid date format'}), 400

    
    rows = _fetch_list(
        from_dt.strftime('%Y-%m-%d %H:%M:%S'),
        to_dt.strftime('%Y-%m-%d %H:%M:%S')
    )

    filtered_rows = []

    for row in rows:

        status = get_barge_status(
            row,
            from_dt,
            to_dt
        )

        row['current_status'] = status

        if status is None:
            continue

        if status_filter == 'all':
            filtered_rows.append(row)
        elif status == status_filter:
            filtered_rows.append(row)

    rows = filtered_rows
    status_order = {
    'currently_loading': 1,
    'loaded_transit': 2,
    'waiting_discharge': 3,
    'under_discharge': 4,
    'completed_discharge': 5
    }

    rows.sort(
        key=lambda x: (
            status_order.get(x.get('current_status'), 999),
            x.get('barge_name', '')
        )
    )

    if column_filter:
        column_field_map = {
            'trip_start': 'trip_start',
            'anchored_gull_island': 'anchored_gull_island',
            'aweigh_gull_island': 'aweigh_gull_island',
            'along_side_vessel': 'along_side_vessel',
            'commenced_loading': 'commenced_loading',
            'completed_loading': 'completed_loading',
            'cast_off_mv': 'cast_off_mv',
            'anchored_gull_island_empty': 'anchored_gull_island_empty',
            'aweigh_gull_island_empty': 'aweigh_gull_island_empty',
            'amf_at_port': 'amf_at_port',
            'along_side_berth': 'along_side_berth',
            'commence_discharge_berth': 'commence_discharge_berth',
            'completed_discharge_berth': 'completed_discharge_berth',
            'cast_off_berth_nt': 'cast_off_berth_nt',
            'cast_off_port': 'cast_off_port'
        }

        db_field = column_field_map.get(column_filter)
        if db_field:
            rows = [r for r in rows if r.get(db_field)]
            
            

    for row in rows:
        
        row['tat'] = _calc_tat(
            row.get('trip_start'),
            row.get('cast_off_port')
        )

        date_fields = [
            'trip_start',
            'anchored_gull_island',
            'aweigh_gull_island',
            'along_side_vessel',
            'commenced_loading',
            'completed_loading',
            'cast_off_mv',
            'amf_at_port',
            'along_side_berth',
            'commence_discharge_berth',
            'completed_discharge_berth',
            'cast_off_berth_nt',
            'cast_off_port',
            'anchored_gull_island_empty',
            'aweigh_gull_island_empty'
        ]

        for fld in date_fields:
            row[fld] = _fmt_dt(row.get(fld))

    return jsonify(rows)

def get_mbc_status(row):

    # Currently Loading: loading_commenced exists, loading_completed does NOT
    if row.get('commenced_loading') and not row.get('completed_loading'):
        return 'currently_loading'

    # Loaded & Transit: loading_completed exists, arrival_gull_island does NOT
    if row.get('completed_loading') and not row.get('arrival_gull_island'):
        return 'loaded_transit'

    # Waiting at Gull: arrival_gull_island exists, departure_gull_island does NOT
    if row.get('arrival_gull_island') and not row.get('departure_gull_island'):
        return 'waiting_gull'

    # On the Way to Dharamtar: departure_gull_island exists, mbc_arrival_port does NOT
    if row.get('departure_gull_island') and not row.get('mbc_arrival_port'):
        return 'on_way_dharamtar'

    # Waiting for Discharge: mbc_arrival_port exists, unloading_commenced does NOT
    if row.get('mbc_arrival_port') and not row.get('unloading_commenced'):
        return 'waiting_discharge'

    # Under Discharge: unloading_commenced exists, unloading_completed does NOT
    if row.get('unloading_commenced') and not row.get('unloading_completed'):
        return 'under_discharge'

    # Waiting for Cast Off: unloading_completed exists, mbc_cast_off does NOT
    if row.get('unloading_completed') and not row.get('mbc_cast_off'):
        return 'waiting_castoff'

    # ✅ ADD THIS — Completed: mbc_cast_off exists (voyage fully done)
    if row.get('mbc_cast_off'):
        return 'completed_discharge'

    # ✅ ADD THIS — No data at all yet
    return None

@bp.route('/api/module/RP01/mv-barge-report/download-all')
@login_required
def mv_barge_report_download_all():

    from_datetime = request.args.get('from_date', '')
    to_datetime = request.args.get('to_date', '')
    column_filter = request.args.get('column_filter', '')
    status_filter = request.args.get('status_filter', 'all')

    try:
        if from_datetime:
            from_dt = datetime.fromisoformat(from_datetime)
        else:
            from_dt = datetime.now().replace(day=1, hour=0, minute=0, second=0, microsecond=0)

        if to_datetime:
            to_dt = datetime.fromisoformat(to_datetime)
        else:
            to_dt = datetime.now().replace(hour=23, minute=59, second=59, microsecond=999999)
    except Exception as e:
        print(f"Date parsing error: {e}")
        return jsonify({'error': 'Invalid date format'}), 400

    
    list_rows = _fetch_list(
        from_dt.strftime('%Y-%m-%d %H:%M:%S'),
        to_dt.strftime('%Y-%m-%d %H:%M:%S')
    )

    filtered_rows = []
    for row in list_rows:
        status = get_barge_status(row, from_dt, to_dt)
        if status is None:
            continue
        row['current_status'] = status
        if status_filter == 'all' or status == status_filter:
            filtered_rows.append(row)
    list_rows = filtered_rows

    if column_filter:
        column_field_map = {
            'trip_start': 'trip_start',
            'anchored_gull_island': 'anchored_gull_island',
            'aweigh_gull_island': 'aweigh_gull_island',
            'along_side_vessel': 'along_side_vessel',
            'commenced_loading': 'commenced_loading',
            'completed_loading': 'completed_loading',
            'cast_off_mv': 'cast_off_mv',
            'anchored_gull_island_empty': 'anchored_gull_island_empty',
            'aweigh_gull_island_empty': 'aweigh_gull_island_empty',
            'amf_at_port': 'amf_at_port',
            'along_side_berth': 'along_side_berth',
            'commence_discharge_berth': 'commence_discharge_berth',
            'completed_discharge_berth': 'completed_discharge_berth',
            'cast_off_berth_nt': 'cast_off_berth_nt',
            'cast_off_port': 'cast_off_port'
        }

        db_field = column_field_map.get(column_filter)
        if db_field:
            list_rows = [r for r in list_rows if r.get(db_field)]

    if not list_rows:
        return Response('No records in selected range', status=404)

    barge_data_list = [
        _fetch_barge_data(r['id'])
        for r in list_rows
    ]

    buf = _build_all_excel(list_rows, barge_data_list)

    fname = f'MVBargeReport_{from_dt.strftime("%Y-%m-%d")}_to_{to_dt.strftime("%Y-%m-%d")}.xlsx'

    return Response(
        buf.getvalue(),
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        headers={
            'Content-Disposition': f'attachment; filename="{fname}"'
        },
    )
@bp.route('/api/module/RP01/mv-barge-report/<int:barge_line_id>/download')
@login_required
def mv_barge_report_download(barge_line_id):
    data = _fetch_barge_data(barge_line_id)
    if not data:
        return jsonify({'error': 'Record not found'}), 404
    buf   = _build_excel(data)
    barge = re.sub(r'[^A-Za-z0-9_\-]', '_', data.get('barge_name', 'barge'))
    fname = f'MVBargeReport_{barge}.xlsx'
    return Response(
        buf.getvalue(),
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        headers={'Content-Disposition': f'attachment; filename="{fname}"'},
    )
    
@bp.route('/api/module/RP01/mv-barge-report/mbc-data')
@login_required
def get_mbc_data():

    from_date     = request.args.get('from_date')
    to_date       = request.args.get('to_date')
    column_filter = request.args.get('column_filter')
    status_filter = request.args.get('status_filter', 'all')

    try:
        from_dt = datetime.fromisoformat(from_date) if from_date else \
                  datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        to_dt   = datetime.fromisoformat(to_date) if to_date else \
                  datetime.now().replace(hour=23, minute=59, second=59, microsecond=999999)
    except Exception as e:
        return jsonify({'error': 'Invalid date format'}), 400

    conn = get_db()
    cur  = get_cursor(conn)

    column_map = {
        'trip_start':                 'lp.arrived_load_port',
        'along_side_vessel':          'lp.alongside_berth',
        'commenced_loading':          'lp.loading_commenced',
        'completed_loading':          'lp.loading_completed',
        'cast_off_mv':                'lp.cast_off_load_port',
        'anchored_gull_island_empty': 'dp.arrival_gull_island',
        'aweigh_gull_island_empty':   'dp.departure_gull_island',
        'amf_at_port':                'dp.vessel_arrival_port',
        'along_side_berth':           'dp.vessel_unloading_berth',
        'commence_discharge_berth':   'dp.unloading_commenced',
        'completed_discharge_berth':  'dp.unloading_completed',
        'cast_off_port':              'dp.vessel_cast_off'
    }

    # ── Fetch ALL rows — no date casting in SQL to avoid corrupt data crash ──
    query = """
        SELECT DISTINCT ON (h.id)
            h.id                          AS mbc_id,
            h.mbc_name                    AS mbc_name,
            h.cargo_name                  AS cargo_type,
            COALESCE(h.bl_quantity, 0)    AS qty_mt,
            (
                COALESCE(h.bl_quantity, 0)
                -
                COALESCE(
                    (SELECT SUM(ll.quantity) FROM lueu_lines ll
                     WHERE ll.source_type = 'MBC'
                       AND ll.source_id   = h.id
                       AND (ll.is_deleted IS NOT TRUE)),
                    0
                )
            ) AS qty_balance,
            lp.arrived_load_port          AS trip_start,
            lp.alongside_berth            AS along_side_vessel,
            lp.loading_commenced          AS commenced_loading,
            lp.loading_completed          AS completed_loading,
            lp.cast_off_load_port         AS cast_off_mv,
            dp.arrival_gull_island        AS arrival_gull_island,
            dp.departure_gull_island      AS departure_gull_island,
            dp.vessel_arrival_port        AS mbc_arrival_port,
            dp.vessel_all_made_fast       AS mbc_amf_unloading_berth,
            dp.unloading_commenced        AS unloading_commenced,
            dp.cleaning_commenced         AS cleaning_commenced,
            dp.unloading_completed        AS unloading_completed,
            dp.vessel_cast_off            AS mbc_cast_off,
            dp.sailed_out_load_port       AS sailed_out_load_port,
            dp.vessel_unloaded_by         AS vessel_unloaded_by,
            dp.vessel_unloading_berth     AS unloaded_berth,
            dp.cleaning_completed         AS cleaning_completed
        FROM mbc_header h
        LEFT JOIN mbc_load_port_lines lp    ON lp.mbc_id = h.id
        LEFT JOIN mbc_discharge_port_lines dp ON dp.mbc_id = h.id
        WHERE h.mbc_name IS NOT NULL
        ORDER BY h.id
    """

    cur.execute(query)
    rows = [dict(r) for r in cur.fetchall()]
    cur.close()
    conn.close()

    # ── Apply date filter in Python (safe — no SQL casting of corrupt values) ─
    filtered_rows = []

    for row in rows:

        trip_start          = safe_dt(row.get('trip_start'))
        mbc_cast_off        = safe_dt(row.get('mbc_cast_off'))
        unloading_completed = safe_dt(row.get('unloading_completed'))

        # Must have a trip start
        if not trip_start:
            continue

        # Trip must have started on or before to_dt
        if trip_start > to_dt:
            continue

        # ── Mirror barge logic exactly ──────────────────────────────────────

        # If unloading is fully completed AND completed before from_dt → exclude
        # (same as barge: completed_discharge < from_dt → skip)
        if unloading_completed and unloading_completed < from_dt:
            continue

        # If mbc_cast_off exists AND is before from_dt → exclude
        # (extra safety for fully-done voyages with corrupt or old cast_off)
        if mbc_cast_off and mbc_cast_off < from_dt:
            continue

        # ── Optional column filter ──────────────────────────────────────────
        if column_filter:
            col_field_map = {
                'trip_start':                 'trip_start',
                'along_side_vessel':          'along_side_vessel',
                'commenced_loading':          'commenced_loading',
                'completed_loading':          'completed_loading',
                'cast_off_mv':                'cast_off_mv',
                'anchored_gull_island_empty': 'arrival_gull_island',
                'aweigh_gull_island_empty':   'departure_gull_island',
                'amf_at_port':                'mbc_arrival_port',
                'along_side_berth':           'unloaded_berth',
                'commence_discharge_berth':   'unloading_commenced',
                'completed_discharge_berth':  'unloading_completed',
                'cast_off_port':              'mbc_cast_off',
            }
            py_field = col_field_map.get(column_filter)
            if py_field:
                col_dt = safe_dt(row.get(py_field))
                if not col_dt or not (from_dt <= col_dt <= to_dt):
                    continue

        # ── Status determination ────────────────────────────────────────────
        status = get_mbc_status(row)
        row['current_status'] = status

        if status is None:
            continue

        if status_filter == 'all' or status == status_filter:
            filtered_rows.append(row)

        status_order = {
        'currently_loading': 1,
        'loaded_transit': 2,
        'waiting_gull': 3,
        'on_way_dharamtar': 4,
        'waiting_discharge': 5,
        'under_discharge': 6,
        'waiting_castoff': 7,
        'completed_discharge': 8
    }

    filtered_rows.sort(
        key=lambda x: status_order.get(
            x.get('current_status'),
            999
        )
    )

    return jsonify(filtered_rows)

@bp.route('/api/module/RP01/mv-barge-report/shift-data')
@login_required
def get_shift_data():

    from_date = request.args.get('from_date')
    to_date   = request.args.get('to_date')

    try:
        from_dt = datetime.fromisoformat(from_date) if from_date else \
                  datetime.now().replace(hour=6, minute=0, second=0, microsecond=0)
        to_dt   = datetime.fromisoformat(to_date) if to_date else \
                  datetime.now().replace(hour=23, minute=59, second=59, microsecond=999999)
    except Exception:
        return jsonify({'error': 'Invalid date format'}), 400

    conn = get_db()
    cur  = get_cursor(conn)

    # ── Fetch lueu_lines data within date range ──────────────────────────────
    cur.execute("""
        SELECT
            ll.shift,
            ll.barge_name,
            ll.equipment_name,
            ll.cargo_name,
            ll.quantity,
            ll.entry_date,
            ll.from_time,
            ll.to_time,
            ll.source_type,
            ll.source_id,

            -- Get mother vessel name for VCN source
            CASE
                WHEN ll.source_type = 'VCN' THEN
                    (SELECT h.vessel_name FROM ldud_header h WHERE h.vcn_id = ll.source_id LIMIT 1)
                WHEN ll.source_type = 'MBC' THEN
                    (SELECT h.mbc_name FROM mbc_header h WHERE h.id = ll.source_id LIMIT 1)
                ELSE ''
            END AS source_name

        FROM lueu_lines ll

        WHERE
            ll.is_deleted IS NOT TRUE
            AND ll.quantity > 0
            AND ll.entry_date IS NOT NULL

            -- Filter by entry_date within selected range
            AND TO_DATE(ll.entry_date, 'YYYY-MM-DD') 
                BETWEEN %s::date AND %s::date

        ORDER BY
            ll.shift,
            ll.equipment_name,
            ll.barge_name
    """, (
        from_dt.strftime('%Y-%m-%d'),
        to_dt.strftime('%Y-%m-%d')
    ))

    rows = [dict(r) for r in cur.fetchall()]
    cur.close()
    conn.close()

    # ── Organise by shift → equipment → list of items ────────────────────────
    shift_map = {
        'A Shift': {},
        'B Shift': {},
        'C Shift': {}
    }

    # Also track shift totals per equipment
    for row in rows:
        raw_shift = (row.get('shift') or '').strip()

        # Normalise shift name
        if raw_shift in ('A', 'A Shift'):
            shift_key = 'A Shift'
        elif raw_shift in ('B', 'B Shift'):
            shift_key = 'B Shift'
        elif raw_shift in ('C', 'C Shift'):
            shift_key = 'C Shift'
        else:
            continue  # skip unknown shifts

        eq = (row.get('equipment_name') or '').strip()
        if not eq:
            continue

        if eq not in shift_map[shift_key]:
            shift_map[shift_key][eq] = []

        shift_map[shift_key][eq].append({
            'barge_name':   row.get('barge_name', ''),
            'cargo_name':   row.get('cargo_name', ''),
            'quantity':     float(row.get('quantity') or 0),
            'source_name':  row.get('source_name', ''),
            'source_type':  row.get('source_type', ''),
            'from_time':    row.get('from_time', ''),
            'to_time':      row.get('to_time', ''),
            'entry_date':   row.get('entry_date', ''),
        })

    return jsonify(shift_map)