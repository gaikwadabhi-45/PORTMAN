import json as _json
from flask import Blueprint, render_template, request, jsonify, session, redirect, url_for
from functools import wraps
from . import model
from database import get_user_permissions, get_module_config
from mail_service import queue_mail as _queue_mail

bp = Blueprint('LDUD01', __name__, template_folder='.')
MODULE_CODE = 'LDUD01'

def _get_user_email_by_id(user_id):
    """Return (email, username) for a user_id."""
    if not user_id:
        return None, None
    from database import get_db, get_cursor
    conn = get_db()
    cur = get_cursor(conn)
    cur.execute('SELECT email, username FROM users WHERE id=%s', [user_id])
    row = cur.fetchone()
    conn.close()
    return (row['email'], row['username']) if row else (None, None)

def _get_closer_email(record_id):
    """Return (email, username) of the last user who closed this LDUD record."""
    from database import get_db, get_cursor
    conn = get_db()
    cur = get_cursor(conn)
    cur.execute("""
        SELECT actioned_by FROM approval_log
        WHERE module_code='LDUD01' AND record_id=%s
          AND action IN ('Closed','Partial Close')
        ORDER BY actioned_at DESC LIMIT 1
    """, [record_id])
    row = cur.fetchone()
    conn.close()
    if not row:
        return None, None
    closer_username = row['actioned_by']
    conn = get_db()
    cur = get_cursor(conn)
    cur.execute('SELECT email, username FROM users WHERE username=%s', [closer_username])
    row2 = cur.fetchone()
    conn.close()
    return (row2['email'], row2['username']) if row2 else (None, closer_username)

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

@bp.route('/module/LDUD01/')
@login_required
def view():
    perms = get_perms()
    if not perms.get('can_read'):
        return render_template('no_access.html'), 403
    return render_template('ldud01.html', permissions=perms)

@bp.route('/api/module/LDUD01/data')
@login_required
def get_data():
    try:
        page = int(request.args.get('page', 1))
        size = int(request.args.get('size', 20))
    except (ValueError, TypeError):
        page, size = 1, 20
    try:
        filters = _json.loads(request.args.get('filters', '[]'))
    except _json.JSONDecodeError:
        filters = []
    rows, total = model.get_data(page, size, filters)
    return jsonify({'data': rows, 'last_page': (total + size - 1) // size, 'total': total})

@bp.route('/api/module/LDUD01/vcn_list')
@login_required
def get_vcn_list():
    return jsonify(model.get_vcn_list())

@bp.route('/api/module/LDUD01/vcn_list/export')
@login_required
def get_export_vcn_list():
    return jsonify(model.get_vcn_list())

@bp.route('/api/module/LDUD01/save', methods=['POST'])
@login_required
def save():
    perms = get_perms()
    data = request.json
    is_new = not data.get('id')
    if is_new and not perms.get('can_add'):
        return jsonify({'error': 'No permission to add'}), 403
    if not is_new and not perms.get('can_edit'):
        return jsonify({'error': 'No permission to edit'}), 403

    config = get_module_config('LDUD01')
    is_approver = str(config.get('approver_id', '')) == str(session.get('user_id')) or session.get('is_admin')

    if not is_new:
        current_status = model.get_doc_status(data['id'])
        if current_status == 'Closed':
            if not is_approver:
                return jsonify({'error': 'Cannot edit a closed record'}), 403
            data['doc_status'] = 'Closed'
        elif current_status == 'Partial Close':
            data['doc_status'] = 'Partial Close'
        else:
            data['doc_status'] = 'Draft'
    else:
        data['doc_status'] = 'Draft'

    row_id, doc_num = model.save_header(data)
    return jsonify({'id': row_id, 'doc_num': doc_num, 'doc_status': data.get('doc_status', 'Draft')})


@bp.route('/api/module/LDUD01/closure_check/<int:ldud_id>')
@login_required
def closure_check(ldud_id):
    return jsonify(model.get_closure_eligibility(ldud_id))


@bp.route('/api/module/LDUD01/close', methods=['POST'])
@login_required
def close():
    config = get_module_config('LDUD01')
    is_approver = str(config.get('approver_id', '')) == str(session.get('user_id')) or session.get('is_admin')
    if not is_approver:
        return jsonify({'error': 'No permission to close'}), 403
    data = request.json
    record_id = data.get('id')
    close_type = data.get('close_type')
    password = (data.get('password') or '').strip()
    if not record_id:
        return jsonify({'error': 'Missing id'}), 400
    if close_type not in ['Closed', 'Partial Close']:
        return jsonify({'error': 'Invalid close type'}), 400
    if not password:
        return jsonify({'error': 'Password is required'}), 400

    # Verify password server-side
    from database import get_db, get_cursor
    conn = get_db()
    cur = get_cursor(conn)
    cur.execute('SELECT id FROM users WHERE id=%s AND password=%s', [session.get('user_id'), password])
    user = cur.fetchone()
    conn.close()
    if not user:
        return jsonify({'error': 'Incorrect password'}), 403

    # Re-verify eligibility server-side
    eligibility = model.get_closure_eligibility(record_id)
    if not eligibility['eligible']:
        return jsonify({'error': 'Record not eligible for closure', 'missing': eligibility['missing']}), 400
    if close_type == 'Closed' and not eligibility['can_full_close']:
        return jsonify({'error': f"Operations total ({eligibility['ops_total']}) does not match BL total ({eligibility['bl_total']}) — use Partial Close instead"}), 400

    model.close_record(record_id, close_type, session.get('username'))
    # Queue notification to approver
    try:
        from database import get_module_config
        cfg = get_module_config('LDUD01')
        approver_email, approver_name = _get_user_email_by_id(cfg.get('approver_id'))
        if approver_email:
            _queue_mail(
                to_email=approver_email,
                to_name=approver_name,
                subject=f"[PORTMAN] LDUD01 Record #{record_id} — {close_type}",
                body_html=f"""<p>Hello {approver_name or 'Approver'},</p>
<p>LDUD01 record <strong>#{record_id}</strong> has been marked as
<strong>{close_type}</strong> by <strong>{session.get('username')}</strong>.</p>
<p>Please review in PORTMAN.</p>
<hr><p style="color:#888;font-size:11px;">Automated notification from PORTMAN.</p>""",
                module_code='LDUD01',
                ref_id=record_id,
            )
    except Exception:
        pass
    return jsonify({'doc_status': close_type})


@bp.route('/api/module/LDUD01/reopen', methods=['POST'])
@login_required
def reopen():
    config = get_module_config('LDUD01')
    is_approver = str(config.get('approver_id', '')) == str(session.get('user_id')) or session.get('is_admin')
    if not is_approver:
        return jsonify({'error': 'No permission to reopen'}), 403
    data = request.json
    record_id = data.get('id')
    comment = (data.get('comment') or '').strip()
    if not record_id:
        return jsonify({'error': 'Missing id'}), 400
    if not comment:
        return jsonify({'error': 'A reason is required when sending back to Draft'}), 400
    model.reopen_record(record_id, comment, session.get('username'))
    # Queue notification to the operator who last closed this record
    try:
        closer_email, closer_name = _get_closer_email(record_id)
        if closer_email:
            _queue_mail(
                to_email=closer_email,
                to_name=closer_name,
                subject=f"[PORTMAN] LDUD01 Record #{record_id} — Sent Back to Draft",
                body_html=f"""<p>Hello {closer_name or ''},</p>
<p>LDUD01 record <strong>#{record_id}</strong> has been <strong>sent back to Draft</strong>
by <strong>{session.get('username')}</strong>.</p>
<p><strong>Reason:</strong> {comment}</p>
<p>Please review and resubmit in PORTMAN.</p>
<hr><p style="color:#888;font-size:11px;">Automated notification from PORTMAN.</p>""",
                module_code='LDUD01',
                ref_id=record_id,
            )
    except Exception:
        pass
    return jsonify({'doc_status': 'Draft'})


@bp.route('/api/module/LDUD01/closure-log/<int:record_id>')
@login_required
def closure_log(record_id):
    return jsonify(model.get_closure_log(record_id))

@bp.route('/api/module/LDUD01/delete', methods=['POST'])
@login_required
def delete():
    perms = get_perms()
    if not perms.get('can_delete'):
        return jsonify({'error': 'No permission to delete'}), 403
    model.delete_header(request.json['id'])
    return jsonify({'success': True})

# Delays sub-table endpoints
@bp.route('/api/module/LDUD01/delays/<int:ldud_id>')
@login_required
def get_delays(ldud_id):
    return jsonify(model.get_delays(ldud_id))

@bp.route('/api/module/LDUD01/delays/save', methods=['POST'])
@login_required
def save_delay():
    perms = get_perms()
    if not perms.get('can_add') and not perms.get('can_edit'):
        return jsonify({'error': 'No permission'}), 403
    data = request.json
    row_id, total_mins, total_hrs = model.save_delay(data)
    return jsonify({'id': row_id, 'success': True, 'total_time_mins': total_mins, 'total_time_hrs': total_hrs})

@bp.route('/api/module/LDUD01/delays/delete', methods=['POST'])
@login_required
def delete_delay():
    perms = get_perms()
    if not perms.get('can_delete'):
        return jsonify({'error': 'No permission to delete'}), 403
    model.delete_delay(request.json['id'])
    return jsonify({'success': True})

# Barge Lines sub-table endpoints
@bp.route('/api/module/LDUD01/barge_lines/<int:ldud_id>')
@login_required
def get_barge_lines(ldud_id):
    return jsonify(model.get_barge_lines(ldud_id))

@bp.route('/api/module/LDUD01/barge_lines/save', methods=['POST'])
@login_required
def save_barge_line():
    perms = get_perms()
    if not perms.get('can_add') and not perms.get('can_edit'):
        return jsonify({'error': 'No permission'}), 403
    data = request.json
    row_id, trip_number = model.save_barge_line(data)
    return jsonify({'id': row_id, 'success': True, 'trip_number': trip_number})

@bp.route('/api/module/LDUD01/barge_lines/delete', methods=['POST'])
@login_required
def delete_barge_line():
    perms = get_perms()
    if not perms.get('can_delete'):
        return jsonify({'error': 'No permission to delete'}), 403
    model.delete_barge_line(request.json['id'])
    return jsonify({'success': True})

# Anchorage Recording sub-table endpoints
@bp.route('/api/module/LDUD01/anchorage/<int:ldud_id>')
@login_required
def get_anchorage(ldud_id):
    return jsonify(model.get_anchorage(ldud_id))

@bp.route('/api/module/LDUD01/anchorage/save', methods=['POST'])
@login_required
def save_anchorage():
    perms = get_perms()
    if not perms.get('can_add') and not perms.get('can_edit'):
        return jsonify({'error': 'No permission'}), 403
    row_id = model.save_anchorage(request.json)
    return jsonify({'id': row_id, 'success': True})

@bp.route('/api/module/LDUD01/anchorage/delete', methods=['POST'])
@login_required
def delete_anchorage():
    perms = get_perms()
    if not perms.get('can_delete'):
        return jsonify({'error': 'No permission to delete'}), 403
    model.delete_anchorage(request.json['id'])
    return jsonify({'success': True})

# Vessel Operations sub-table endpoints
@bp.route('/api/module/LDUD01/vessel_ops/<int:ldud_id>')
@login_required
def get_vessel_operations(ldud_id):
    return jsonify(model.get_vessel_operations(ldud_id))

@bp.route('/api/module/LDUD01/vessel_ops/save', methods=['POST'])
@login_required
def save_vessel_operation():
    perms = get_perms()
    if not perms.get('can_add') and not perms.get('can_edit'):
        return jsonify({'error': 'No permission'}), 403
    row_id = model.save_vessel_operation(request.json)
    return jsonify({'id': row_id, 'success': True})

@bp.route('/api/module/LDUD01/vessel_ops/delete', methods=['POST'])
@login_required
def delete_vessel_operation():
    perms = get_perms()
    if not perms.get('can_delete'):
        return jsonify({'error': 'No permission to delete'}), 403
    model.delete_vessel_operation(request.json['id'])
    return jsonify({'success': True})

# Barge Cleaning Lines sub-table endpoints
@bp.route('/api/module/LDUD01/barge_cleaning/<int:ldud_id>')
@login_required
def get_barge_cleaning(ldud_id):
    return jsonify(model.get_barge_cleaning(ldud_id))

@bp.route('/api/module/LDUD01/barge_cleaning/save', methods=['POST'])
@login_required
def save_barge_cleaning():
    perms = get_perms()
    if not perms.get('can_add') and not perms.get('can_edit'):
        return jsonify({'error': 'No permission'}), 403
    row_id = model.save_barge_cleaning(request.json)
    return jsonify({'id': row_id, 'success': True})

@bp.route('/api/module/LDUD01/barge_cleaning/delete', methods=['POST'])
@login_required
def delete_barge_cleaning():
    perms = get_perms()
    if not perms.get('can_delete'):
        return jsonify({'error': 'No permission to delete'}), 403
    model.delete_barge_cleaning(request.json['id'])
    return jsonify({'success': True})

# Hold Completion sub-table endpoints
@bp.route('/api/module/LDUD01/hold_completion/<int:ldud_id>')
@login_required
def get_hold_completion(ldud_id):
    return jsonify(model.get_hold_completion(ldud_id))

@bp.route('/api/module/LDUD01/hold_completion/save', methods=['POST'])
@login_required
def save_hold_completion():
    perms = get_perms()
    if not perms.get('can_add') and not perms.get('can_edit'):
        return jsonify({'error': 'No permission'}), 403
    data = request.json
    if data.get('commenced') and data.get('completed'):
        from datetime import datetime
        try:
            start = datetime.fromisoformat(data['commenced'].replace(' ', 'T'))
            end = datetime.fromisoformat(data['completed'].replace(' ', 'T'))
            if end <= start:
                return jsonify({'error': 'Completed must be after Commenced'}), 400
        except ValueError:
            pass
    row_id = model.save_hold_completion(data)
    return jsonify({'id': row_id, 'success': True})

@bp.route('/api/module/LDUD01/hold_completion/delete', methods=['POST'])
@login_required
def delete_hold_completion():
    perms = get_perms()
    if not perms.get('can_delete'):
        return jsonify({'error': 'No permission to delete'}), 403
    model.delete_hold_completion(request.json['id'])
    return jsonify({'success': True})

# Hold Cargo Config endpoints
@bp.route('/api/module/LDUD01/hold_cargo/<int:ldud_id>')
@login_required
def get_hold_cargo(ldud_id):
    return jsonify(model.get_hold_cargo(ldud_id))

@bp.route('/api/module/LDUD01/hold_cargo/save', methods=['POST'])
@login_required
def save_hold_cargo():
    perms = get_perms()
    if not perms.get('can_add') and not perms.get('can_edit'):
        return jsonify({'error': 'No permission'}), 403
    data = request.json
    model.save_hold_cargo(data['ldud_id'], data['hold_name'], data.get('cargo_name', ''))
    return jsonify({'success': True})
