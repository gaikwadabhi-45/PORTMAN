from flask import render_template, session, redirect, url_for
from functools import wraps

from .. import bp


def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated


@bp.route('/module/RP01/dashboard/')
@login_required
def dashboard_index():
    return render_template('dashboard/dashboard.html',
                           username=session.get('username'))
