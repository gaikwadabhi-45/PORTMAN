from flask import Blueprint, render_template, request, jsonify, session, redirect, url_for
from functools import wraps
from . import model

bp = Blueprint('SAPCFG', __name__, template_folder='.')


def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('login'))
        if not session.get('is_admin'):
            return redirect(url_for('home'))
        return f(*args, **kwargs)
    return decorated


@bp.route('/module/SAPCFG/')
@admin_required
def view():
    configs = model.get_all_configs()
    return render_template('sapcfg.html', configs=configs)


@bp.route('/api/module/SAPCFG/save', methods=['POST'])
@admin_required
def save():
    data = request.json
    row_id = model.save_config(data, session.get('user_id'))
    return jsonify({'success': True, 'id': row_id})


@bp.route('/api/module/SAPCFG/set-active', methods=['POST'])
@admin_required
def set_active():
    env = request.json.get('environment')
    model.set_active_env(env)
    return jsonify({'success': True})


@bp.route('/api/module/SAPCFG/test-connection', methods=['POST'])
@admin_required
def test_connection():
    data = request.json
    try:
        import requests as req
        token_url = data.get('token_url')
        client_id = data.get('client_id')
        client_secret = data.get('client_secret')
        if not all([token_url, client_id, client_secret]):
            return jsonify({'success': False, 'message': 'Missing token URL, client ID or secret'})
        resp = req.post(token_url, params={
            'client_id': client_id,
            'client_secret': client_secret,
            'grant_type': 'client_credentials'
        }, timeout=15)
        if resp.status_code == 200 and resp.json().get('access_token'):
            return jsonify({'success': True, 'message': f'Connected! Token expires in {resp.json().get("expires_in", "?")}s'})
        else:
            return jsonify({'success': False, 'message': f'HTTP {resp.status_code}: {resp.text[:200]}'})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)})
