from flask import Blueprint, render_template, request, jsonify, session, redirect, url_for
from functools import wraps
from . import model
from database import get_user_permissions

bp = Blueprint('FSAP01', __name__, template_folder='.')
MODULE_CODE = 'FSAP01'


def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated


def get_perms():
    if session.get('is_admin'):
        return {'can_read': 1, 'can_add': 1, 'can_edit': 1, 'can_delete': 1}
    return get_user_permissions(session.get('user_id'), MODULE_CODE)


@bp.route('/module/FSAP01/')
@login_required
def view():
    perms = get_perms()
    if not perms.get('can_read'):
        return render_template('no_access.html'), 403
    return render_template('fsap01.html', permissions=perms)


@bp.route('/api/module/FSAP01/sap-invoice-logs')
@login_required
def sap_invoice_logs():
    page = int(request.args.get('page', 1))
    size = int(request.args.get('size', 50))
    data, total = model.get_sap_invoice_logs(page, size)
    return jsonify({'data': data, 'last_page': (total + size - 1) // size, 'total': total})


@bp.route('/api/module/FSAP01/sap-cn-logs')
@login_required
def sap_cn_logs():
    page = int(request.args.get('page', 1))
    size = int(request.args.get('size', 50))
    data, total = model.get_sap_cn_logs(page, size)
    return jsonify({'data': data, 'last_page': (total + size - 1) // size, 'total': total})


@bp.route('/api/module/FSAP01/gst-logs')
@login_required
def gst_logs():
    page = int(request.args.get('page', 1))
    size = int(request.args.get('size', 50))
    data, total = model.get_gst_logs(page, size)
    return jsonify({'data': data, 'last_page': (total + size - 1) // size, 'total': total})


# ── Staging table endpoints ───────────────────────────────────────────────────

@bp.route('/api/module/FSAP01/staging/push', methods=['POST'])
@login_required
def staging_push():
    """Write invoice data into invoice_sap_staging (processing_status = 'N')."""
    perms = get_perms()
    if not perms.get('can_add'):
        return jsonify({'ok': False, 'error': 'No permission'}), 403

    invoice_id = (request.json or {}).get('invoice_id')
    if not invoice_id:
        return jsonify({'ok': False, 'error': 'invoice_id required'}), 400

    from flask import session as _session
    result = model.push_invoice_to_staging(invoice_id, pushed_by=_session.get('username'))
    return jsonify(result)


@bp.route('/api/module/FSAP01/staging/sync', methods=['POST'])
@login_required
def staging_sync():
    """Read SAP response back from staging and update invoice_header."""
    invoice_id = (request.json or {}).get('invoice_id')
    if not invoice_id:
        return jsonify({'ok': False, 'error': 'invoice_id required'}), 400

    result = model.sync_staging_response(invoice_id)
    return jsonify(result)


@bp.route('/api/module/FSAP01/staging/rows/<int:invoice_id>')
@login_required
def staging_rows(invoice_id):
    """Return all staging rows for a specific invoice (for debug/audit)."""
    rows = model.get_staging_rows_for_invoice(invoice_id)
    return jsonify({'data': rows, 'total': len(rows)})


@bp.route('/api/module/FSAP01/staging/list')
@login_required
def staging_list():
    """Paginated list of all staging rows with optional status filter."""
    page   = int(request.args.get('page', 1))
    size   = int(request.args.get('size', 50))
    status = request.args.get('status') or None
    data, total = model.get_staging_rows(page, size, status)
    return jsonify({'data': data, 'last_page': (total + size - 1) // size, 'total': total})


# ── SAP adapter inbound callback ──────────────────────────────────────────────

@bp.route('/api/sap/callback', methods=['POST'])
def sap_callback():
    """
    Inbound endpoint called by the SAP adapter after it processes staging rows.

    Matched by reference_text (= invoice_number, up to 16 chars).
    Updates invoice_sap_staging rows and invoice_header accordingly.

    No session auth — secured by a static API key passed in the
    X-SAP-Api-Key header (configured in sap_api_config.callback_api_key).

    Expected JSON body:
        {
          "reference_text":      "INV/2025/0042",
          "processing_status":   "Y",          -- Y | E | R
          "sap_document_number": "1900000123",
          "fiscal_year":         "2025",
          "fiscal_period":       "01",
          "sap_message":         "",
          "irn_number":          "abc...def",
          "ack_number":          "1234567890",
          "irn_date":            "2025-04-14",
          "qr_code":             "..."
        }
    """
    # ── API-key guard ─────────────────────────────────────────────────────────
    from database import get_db, get_cursor
    conn = get_db()
    cur  = get_cursor(conn)
    cur.execute("SELECT callback_api_key FROM sap_api_config WHERE is_active = 1 LIMIT 1")
    cfg_row = cur.fetchone()
    conn.close()
    expected_key = (cfg_row['callback_api_key'] if cfg_row else None) or ''

    if expected_key:
        provided_key = request.headers.get('X-SAP-Api-Key', '')
        if provided_key != expected_key:
            return jsonify({'ok': False, 'error': 'Unauthorized'}), 401

    payload = request.get_json(force=True, silent=True) or {}
    result  = model.sap_callback(payload)
    status_code = 200 if result.get('ok') else 400
    return jsonify(result), status_code
