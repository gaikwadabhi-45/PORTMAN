from flask import Blueprint, render_template, request, jsonify, session, redirect, url_for
from functools import wraps
from . import model
from database import get_user_permissions, get_module_config
import sap_builder
import sap_client
import logging

log = logging.getLogger(__name__)

bp = Blueprint('FDCN01', __name__, template_folder='.')
MODULE_CODE = 'FDCN01'


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
    perms = get_user_permissions(session.get('user_id'), MODULE_CODE)
    if not perms.get('can_read'):
        perms = get_user_permissions(session.get('user_id'), 'FIN01')
    return perms


def _is_approver():
    if session.get('is_admin'):
        return True
    config = get_module_config(MODULE_CODE)
    if not config:
        config = get_module_config('FIN01')
    approver_id = config.get('approver_id')
    if approver_id and str(session.get('user_id')) == str(approver_id):
        return True
    return False


# ===== Page Routes =====

@bp.route('/module/FDCN01/')
@login_required
def index():
    return redirect(url_for('FDCN01.list_view'))


@bp.route('/module/FDCN01/list')
@login_required
def list_view():
    perms = get_perms()
    if not perms.get('can_read'):
        return render_template('no_access.html'), 403
    return render_template('fdcn01_list.html', permissions=perms, is_approver=_is_approver())


@bp.route('/module/FDCN01/entry')
@login_required
def entry():
    perms = get_perms()
    if not perms.get('can_read'):
        return render_template('no_access.html'), 403
    fdcn_id = request.args.get('id')
    return render_template('fdcn01_entry.html', permissions=perms,
                           is_approver=_is_approver(), fdcn_id=fdcn_id or '')


@bp.route('/module/FDCN01/doc-series')
@login_required
def doc_series_view():
    perms = get_perms()
    if not perms.get('can_read'):
        return render_template('no_access.html'), 403
    return render_template('fdcn01_doc_series.html', permissions=perms)


@bp.route('/module/FDCN01/print/<int:fdcn_id>')
@login_required
def print_view(fdcn_id):
    from database import get_db, get_cursor

    header = model.get_fdcn_by_id(fdcn_id)
    if not header:
        return 'Document not found', 404
    # Convert date objects to ISO strings so templates can subscript/slice them
    for _field in ('doc_date', 'original_invoice_date'):
        if header.get(_field) and not isinstance(header[_field], str):
            header[_field] = header[_field].strftime('%Y-%m-%d')
    lines = model.get_fdcn_lines(fdcn_id)
    sac_summary = model.get_fdcn_sac_summary(fdcn_id)

    # Port config for header GSTIN etc.
    config = get_module_config('FIN01')
    port_config = {
        'seller_gstin': config.get('seller_gstin', ''),
        'seller_legal_name': config.get('seller_legal_name', 'JSW Dharamtar Port Pvt. Ltd.'),
    }

    # Payment bank details
    payment_bank = None
    conn_b = get_db()
    cur_b = get_cursor(conn_b)
    try:
        ctype = (header.get('customer_type') or '').lower()
        cid = header.get('customer_id')
        va = None
        if cid:
            tbl = 'vessel_agents' if ctype == 'agent' else 'vessel_customers'
            cur_b.execute(f'SELECT virtual_account_number FROM {tbl} WHERE id=%s', [cid])
            row = cur_b.fetchone()
            if row:
                va = (row['virtual_account_number'] or '').strip()

        if va:
            cur_b.execute('SELECT * FROM port_bank_accounts ORDER BY id LIMIT 1')
            base = cur_b.fetchone()
            payment_bank = dict(base) if base else {}
            payment_bank['account_number'] = va
        else:
            cur_b.execute('SELECT * FROM port_bank_accounts ORDER BY id LIMIT 1')
            row = cur_b.fetchone()
            payment_bank = dict(row) if row else None
    except Exception:
        payment_bank = None
    conn_b.close()

    return render_template('fdcn01_print.html',
                         header=header, lines=lines,
                         sac_summary=sac_summary,
                         port_config=port_config,
                         payment_bank=payment_bank)


# ===== API: List & Detail =====

@bp.route('/api/module/FDCN01/data')
@login_required
def get_data():
    page = int(request.args.get('page', 1))
    size = int(request.args.get('size', 20))
    status_filter = request.args.get('status') or None
    type_filter = request.args.get('type') or None
    data, total = model.get_fdcn_list(page, size, status_filter, type_filter)
    return jsonify({'data': data, 'last_page': (total + size - 1) // size, 'total': total})


@bp.route('/api/module/FDCN01/detail/<int:fdcn_id>')
@login_required
def get_detail(fdcn_id):
    header = model.get_fdcn_by_id(fdcn_id)
    if not header:
        return jsonify({'error': 'Not found'}), 404
    lines = model.get_fdcn_lines(fdcn_id)
    return jsonify({'header': header, 'lines': lines})


# ===== API: Save =====

@bp.route('/api/module/FDCN01/save', methods=['POST'])
@login_required
def save():
    perms = get_perms()
    data = request.json
    is_new = not data.get('id')

    if is_new and not perms.get('can_add'):
        return jsonify({'error': 'No permission to add'}), 403
    if not is_new and not perms.get('can_edit'):
        return jsonify({'error': 'No permission to edit'}), 403

    header_data = data.get('header', data)
    lines_data = data.get('lines', [])

    # Determine doc_status for new records
    if is_new:
        config = get_module_config(MODULE_CODE) or get_module_config('FIN01') or {}
        if _is_approver():
            header_data['doc_status'] = 'Approved'
        elif config.get('approval_add'):
            header_data['doc_status'] = 'Pending Approval'
        else:
            header_data['doc_status'] = 'Draft'

    fdcn_id = model.save_fdcn_header(header_data, session.get('username'))

    if lines_data:
        model.save_fdcn_lines(fdcn_id, lines_data)

    header = model.get_fdcn_by_id(fdcn_id)
    return jsonify({'success': True, 'id': fdcn_id, 'doc_number': header.get('doc_number')})


# ===== API: Approval Workflow =====

@bp.route('/api/module/FDCN01/submit', methods=['POST'])
@login_required
def submit():
    perms = get_perms()
    if not perms.get('can_edit'):
        return jsonify({'error': 'No permission'}), 403
    fdcn_id = request.json.get('id')
    model.update_fdcn_status(fdcn_id, 'Pending Approval')
    return jsonify({'success': True})


@bp.route('/api/module/FDCN01/approve', methods=['POST'])
@login_required
def approve():
    if not _is_approver():
        return jsonify({'error': 'Only approver/admin can approve'}), 403
    fdcn_id = request.json.get('id')
    model.update_fdcn_status(fdcn_id, 'Approved', session.get('username'))
    return jsonify({'success': True})


@bp.route('/api/module/FDCN01/reject', methods=['POST'])
@login_required
def reject():
    if not _is_approver():
        return jsonify({'error': 'Only approver/admin can reject'}), 403
    fdcn_id = request.json.get('id')
    reason = request.json.get('reason', '')
    model.update_fdcn_status(fdcn_id, 'Rejected', rejection_reason=reason)
    return jsonify({'success': True})


@bp.route('/api/module/FDCN01/delete', methods=['POST'])
@login_required
def delete():
    perms = get_perms()
    if not perms.get('can_delete'):
        return jsonify({'error': 'No permission to delete'}), 403
    fdcn_id = request.json.get('id')
    header = model.get_fdcn_by_id(fdcn_id)
    if header and header.get('doc_status') not in ('Draft', 'Rejected'):
        return jsonify({'error': 'Can only delete Draft or Rejected documents'}), 400
    model.delete_fdcn(fdcn_id)
    return jsonify({'success': True})


# ===== API: Lookups =====

@bp.route('/api/module/FDCN01/customers/<path:customer_type>')
@login_required
def get_customers(customer_type):
    rows = model.get_customers_for_billing(customer_type)
    return jsonify(rows)


@bp.route('/api/module/FDCN01/invoices/<path:customer_type>/<int:customer_id>')
@login_required
def get_invoices(customer_type, customer_id):
    rows = model.get_invoices_for_customer(customer_type, customer_id)
    return jsonify(rows)


@bp.route('/api/module/FDCN01/invoice-lines', methods=['POST'])
@login_required
def get_invoice_lines():
    """Accept multiple invoice IDs and return all lines with service_type_id."""
    invoice_ids = request.json.get('invoice_ids', [])
    if not invoice_ids:
        return jsonify([])
    rows = model.get_invoice_lines_for_fdcn(invoice_ids)
    return jsonify(rows)


@bp.route('/api/module/FDCN01/agreements/<path:customer_type>/<int:customer_id>')
@login_required
def get_agreements(customer_type, customer_id):
    rows = model.get_customer_agreements(customer_type, customer_id)
    return jsonify(rows)


@bp.route('/api/module/FDCN01/agreement-rates/<int:agreement_id>')
@login_required
def get_agreement_rates(agreement_id):
    rows = model.get_agreement_rates(agreement_id)
    return jsonify(rows)


# ===== API: Doc Series =====

@bp.route('/api/module/FDCN01/doc-series/data')
@login_required
def doc_series_data():
    return jsonify(model.get_doc_series_list())


@bp.route('/api/module/FDCN01/doc-series/save', methods=['POST'])
@login_required
def doc_series_save():
    perms = get_perms()
    if not perms.get('can_add') and not perms.get('can_edit'):
        return jsonify({'error': 'No permission'}), 403
    ds_id = model.save_doc_series(request.json)
    return jsonify({'success': True, 'id': ds_id})


@bp.route('/api/module/FDCN01/doc-series/delete', methods=['POST'])
@login_required
def doc_series_delete():
    perms = get_perms()
    if not perms.get('can_delete'):
        return jsonify({'error': 'No permission'}), 403
    model.delete_doc_series(request.json.get('id'))
    return jsonify({'success': True})


# ===== API: SAP Integration =====

@bp.route('/api/module/FDCN01/post-sap', methods=['POST'])
@login_required
def post_sap():
    perms = get_perms()
    if not perms.get('can_edit'):
        return jsonify({'success': False, 'error': 'No permission'}), 403

    fdcn_id = request.json.get('id')
    header = model.get_fdcn_by_id(fdcn_id)
    if not header:
        return jsonify({'success': False, 'error': 'Document not found'}), 404

    if header.get('sap_document_number'):
        return jsonify({'success': False, 'error': 'Already posted to SAP'})

    if header.get('doc_status') not in ('Approved', 'SAP Failed'):
        return jsonify({'success': False, 'error': 'Document must be Approved first'})

    lines = model.get_fdcn_lines(fdcn_id)
    payload = sap_builder.build_fdcn_payload(header, lines)

    doc_type_label = 'DebitNote' if header['doc_type'] == 'DN' else 'CreditNote'
    result = sap_client.post_invoice_to_sap(
        payload, doc_type_label, fdcn_id,
        header.get('doc_number', ''),
        session.get('username')
    )

    if result['ok']:
        model.update_sap_details(fdcn_id, result['sap_document_number'], session.get('username'))

    if not result['ok']:
        from database import get_db as _gdb, get_cursor as _gc
        conn = _gdb()
        cur = _gc(conn)
        cur.execute("UPDATE fdcn_header SET doc_status='SAP Failed' WHERE id=%s", [fdcn_id])
        conn.commit()
        conn.close()

    return jsonify({
        'success': result['ok'],
        'sap_document_number': result.get('sap_document_number'),
        'message': result['message'],
        'log_id': result['log_id']
    })


@bp.route('/api/module/FDCN01/fetch-irn', methods=['POST'])
@login_required
def fetch_irn():
    """Fetch IRN details from SAP (populated by Cygnet after e-invoice generation)"""
    perms = get_perms()
    if not perms.get('can_edit'):
        return jsonify({'success': False, 'error': 'No permission'}), 403

    fdcn_id = request.json.get('id')
    header = model.get_fdcn_by_id(fdcn_id)
    if not header:
        return jsonify({'success': False, 'error': 'Document not found'}), 404

    if not header.get('sap_document_number'):
        return jsonify({'success': False, 'error': 'Document not yet posted to SAP'})

    if header.get('gst_irn'):
        return jsonify({'success': False, 'error': 'IRN already present',
                        'irn': header['gst_irn']})

    doc_type_label = 'DebitNote' if header['doc_type'] == 'DN' else 'CreditNote'
    result = sap_client.fetch_irn_from_sap(
        header.get('doc_number', ''), doc_type_label, fdcn_id,
        session.get('username')
    )

    if result['ok']:
        model.update_gst_details(
            fdcn_id, result['irn'], result['ack_no'],
            result.get('ack_date') or result.get('irn_date'),
            None  # qr_code not fetched
        )

    return jsonify({
        'success': result['ok'],
        'irn': result.get('irn', ''),
        'ack_no': result.get('ack_no', ''),
        'irn_date': result.get('irn_date', ''),
        'message': result['message'],
    })


