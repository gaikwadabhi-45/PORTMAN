from flask import render_template, session, redirect, url_for
from functools import wraps
from datetime import datetime
from database import get_db, get_cursor
from .. import bp


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated


# ---------------------------------------------------------------------------
# Timestamp helper
# ---------------------------------------------------------------------------

def _parse(ts):
    if not ts:
        return None
    try:
        return datetime.fromisoformat(str(ts))
    except Exception:
        return None


def fmt_dt(ts):
    """'2026-02-08 14:40:00'  →  'ON 08.02.2026 AT 1440 HRS.'"""
    dt = _parse(ts)
    if not dt:
        return ''
    return f"ON {dt.strftime('%d.%m.%Y')} AT {dt.strftime('%H%M')} HRS."


def fmt_qty(value):
    if value is None or value == '':
        return ''
    try:
        text = f'{float(value):,.3f}'
    except (TypeError, ValueError):
        return str(value)
    return text[:-4] if text.endswith('.000') else text


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

def _fetch_mbc_list():
    """Return all MBC header records sorted by operation_type, then doc_date DESC."""
    conn = get_db()
    cur  = get_cursor(conn)
    cur.execute("""
        SELECT id, doc_num, mbc_name, operation_type, cargo_name,
               bl_quantity, quantity_uom, doc_status, doc_date
        FROM mbc_header
        ORDER BY operation_type, doc_date DESC NULLS LAST
    """)
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    for r in rows:
        d = r.get('doc_date')
        if d:
            try:
                # May be a date/datetime object or a string like 'YYYY-MM-DD'
                if hasattr(d, 'strftime'):
                    r['doc_date_display'] = d.strftime('%d.%m.%Y')
                else:
                    dt = datetime.fromisoformat(str(d)[:10])
                    r['doc_date_display'] = dt.strftime('%d.%m.%Y')
            except Exception:
                r['doc_date_display'] = str(d)
        else:
            r['doc_date_display'] = '—'
        r['bl_quantity_display'] = fmt_qty(r.get('bl_quantity')) if r.get('bl_quantity') else ''
    return rows


def _fetch_mbc_sof_data(mbc_id):
    """Fetch header + relevant lines for one MBC.

    Returns (header, load_port, discharge_port, export_load_port).
    Unused dicts will be empty {}.
    """
    conn = get_db()
    cur  = get_cursor(conn)

    cur.execute("SELECT * FROM mbc_header WHERE id = %s", (mbc_id,))
    row = cur.fetchone()
    if not row:
        conn.close()
        return None, {}, {}, {}
    header   = dict(row)
    op_type  = (header.get('operation_type') or '').lower()

    load_port      = {}
    discharge_port = {}
    export_load    = {}

    if op_type == 'export':
        cur.execute(
            "SELECT * FROM mbc_export_load_port_lines WHERE mbc_id = %s",
            (mbc_id,)
        )
        r = cur.fetchone()
        if r:
            export_load = dict(r)
    else:
        cur.execute(
            "SELECT * FROM mbc_load_port_lines WHERE mbc_id = %s",
            (mbc_id,)
        )
        r = cur.fetchone()
        if r:
            load_port = dict(r)

        cur.execute(
            "SELECT * FROM mbc_discharge_port_lines WHERE mbc_id = %s",
            (mbc_id,)
        )
        r = cur.fetchone()
        if r:
            discharge_port = dict(r)

    conn.close()
    return header, load_port, discharge_port, export_load


# ---------------------------------------------------------------------------
# Row builders
# ---------------------------------------------------------------------------

def _build_import_sections(load_port, discharge_port):
    """Return list of section dicts for Import SOF.
    Each section: {'title': str, 'rows': [{label, value}, ...]}
    Only non-empty values are included.
    """
    sections = []

    # ── Section 1: Load Port Details ────────────────────────────────────────
    lp_rows = []
    for label, val in [
        ('Arrived Load Port',    fmt_dt(load_port.get('arrived_load_port'))),
        ('Alongside Berth',      fmt_dt(load_port.get('alongside_berth'))),
        ('Loading Commenced',    fmt_dt(load_port.get('loading_commenced'))),
        ('Loading Completed',    fmt_dt(load_port.get('loading_completed'))),
        ('Cast Off Load Port',   fmt_dt(load_port.get('cast_off_load_port'))),
        ('ETA at Gull Island',   fmt_dt(load_port.get('eta'))),
    ]:
        if val:
            lp_rows.append({'label': label, 'value': val})
    sections.append({'title': 'Load Port Details', 'rows': lp_rows})

    # ── Section 2: Discharge Port Details ───────────────────────────────────
    dp_rows = []
    dp_fields = [
        ('Arrival Gull Island',          fmt_dt(discharge_port.get('arrival_gull_island'))),
        ('Departure Gull Island',         fmt_dt(discharge_port.get('departure_gull_island'))),
        ('Arrived Yellow Crane',          fmt_dt(discharge_port.get('arrived_yellow_crane'))),
        ('MBC Arrival Port',             fmt_dt(discharge_port.get('vessel_arrival_port'))),
        ('MBC AMF at Unloading Berth',   fmt_dt(discharge_port.get('vessel_all_made_fast'))),
        ('Unloading Commenced',          fmt_dt(discharge_port.get('unloading_commenced'))),
        ('Cleaning Commenced',           fmt_dt(discharge_port.get('cleaning_commenced'))),
        ('Cleaning Completed',           fmt_dt(discharge_port.get('cleaning_completed'))),
        ('Unloading Completed',          fmt_dt(discharge_port.get('unloading_completed'))),
        ('MBC Cast Off',                 fmt_dt(discharge_port.get('vessel_cast_off'))),
        ('Sailed Out From Load Port',    fmt_dt(discharge_port.get('sailed_out_load_port'))),
        ('Unloaded By',                  discharge_port.get('vessel_unloaded_by') or ''),
        ('Unloading Berth',              discharge_port.get('vessel_unloading_berth') or ''),
        ('Discharge Stop',               fmt_dt(discharge_port.get('discharge_stop_shifting'))),
        ('Discharge Start',              fmt_dt(discharge_port.get('discharge_start_shifting'))),
    ]
    for label, val in dp_fields:
        if val:
            dp_rows.append({'label': label, 'value': val})
    sections.append({'title': 'Discharge Port Details', 'rows': dp_rows})

    return sections


def _build_export_sections(export_load):
    """Return list of section dicts for Export SOF."""
    rows = []
    for label, val in [
        ('Arrived at Port',       fmt_dt(export_load.get('arrived_at_port'))),
        ('Alongside at Berth',    fmt_dt(export_load.get('alongside_at_berth'))),
        ('Loading Commenced',     fmt_dt(export_load.get('loading_commenced'))),
        ('Loading Completed',     fmt_dt(export_load.get('loading_completed'))),
        ('Cast Off From Berth',   fmt_dt(export_load.get('cast_off_from_berth'))),
        ('Sailed Out From Port',  fmt_dt(export_load.get('sailed_out_from_port'))),
        ('ETA at Gull Island',    fmt_dt(export_load.get('eta_at_gull_island'))),
        ('Unloaded By',           export_load.get('unloaded_by') or ''),
        ('Berth Master',          export_load.get('berth_master') or ''),
    ]:
        if val:
            rows.append({'label': label, 'value': val})
    return [{'title': 'Load Port Details', 'rows': rows}]


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@bp.route('/module/RP01/mbc-sof/')
@login_required
def mbc_sof_list():
    records = _fetch_mbc_list()
    import_records = [r for r in records if (r.get('operation_type') or '').lower() == 'import']
    export_records = [r for r in records if (r.get('operation_type') or '').lower() != 'import']
    return render_template('mbc_sof/mbc_sof_list.html',
                           import_records=import_records,
                           export_records=export_records,
                           username=session.get('username'))


@bp.route('/module/RP01/mbc-sof/<int:mbc_id>')
@login_required
def mbc_sof_print(mbc_id):
    header, load_port, discharge_port, export_load = _fetch_mbc_sof_data(mbc_id)
    if not header:
        return "Record not found", 404

    op_type = (header.get('operation_type') or '').lower()
    if op_type == 'export':
        sections = _build_export_sections(export_load)
    else:
        sections = _build_import_sections(load_port, discharge_port)

    mbc_name   = header.get('mbc_name', '')
    mbc_no     = header.get('doc_num', '')
    cargo_name = header.get('cargo_name', '') or header.get('cargo_type', '')
    bl_qty     = header.get('bl_quantity') or 0
    uom        = header.get('quantity_uom', 'MT')

    banner_parts = []
    if mbc_no:
        banner_parts.append(f"MBC No: {mbc_no}")
    banner_parts.append(f"{mbc_name} - {header.get('operation_type', '')} of {cargo_name}")
    if bl_qty:
        banner_parts.append(f"{fmt_qty(bl_qty)} {uom}")
    banner = " - ".join(banner_parts)

    return render_template('mbc_sof/mbc_sof_print.html',
                           header=header,
                           mbc_name=mbc_name,
                           mbc_no=mbc_no,
                           op_type=header.get('operation_type', ''),
                           sections=sections,
                           cargo_name=cargo_name,
                           banner=banner,
                           mbc_id=mbc_id)
