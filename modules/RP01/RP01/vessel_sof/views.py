from flask import render_template, jsonify, session, redirect, url_for
from functools import wraps
from datetime import datetime
from urllib.parse import quote as url_quote
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
# Timestamp helpers
# ---------------------------------------------------------------------------

def _parse(ts):
    """Return a datetime from various formats, or None."""
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


def fmt_range(ts_from, ts_to):
    """'2026-02-08 20:00'  to  '2026-02-08 21:00'
       →  'ON 08.02.2026 AT 2000 HRS. TO 2100 HRS.'
    """
    dt_from = _parse(ts_from)
    dt_to   = _parse(ts_to)
    if not dt_from and not dt_to:
        return ''
    from_str = fmt_dt(ts_from) if dt_from else ''
    if dt_to:
        if dt_from and dt_from.date() == dt_to.date():
            return f"{from_str} TO {dt_to.strftime('%H%M')} HRS."
        else:
            return f"{from_str} TO ON {dt_to.strftime('%d.%m.%Y')} AT {dt_to.strftime('%H%M')} HRS."
    return from_str


def fmt_qty(value):
    if value is None or value == '':
        return ''
    try:
        text = f'{float(value):,.3f}'
    except (TypeError, ValueError):
        return str(value)
    return text[:-4] if text.endswith('.000') else text


# ---------------------------------------------------------------------------
# SOF row builder
# ---------------------------------------------------------------------------

def build_sof_rows(header, anchorages, cargo_list):
    """Return a flat list of {label, value} dicts for SOF rows 1-20+."""
    cargo_name = cargo_list[0]['cargo_name'] if cargo_list else (header.get('cargo_type') or '')
    uom        = cargo_list[0]['quantity_uom'] if cargo_list else 'MT'
    bl_total   = sum(float(c.get('bl_quantity') or 0) for c in cargo_list)

    rows = []

    # ── Fixed rows 1–8 ──────────────────────────────────────────────────────
    vessel_display = header.get('vessel_name', '')
    if vessel_display and not vessel_display.upper().startswith('M.V'):
        vessel_display = f"{vessel_display}"

    rows.append({'label': 'Name of Vessel',             'value': vessel_display})
    rows.append({'label': 'Vessel No',                  'value': header.get('vessel_unique_no', '')})
    rows.append({'label': 'Flag',                        'value': header.get('nationality', '')})
    rows.append({'label': 'Receivers',                   'value': header.get('importer_exporter_name', '')})
    rows.append({'label': 'Load Port',                   'value': header.get('load_port', '')})
    rows.append({'label': 'Discharge Port',              'value': header.get('discharge_port', '')})
    rows.append({'label': 'Discharge Port Agents',       'value': header.get('importer_exporter_name', '')})
    rows.append({'label': 'Notice of Readiness Tendered','value': fmt_dt(header.get('nor_tendered'))})

    # ── Dynamic anchorage rows ──────────────────────────────────────────────
    # Row labels use anchorage_name from ldud_anchorage — fully dynamic.
    # ldud_anchorage field mapping (confusing DB names):
    #   anchored            → when vessel dropped anchor at this anchorage
    #   discharge_started   → when discharge/loading STARTED  (= "Vessel Commenced Discharge at")
    #   discharge_commenced → when discharge/loading COMPLETED (= "Discharging Completed" for last)
    #   anchor_aweigh       → when vessel raised anchor and left
    #
    # Pattern per anchorage:
    #   "Vessel Anchor [Name]"
    #   [Initial Draft Survey — first anchorage only]
    #   "Vessel Commenced Discharge at [Name]"   (if discharge_started set)
    #   "Vessel Anchor Aweigh [Name]"             (if anchor_aweigh set)
    #   "Vessel Anchored [Next Name]"             (if not last anchorage)
    # Last anchorage arrival is added by the previous iteration's "Vessel Anchored [Next]" row.

    for i, anch in enumerate(anchorages):
        anch_name = anch.get('anchorage_name', '')

        if i == 0:
            # First anchorage — always emit all available sub-rows
            rows.append({'label': f'Vessel Anchor {anch_name}',
                         'value': fmt_dt(anch.get('anchored'))})
            rows.append({'label': 'Initial Draft Survey Commenced / Completed',
                         'value': fmt_range(header.get('initial_draft_survey_from'),
                                            header.get('initial_draft_survey_to'))})
            if anch.get('discharge_started'):
                rows.append({'label': f'Vessel Commenced Discharge at {anch_name}',
                             'value': fmt_dt(anch.get('discharge_started'))})
            if anch.get('anchor_aweigh'):
                rows.append({'label': f'Vessel Anchor Aweigh {anch_name}',
                             'value': fmt_dt(anch.get('anchor_aweigh'))})
            # Arrival row for next anchorage
            if i + 1 < len(anchorages):
                next_anch = anchorages[i + 1]
                rows.append({'label': f'Vessel Anchored {next_anch.get("anchorage_name", "")}',
                             'value': fmt_dt(next_anch.get('anchored'))})

        elif i < len(anchorages) - 1:
            # Middle anchorages (3+ anchorages scenario)
            if anch.get('discharge_started'):
                rows.append({'label': f'Vessel Commenced Discharge at {anch_name}',
                             'value': fmt_dt(anch.get('discharge_started'))})
            if anch.get('anchor_aweigh'):
                rows.append({'label': f'Vessel Anchor Aweigh {anch_name}',
                             'value': fmt_dt(anch.get('anchor_aweigh'))})
            next_anch = anchorages[i + 1]
            rows.append({'label': f'Vessel Anchored {next_anch.get("anchorage_name", "")}',
                         'value': fmt_dt(next_anch.get('anchored'))})
        # Last anchorage: its "Vessel Anchored [Name]" row was already emitted above

    # ldud_anchorage.discharge_commenced is the discharge *completion* time (per UI naming)
    last_disc_completed = anchorages[-1].get('discharge_commenced') if anchorages else None
    rows.append({'label': 'Discharging Completed',
                 'value': fmt_dt(last_disc_completed or header.get('discharge_completed'))})
    rows.append({'label': 'Final Draft Survey Commenced / Completed',
                 'value': fmt_range(header.get('final_draft_survey_from'),
                                    header.get('final_draft_survey_to'))})
    rows.append({'label': 'Manifested Cargo Quantity as Per B/L',
                 'value': f'{fmt_qty(bl_total)} {uom} {cargo_name} IN BULK' if bl_total else ''})
    rows.append({'label': 'Agent / Custom / Survey On Board',
                 'value': fmt_dt(header.get('agent_stevedore_onboard'))})
    rows.append({'label': 'Customs Clearance',
                 'value': fmt_dt(header.get('custom_clearance'))})

    # Quantity discharged per anchorage
    total_discharged = 0.0
    for anch in anchorages:
        qty = float(anch.get('cargo_quantity') or 0)
        if qty:
            total_discharged += qty
            anch_name  = anch.get('anchorage_name', '')
            anch_cargo = anch.get('cargo_name') or cargo_name
            rows.append({'label': f'Quantity Discharged at {anch_name}',
                         'value': f'{fmt_qty(qty)} {uom} {anch_cargo} IN BULK'})

    rows.append({'label': 'Total Quantity Discharged at B/L',
                 'value': f'{fmt_qty(total_discharged)} {uom} {cargo_name} IN BULK' if total_discharged else ''})

    last_anch_name = anchorages[-1].get('anchorage_name', '') if anchorages else ''
    sailed_label   = f'Vessel Sailed from {last_anch_name}' if last_anch_name else 'Vessel Sailed from'
    rows.append({'label': sailed_label, 'value': ''})

    return rows


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

def _fetch_barge_list():
    """Return barges with trip counts, keyed by vessel name for the list view."""
    conn = get_db()
    cur  = get_cursor(conn)
    cur.execute("""
        SELECT
            COALESCE(vs.vessel_name, vcn.vessel_name) AS vessel_name,
            lbl.ldud_id,
            h.doc_num  AS ldud_doc_num,
            lbl.barge_name,
            COUNT(lbl.id) AS trip_count,
            vcn.operation_type
        FROM ldud_barge_lines lbl
        JOIN ldud_header h  ON h.id  = lbl.ldud_id
        JOIN vcn_header vcn ON vcn.id = h.vcn_id
        LEFT JOIN vessels vs ON vs.doc_num = SPLIT_PART(vcn.vessel_master_doc, '/', 1)
        WHERE lbl.barge_name IS NOT NULL AND lbl.barge_name != ''
        GROUP BY COALESCE(vs.vessel_name, vcn.vessel_name),
                 lbl.ldud_id, h.doc_num, lbl.barge_name, vcn.operation_type
        ORDER BY vessel_name, lbl.barge_name
    """)
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    for r in rows:
        r['barge_name_encoded'] = url_quote(r['barge_name'] or '', safe='')
    return rows


def _fetch_barge_sof_data(ldud_id, barge_name):
    """Return (header dict, list of trip dicts) for one barge within an LDUD."""
    conn = get_db()
    cur  = get_cursor(conn)
    cur.execute("""
        SELECT
            h.*,
            vcn.vessel_name, vcn.operation_type, vcn.cargo_type,
            vcn.load_port, vcn.discharge_port,
            COALESCE(vs.doc_num, NULLIF(SPLIT_PART(vcn.vessel_master_doc, '/', 1), ''), '') AS vessel_unique_no
        FROM ldud_header h
        JOIN vcn_header vcn ON vcn.id = h.vcn_id
        LEFT JOIN vessels vs ON vs.doc_num = SPLIT_PART(vcn.vessel_master_doc, '/', 1)
        WHERE h.id = %s
    """, (ldud_id,))
    header = dict(cur.fetchone() or {})
    if not header:
        conn.close()
        return None, []
    cur.execute("""
        SELECT lbl.*, b.id AS barge_unique_no
        FROM ldud_barge_lines lbl
        LEFT JOIN barges b ON b.barge_name = lbl.barge_name
        WHERE lbl.ldud_id = %s AND lbl.barge_name = %s
        ORDER BY lbl.trip_number ASC
    """, (ldud_id, barge_name))
    trips = [dict(r) for r in cur.fetchall()]
    barge_unique_no = trips[0].get('barge_unique_no') if trips else None
    if not barge_unique_no:
        cur.execute("SELECT id FROM barges WHERE barge_name = %s", (barge_name,))
        barge_row = cur.fetchone()
        barge_unique_no = barge_row['id'] if barge_row else ''
    header['barge_unique_no'] = barge_unique_no or ''
    conn.close()
    return header, trips


def _build_barge_trip_rows(trip, op_type):
    """Return a flat list of {label, value} for one barge trip (only non-empty fields)."""
    rows = []

    def _add(label, value):
        if value:
            rows.append({'label': label, 'value': value})

    if trip.get('hold_name'):
        _add('Hold', trip['hold_name'])
    if trip.get('cargo_name'):
        _add('Cargo', trip['cargo_name'])
    if trip.get('contractor_name'):
        _add('Contractor / Stevedore', trip['contractor_name'])
    if trip.get('bpt_bfl'):
        _add('MBPT / PLA', trip['bpt_bfl'])

    if (op_type or '').lower() == 'export':
        _add('Trip Start',                      fmt_dt(trip.get('trip_start')))
        _add('AMF at Port',                     fmt_dt(trip.get('amf_at_port')))
        _add('Alongside Loading Berth',         fmt_dt(trip.get('along_side_berth')))
        _add('Cast Off Loading Berth',          fmt_dt(trip.get('cast_off_loading_berth')))
        if trip.get('port_crane'):
            _add('Port Crane', trip['port_crane'])
        _add('Anchored Gull Island (Loaded)',   fmt_dt(trip.get('anchored_gull_island')))
        _add('Aweigh Gull Island (Loaded)',     fmt_dt(trip.get('aweigh_gull_island')))
        _add('Alongside Vessel',                fmt_dt(trip.get('along_side_vessel')))
        _add('Commenced Loading onto Vessel',   fmt_dt(trip.get('commenced_loading')))
        _add('Completed Loading onto Vessel',   fmt_dt(trip.get('completed_loading')))
        _add('Cast Off M.V.',                   fmt_dt(trip.get('cast_off_mv')))
        _add('Anchored Gull Island (Empty)',    fmt_dt(trip.get('anchored_gull_island_empty')))
        _add('Aweigh Gull Island (Empty)',      fmt_dt(trip.get('aweigh_gull_island_empty')))
    else:  # Import
        if trip.get('crane_loaded_from'):
            _add('Crane Loaded From', trip['crane_loaded_from'])
        _add('Trip Start',                         fmt_dt(trip.get('trip_start')))
        _add('Anchored Gull Island (Loaded)',       fmt_dt(trip.get('anchored_gull_island')))
        _add('Aweigh Gull Island (Loaded)',         fmt_dt(trip.get('aweigh_gull_island')))
        _add('Alongside Vessel',                    fmt_dt(trip.get('along_side_vessel')))
        _add('Commenced Loading from Vessel',       fmt_dt(trip.get('commenced_loading')))
        _add('Completed Loading from Vessel',       fmt_dt(trip.get('completed_loading')))
        _add('Cast Off M.V.',                       fmt_dt(trip.get('cast_off_mv')))
        _add('Anchored Gull Island (Empty)',        fmt_dt(trip.get('anchored_gull_island_empty')))
        _add('Aweigh Gull Island (Empty)',          fmt_dt(trip.get('aweigh_gull_island_empty')))
        _add('AMF at Port',                         fmt_dt(trip.get('amf_at_port')))
        _add('Alongside Berth',                     fmt_dt(trip.get('along_side_berth')))
        _add('Commence Discharge at Berth',         fmt_dt(trip.get('commence_discharge_berth')))
        _add('Completed Discharge at Berth',        fmt_dt(trip.get('completed_discharge_berth')))
        _add('Cast Off Berth',                      fmt_dt(trip.get('cast_off_berth')))
        _add('Cast Off Port',                       fmt_dt(trip.get('cast_off_port')))

    qty = float(trip.get('discharge_quantity') or 0)
    if qty:
        verb = 'Loaded' if (op_type or '').lower() == 'export' else 'Discharged'
        rows.append({'label': f'Quantity {verb}', 'value': f'{fmt_qty(qty)} MT'})

    return rows


def _fetch_vessel_list():
    conn = get_db()
    cur  = get_cursor(conn)
    cur.execute("""
        SELECT
            COALESCE(vs.vessel_name, vcn.vessel_name) AS vessel_name,
            h.id          AS ldud_id,
            h.doc_num     AS ldud_doc_num,
            h.vcn_doc_num,
            h.nor_tendered,
            h.discharge_completed,
            h.doc_status,
            vcn.operation_type,
            vcn.cargo_type
        FROM ldud_header h
        JOIN vcn_header vcn ON h.vcn_id = vcn.id
        LEFT JOIN vessels vs ON vcn.vessel_master_doc = vs.doc_num
        ORDER BY vessel_name, h.nor_tendered DESC NULLS LAST
    """)
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return rows


def _fetch_sof_data(ldud_id):
    conn = get_db()
    cur  = get_cursor(conn)

    # 1. Header
    # vessel_master_doc is stored as "VM1/VesselName" so split on '/' to get doc_num
    cur.execute("""
        SELECT
            h.*,
            vcn.vessel_name, vcn.vessel_agent_name, vcn.importer_exporter_name,
            vcn.operation_type, vcn.cargo_type,
            vcn.load_port, vcn.discharge_port,
            vs.nationality,
            COALESCE(vs.doc_num, NULLIF(SPLIT_PART(vcn.vessel_master_doc, '/', 1), ''), '') AS vessel_unique_no
        FROM ldud_header h
        JOIN vcn_header vcn ON h.vcn_id = vcn.id
        LEFT JOIN vessels vs ON vs.doc_num = SPLIT_PART(vcn.vessel_master_doc, '/', 1)
        WHERE h.id = %s
    """, (ldud_id,))
    header = dict(cur.fetchone() or {})

    if not header:
        conn.close()
        return None, [], [], [], []

    vcn_id = header.get('vcn_id')

    # 2. BL cargo
    cur.execute("""
        SELECT cargo_name, SUM(bl_quantity) AS bl_quantity, quantity_uom
        FROM vcn_cargo_declaration
        WHERE vcn_id = %s
        GROUP BY cargo_name, quantity_uom
        ORDER BY bl_quantity DESC NULLS LAST
    """, (vcn_id,))
    cargo_list = [dict(r) for r in cur.fetchall()]

    # 3. Anchorages
    cur.execute("""
        SELECT * FROM ldud_anchorage
        WHERE ldud_id = %s
        ORDER BY COALESCE(discharge_started, anchored) ASC NULLS LAST
    """, (ldud_id,))
    anchorages = [dict(r) for r in cur.fetchall()]

    # 4. Hold completion
    cur.execute("""
        SELECT lhc.hold_name, lhc.completed, lhcargo.cargo_name
        FROM ldud_hold_completion lhc
        LEFT JOIN ldud_hold_cargo lhcargo
            ON lhcargo.ldud_id = lhc.ldud_id AND lhcargo.hold_name = lhc.hold_name
        WHERE lhc.ldud_id = %s
        ORDER BY lhc.completed ASC NULLS LAST
    """, (ldud_id,))
    holds = [dict(r) for r in cur.fetchall()]

    # 5. All delays for this LDUD (delays_to_sof is never saved by the model, so no filter)
    cur.execute("""
        SELECT crane_number, start_datetime, end_datetime, delay_name
        FROM ldud_delays
        WHERE ldud_id = %s
        ORDER BY start_datetime ASC NULLS LAST
    """, (ldud_id,))
    delays = [dict(r) for r in cur.fetchall()]

    conn.close()
    return header, cargo_list, anchorages, holds, delays


def _fmt_hold_completion(hold, operation_type):
    """'Hold 1 Completed Loading 1000 Hrs On 14.02.2026'"""
    dt = _parse(hold.get('completed'))
    verb = 'Loading' if (operation_type or '').lower() == 'export' else 'Discharging'
    time_str = dt.strftime('%H%M') + ' Hrs' if dt else ''
    date_str = 'On ' + dt.strftime('%d.%m.%Y') if dt else ''
    return f"{hold.get('hold_name', '')} Completed {verb} {time_str} {date_str}".strip()


def _fmt_delay_time(ts):
    """'2026-02-09 02:20:00' → '09.02.2026 - 0220 HRS'"""
    dt = _parse(ts)
    if not dt:
        return ''
    return f"{dt.strftime('%d.%m.%Y')} - {dt.strftime('%H%M')} HRS"


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@bp.route('/api/module/RP01/vessel-sof/vessels')
@login_required
def api_vessels():
    rows = _fetch_vessel_list()
    # Group by vessel_name
    grouped = {}
    for r in rows:
        vname = r['vessel_name'] or 'Unknown Vessel'
        if vname not in grouped:
            grouped[vname] = []
        grouped[vname].append(r)
    result = [{'vessel_name': k, 'records': v} for k, v in grouped.items()]
    return jsonify(result)


@bp.route('/module/RP01/vessel-sof/')
@login_required
def vessel_sof_list():
    rows       = _fetch_vessel_list()
    barge_rows = _fetch_barge_list()

    # Build barge lookup: vessel_name → {'Import': [...], 'Export': [...]}
    barges_map = {}
    for b in barge_rows:
        vname = b['vessel_name'] or 'Unknown Vessel'
        cat   = 'Export' if (b.get('operation_type') or '').lower() == 'export' else 'Import'
        if vname not in barges_map:
            barges_map[vname] = {'Import': [], 'Export': []}
        barges_map[vname][cat].append(b)

    # Group LDUD records by vessel
    grouped = {}
    for r in rows:
        vname = r['vessel_name'] or 'Unknown Vessel'
        if vname not in grouped:
            grouped[vname] = []
        grouped[vname].append(r)

    vessels = [
        {
            'vessel_name':   k,
            'records':       v,
            'import_barges': barges_map.get(k, {}).get('Import', []),
            'export_barges': barges_map.get(k, {}).get('Export', []),
        }
        for k, v in grouped.items()
    ]
    return render_template('vessel_sof/vessel_sof_list.html',
                           vessels=vessels,
                           username=session.get('username'))


@bp.route('/module/RP01/barge-sof/<int:ldud_id>/<barge_name>')
@login_required
def vessel_barge_sof_print(ldud_id, barge_name):
    header, trips = _fetch_barge_sof_data(ldud_id, barge_name)
    if not header:
        return "Record not found", 404

    op_type = header.get('operation_type', 'Import')
    trips_with_rows = [
        {'trip': t, 'rows': _build_barge_trip_rows(t, op_type)}
        for t in trips
    ]
    cargo_name  = trips[0].get('cargo_name') if trips else (header.get('cargo_type') or '')
    vessel_name = header.get('vessel_name', '')
    barge_unique_no = header.get('barge_unique_no', '')

    return render_template('vessel_sof/barge_sof_print.html',
                           header=header,
                           barge_name=barge_name,
                           barge_unique_no=barge_unique_no,
                           op_type=op_type,
                           trips_with_rows=trips_with_rows,
                           cargo_name=cargo_name,
                           vessel_name=vessel_name,
                           ldud_id=ldud_id)


@bp.route('/module/RP01/vessel-sof/<int:ldud_id>')
@login_required
def vessel_sof_print(ldud_id):
    header, cargo_list, anchorages, holds, delays = _fetch_sof_data(ldud_id)
    if not header:
        return "Record not found", 404

    sof_rows = build_sof_rows(header, anchorages, cargo_list)

    # Build hold display strings
    op_type = header.get('operation_type', '')
    holds_display = [_fmt_hold_completion(h, op_type) for h in holds]

    # Build delay display rows
    delays_display = []
    for d in delays:
        delays_display.append({
            'crane_number':  d.get('crane_number', ''),
            'date_from':     _fmt_delay_time(d.get('start_datetime')),
            'date_to':       _fmt_delay_time(d.get('end_datetime')),
            'reason':        d.get('delay_name', ''),
        })

    # Build banner
    cargo_name = cargo_list[0]['cargo_name'] if cargo_list else (header.get('cargo_type') or '')
    uom        = cargo_list[0]['quantity_uom'] if cargo_list else 'MT'
    bl_total   = sum(float(c.get('bl_quantity') or 0) for c in cargo_list)
    bl_qty_display = fmt_qty(bl_total)
    nor_dt     = _parse(header.get('nor_tendered'))
    nor_date   = nor_dt.strftime('%d.%m.%Y') if nor_dt else ''
    nor_time   = nor_dt.strftime('%H%M') if nor_dt else ''
    vessel_name = header.get('vessel_name', '')

    discharge_port = header.get('discharge_port') or 'Discharge Port'
    banner = (
        f"{vessel_name} Arrived at {discharge_port} on {nor_date} AT {nor_time} Hrs. "
        f"for {op_type} {bl_qty_display} {uom} {cargo_name} IN BULK "
        f"as Per Terms, Conditions, and Exception of Relevant Charter Party"
    )

    return render_template('vessel_sof/vessel_sof_print.html',
                           header=header,
                           sof_rows=sof_rows,
                           holds_display=holds_display,
                           delays_display=delays_display,
                           banner=banner,
                           vessel_name=vessel_name,
                           ldud_id=ldud_id)
