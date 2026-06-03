import io
from functools import wraps
from flask import render_template, request, jsonify, session, redirect, url_for, Response

from .. import bp
from . import model


def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated


def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('login'))
        if not session.get('is_admin'):
            return jsonify({'error': 'Admin only'}), 403
        return f(*args, **kwargs)
    return decorated


@bp.route('/module/RP01/historical-data/')
@login_required
def historical_data_index():
    if not session.get('is_admin'):
        return render_template('no_access.html'), 403
    return render_template('historical_data/historical_data.html',
                           username=session.get('username'),
                           status=model.get_status())


@bp.route('/api/module/RP01/historical/template')
@admin_required
def historical_template():
    wb = model.build_template_workbook()
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return Response(
        buf.getvalue(),
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        headers={'Content-Disposition': 'attachment; filename="RP01_historical_template.xlsx"'},
    )


@bp.route('/api/module/RP01/historical/preview', methods=['POST'])
@admin_required
def historical_preview():
    f = request.files.get('file')
    if not f:
        return jsonify({'error': 'No file provided'}), 400
    rows, errors = model.parse_upload(f)
    masters = model.get_all_masters()
    recon = model.reconcile(rows, masters)
    return jsonify({'total_rows': len(rows), 'format_errors': errors,
                    'reconciliation': recon})


@bp.route('/api/module/RP01/historical/apply', methods=['POST'])
@admin_required
def historical_apply():
    f = request.files.get('file')
    if not f:
        return jsonify({'error': 'No file provided'}), 400
    rows, errors = model.parse_upload(f)
    if errors:
        return jsonify({'error': 'Fix format errors before applying',
                        'format_errors': errors}), 400
    inserted = model.replace_all(rows, session.get('user_id'))
    return jsonify({'inserted': inserted})
