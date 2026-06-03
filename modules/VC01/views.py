from flask import Blueprint, render_template, request, jsonify, session, redirect, url_for
from functools import wraps
import json
from . import model
from database import get_module_config, get_user_permissions

bp = Blueprint('VC01', __name__, template_folder='.')
MODULE_CODE = 'VC01'

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

@bp.route('/module/VC01/')
@login_required
def view():
    perms = get_perms()
    if not perms.get('can_read'):
        return render_template('no_access.html'), 403
    return render_template('vc01.html', username=session.get('username'), permissions=perms)

@bp.route('/api/module/VC01/data')
@login_required
def get_data():
    page = int(request.args.get('page', 1))
    size = int(request.args.get('size', 20))
    
    # Get and parse filters
    filters = None
    filters_param = request.args.get('filters', '')
    if filters_param:
        try:
            filters = json.loads(filters_param)
        except Exception as e:
            print(f"Filter parse error: {e}")
            filters = None
    
    data, total = model.get_data(page, size, filters)
    return jsonify({
        'data': data,
        'last_page': (total + size - 1) // size,
        'total': total
    })

@bp.route('/api/module/VC01/save', methods=['POST'])
@login_required
def save():
    data = request.json
    perms = get_perms()
    is_new = not data.get('id')

    # Check permissions
    if is_new and not perms.get('can_add'):
        return jsonify({'error': 'No permission to add'}), 403
    if not is_new and not perms.get('can_edit'):
        return jsonify({'error': 'No permission to edit'}), 403

    config = get_module_config('VC01')
    user_id = session.get('user_id')
    is_approver = str(config.get('approver_id')) == str(user_id)

    print(f"[VC01 Save] user_id={user_id}, approver_id={config.get('approver_id')}, is_approver={is_approver}, doc_status={data.get('doc_status')}")

    # Determine doc_status based on approval rules
    if is_approver:
        # Approver can set any status (Approved/Rejected/Pending)
        pass
    else:
        # Non-approvers: new entries are always Pending
        if is_new:
            data['doc_status'] = 'Pending'
        elif config.get('approval_edit'):
            # Edits require re-approval if configured
            data['doc_status'] = 'Pending'

    row_id, doc_num = model.save_data(data)
    print(f"[VC01 Save] Saved row_id={row_id}, doc_num={doc_num}, final_doc_status={data.get('doc_status')}")
    return jsonify({'success': True, 'id': row_id, 'doc_num': doc_num, 'doc_status': data.get('doc_status')})

@bp.route('/api/module/VC01/delete', methods=['POST'])
@login_required
def delete():
    perms = get_perms()
    if not perms.get('can_delete'):
        return jsonify({'error': 'No permission to delete'}), 403

    row_id = request.json.get('id')
    model.delete_data(row_id)
    return jsonify({'success': True})
