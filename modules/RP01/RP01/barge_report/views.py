from flask import render_template, request, jsonify, session, redirect, url_for
from functools import wraps
import traceback

from .. import bp
from database import get_db, get_cursor


def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated


def _fmt(val):
    """Return string or empty string for None."""
    if val is None:
        return ''
    return str(val)


# ── routes ──────────────────────────────────────────────────────────────────────

@bp.route('/module/RP01/barge-report/')
@login_required
def barge_report_index():
    return render_template('barge_report/barge_report.html', username=session.get('username'))


@bp.route('/api/module/RP01/barge-report/vessels')
@login_required
def barge_report_vessels():
    """Return ldud records for a given operation type (import / export)."""
    op_type = request.args.get('op_type', 'import').strip()
    conn = get_db()
    cur = get_cursor(conn)
    try:
        cur.execute("""
            SELECT h.id, h.doc_num, h.vessel_name, h.operation_type,
                   h.nor_tendered, h.doc_status
            FROM ldud_header h
            WHERE LOWER(h.operation_type) = LOWER(%s)
            ORDER BY h.id DESC
        """, (op_type,))
        rows = cur.fetchall()
        result = []
        for r in rows:
            result.append({
                'id': r['id'],
                'doc_num': r['doc_num'] or '',
                'vessel_name': r['vessel_name'] or '',
                'operation_type': r['operation_type'] or '',
                'nor_tendered': _fmt(r['nor_tendered']),
                'doc_status': r['doc_status'] or '',
                'label': f"{r['doc_num'] or ''} — {r['vessel_name'] or ''}",
            })
        return jsonify(result)
    except Exception:
        return jsonify({'error': traceback.format_exc()}), 500
    finally:
        conn.close()


@bp.route('/api/module/RP01/barge-report/data')
@login_required
def barge_report_data():
    """Return barge lines for selected ldud_ids, with vessel header repeated per line."""
    ids_param = request.args.get('ldud_ids', '').strip()
    if not ids_param:
        return jsonify([])

    try:
        ldud_ids = [int(x) for x in ids_param.split(',') if x.strip()]
    except ValueError:
        return jsonify({'error': 'Invalid ldud_ids'}), 400

    if not ldud_ids:
        return jsonify([])

    conn = get_db()
    cur = get_cursor(conn)
    try:
        cur.execute("""
            SELECT
                h.id                          AS ldud_id,
                h.doc_num,
                h.vessel_name,
                h.operation_type,
                h.nor_tendered,
                h.discharge_commenced,
                h.discharge_completed,
                h.doc_status,
                h.initial_draft_survey_quantity,
                v.vessel_agent_name,
                v.importer_exporter_name,
                bl.id                         AS line_id,
                bl.trip_number,
                bl.hold_name,
                bl.barge_name,
                bl.contractor_name,
                bl.cargo_name,
                bl.bpt_bfl,
                bl.discharge_quantity,
                bl.crane_loaded_from,
                bl.port_crane,
                bl.commenced_loading,
                bl.completed_loading,
                bl.along_side_vessel,
                bl.cast_off_mv,
                bl.cast_off_berth,
                bl.cast_off_berth_nt
            FROM ldud_header h
            LEFT JOIN vcn_header v ON v.id = h.vcn_id
            LEFT JOIN ldud_barge_lines bl ON bl.ldud_id = h.id
            WHERE h.id = ANY(%s)
            ORDER BY h.id, bl.trip_number, bl.barge_name, bl.id
        """, (ldud_ids,))
        rows = cur.fetchall()
        result = []
        for r in rows:
            result.append({
                'ldud_id':                    r['ldud_id'],
                'doc_num':                    _fmt(r['doc_num']),
                'vessel_name':                _fmt(r['vessel_name']),
                'operation_type':             _fmt(r['operation_type']),
                'nor_tendered':               _fmt(r['nor_tendered']),
                'discharge_commenced':        _fmt(r['discharge_commenced']),
                'discharge_completed':        _fmt(r['discharge_completed']),
                'doc_status':                 _fmt(r['doc_status']),
                'initial_draft_survey_quantity': _fmt(r['initial_draft_survey_quantity']),
                'vessel_agent_name':          _fmt(r['vessel_agent_name']),
                'importer_exporter_name':     _fmt(r['importer_exporter_name']),
                'line_id':                    r['line_id'],
                'trip_number':                r['trip_number'],
                'hold_name':                  _fmt(r['hold_name']),
                'barge_name':                 _fmt(r['barge_name']),
                'contractor_name':            _fmt(r['contractor_name']),
                'cargo_name':                 _fmt(r['cargo_name']),
                'bpt_bfl':                    _fmt(r['bpt_bfl']),
                'discharge_quantity':         float(r['discharge_quantity']) if r['discharge_quantity'] is not None else None,
                'crane_loaded_from':          _fmt(r['crane_loaded_from']),
                'port_crane':                 _fmt(r['port_crane']),
                'commenced_loading':          _fmt(r['commenced_loading']),
                'completed_loading':          _fmt(r['completed_loading']),
                'along_side_vessel':          _fmt(r['along_side_vessel']),
                'cast_off_mv':                _fmt(r['cast_off_mv']),
                'cast_off_berth':             _fmt(r['cast_off_berth']),
                'cast_off_berth_nt':          _fmt(r['cast_off_berth_nt']),
            })
        return jsonify(result)
    except Exception:
        return jsonify({'error': traceback.format_exc()}), 500
    finally:
        conn.close()
