from flask import render_template, session, redirect, url_for
from functools import wraps
from . import bp
from .vessel_sof import views as _vessel_sof_views  # noqa: registers vessel-sof routes on bp
from .mbc_sof import views as _mbc_sof_views        # noqa: registers mbc-sof routes on bp
from .mbc_master import views as _mbc_master_views  # noqa: registers mbc-master routes on bp
from .mbc_tat    import views as _mbc_tat_views     # noqa: registers mbc-tat routes on bp
from .vessel_discharged import views as _vessel_discharged_views  # noqa: registers vessel-discharged routes on bp
from .custom_report    import views as _custom_report_views       # noqa: registers custom-report routes on bp
from .dashboard        import views as _dashboard_views           # noqa: registers dashboard routes on bp
from .daily_ops         import views as _daily_ops_views        # noqa: registers daily-ops routes on bp
from .shift_report      import views as _shift_report_views    # noqa: registers shift-report routes on bp
from .barge_report      import views as _barge_report_views    # noqa: registers barge-report routes on bp

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated


@bp.route('/module/RP01/')
@login_required
def index():
    return render_template('rp01.html', username=session.get('username'))
