from flask import Blueprint, render_template, request, jsonify, session, redirect, url_for
from functools import wraps
from . import model
from database import get_user_permissions, get_db, get_cursor
import einvoice_builder
import gsp_client

bp = Blueprint('FCN01', __name__, template_folder='.')
MODULE_CODE = 'FCN01'


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


@bp.route('/module/FCN01/')
@login_required
def view():
    perms = get_perms()
    if not perms.get('can_read'):
        return render_template('no_access.html'), 403
    return render_template('fcn01.html', permissions=perms)


@bp.route('/api/module/FCN01/data')
@login_required
def get_data():
    page = int(request.args.get('page', 1))
    size = int(request.args.get('size', 20))
    data, total = model.get_credit_notes(page, size)
    return jsonify({'data': data, 'last_page': (total + size - 1) // size, 'total': total})


@bp.route('/api/module/FCN01/detail/<int:cn_id>')
@login_required
def get_detail(cn_id):
    header, lines = model.get_credit_note(cn_id)
    if not header:
        return jsonify({'error': 'Not found'}), 404
    return jsonify({'header': header, 'lines': lines})


@bp.route('/api/module/FCN01/save', methods=['POST'])
@login_required
def save():
    perms = get_perms()
    data = request.json
    is_new = not data.get('id')
    if is_new and not perms.get('can_add'):
        return jsonify({'error': 'No permission to add'}), 403
    if not is_new and not perms.get('can_edit'):
        return jsonify({'error': 'No permission to edit'}), 403
    cn_id = model.save_credit_note(data, session.get('username'))
    return jsonify({'success': True, 'id': cn_id})


@bp.route('/api/module/FCN01/save-line', methods=['POST'])
@login_required
def save_line():
    perms = get_perms()
    if not perms.get('can_edit') and not perms.get('can_add'):
        return jsonify({'error': 'No permission'}), 403
    data = request.json
    line_id = model.save_credit_note_line(data)
    return jsonify({'success': True, 'id': line_id})


@bp.route('/api/module/FCN01/delete', methods=['POST'])
@login_required
def delete():
    perms = get_perms()
    if not perms.get('can_delete'):
        return jsonify({'error': 'No permission to delete'}), 403
    model.delete_credit_note(request.json.get('id'))
    return jsonify({'success': True})


@bp.route('/api/module/FCN01/delete-line', methods=['POST'])
@login_required
def delete_line():
    perms = get_perms()
    if not perms.get('can_delete'):
        return jsonify({'error': 'No permission to delete'}), 403
    model.delete_credit_note_line(request.json.get('id'))
    return jsonify({'success': True})


@bp.route('/api/module/FCN01/invoices')
@login_required
def get_invoices():
    return jsonify(model.get_invoices_for_dropdown())


# ===== GST IRN Integration =====

@bp.route('/api/module/FCN01/generate-irn', methods=['POST'])
@login_required
def generate_irn():
    """Generate IRN for a credit note via IRP e-invoice API"""
    perms = get_perms()
    if not perms.get('can_edit'):
        return jsonify({'success': False, 'error': 'No permission'}), 403

    cn_id = request.json.get('id')
    header, lines = model.get_credit_note(cn_id)
    if not header:
        return jsonify({'success': False, 'error': 'Credit note not found'}), 404

    if header.get('gst_irn'):
        return jsonify({'success': False, 'error': 'IRN already generated'})

    einvoice_json = einvoice_builder.build_einvoice_from_credit_note(header, lines)
    result = gsp_client.generate_irn(
        einvoice_json, 'CreditNote', cn_id,
        header.get('credit_note_number') or header.get('cn_number', ''),
        session.get('username')
    )

    if result['ok']:
        conn = get_db()
        cur = get_cursor(conn)
        cur.execute('''UPDATE credit_note_header
            SET gst_irn=%s, gst_ack_number=%s
            WHERE id=%s''',
            [result['irn'], result['ack_number'], cn_id])
        conn.commit()
        conn.close()

    return jsonify({
        'success': result['ok'],
        'irn': result.get('irn'),
        'ack_number': result.get('ack_number'),
        'message': result['message'],
        'log_id': result['log_id']
    })
