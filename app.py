from flask import Flask, render_template, request, redirect, url_for, session, jsonify
from functools import wraps
from database import get_db, get_cursor
from config import SECRET_KEY, FLASK_ENV, SERVER_HOST, SERVER_PORT, SSL_CERT, SSL_KEY

app = Flask(__name__)
app.secret_key = SECRET_KEY

# Module registry
MODULES = {}

def register_module(code, name, blueprint):
    MODULES[code] = {'name': name}
    app.register_blueprint(blueprint)

# Import and register existing modules
from modules.VC01 import bp as vc01_bp, MODULE_INFO as vc01_info
from modules.VTM01 import bp as vtm01_bp, MODULE_INFO as vtm01_info
from modules.VCM01 import bp as vcm01_bp, MODULE_INFO as vcm01_info
from modules.VFM01 import bp as vfm01_bp, MODULE_INFO as vfm01_info
from modules.GM01 import bp as gm01_bp, MODULE_INFO as gm01_info
from modules.ADMIN import bp as admin_bp, MODULE_INFO as admin_info

# Import new master modules
from modules.VAM01 import bp as vam01_bp, MODULE_INFO as vam01_info
from modules.VCUM01 import bp as vcum01_bp, MODULE_INFO as vcum01_info
from modules.VCDS01 import bp as vcds01_bp, MODULE_INFO as vcds01_info
from modules.VTOD01 import bp as vtod01_bp, MODULE_INFO as vtod01_info
from modules.VRT01 import bp as vrt01_bp, MODULE_INFO as vrt01_info
from modules.VDM01 import bp as vdm01_bp, MODULE_INFO as vdm01_info
from modules.VCG01 import bp as vcg01_bp, MODULE_INFO as vcg01_info
from modules.VCN01 import bp as vcn01_bp, MODULE_INFO as vcn01_info
from modules.VQM01 import bp as vqm01_bp, MODULE_INFO as vqm01_info

from modules.VHO01 import bp as vho01_bp, MODULE_INFO as vho01_info
from modules.PDM01 import bp as pdm01_bp, MODULE_INFO as pdm01_info
from modules.VEM01 import bp as vem01_bp, MODULE_INFO as vem01_info
from modules.VBM01 import bp as vbm01_bp, MODULE_INFO as vbm01_info
from modules.VSDM01 import bp as vsdm01_bp, MODULE_INFO as vsdm01_info
from modules.LDUD01 import bp as ldud01_bp, MODULE_INFO as ldud01_info
from modules.MBCM01 import bp as mbcm01_bp, MODULE_INFO as mbcm01_info
from modules.PBM01 import bp as pbm01_bp, MODULE_INFO as pbm01_info
from modules.MBCDS01 import bp as mbcds01_bp, MODULE_INFO as mbcds01_info
from modules.INVDS01 import bp as invds01_bp, MODULE_INFO as invds01_info
from modules.MBC01 import bp as mbc01_bp, MODULE_INFO as mbc01_info
from modules.PPL01 import bp as ppl01_bp, MODULE_INFO as ppl01_info
from modules.LUEU01 import bp as eu01_bp, MODULE_INFO as eu01_info
from modules.CRM01 import bp as crm01_bp, MODULE_INFO as crm01_info

# Import finance modules
from modules.FCRM01 import bp as fcrm01_bp, MODULE_INFO as fcrm01_info
from modules.FGRM01 import bp as fgrm01_bp, MODULE_INFO as fgrm01_info
from modules.FSTM01 import bp as fstm01_bp, MODULE_INFO as fstm01_info
from modules.FCAM01 import bp as fcam01_bp, MODULE_INFO as fcam01_info
from modules.FIN01 import bp as fin01_bp, MODULE_INFO as fin01_info
from modules.SRV01 import bp as srv01_bp, MODULE_INFO as srv01_info
from modules.VANM01 import bp as vanm01_bp, MODULE_INFO as vanm01_info
from modules.VPM01 import bp as vpm01_bp, MODULE_INFO as vpm01_info
from modules.TM01 import bp as tm01_bp, MODULE_INFO as tm01_info
from modules.PSM01 import bp as psm01_bp, MODULE_INFO as psm01_info
from modules.PSMM01 import bp as psmm01_bp, MODULE_INFO as psmm01_info
from modules.PSOM01 import bp as psom01_bp, MODULE_INFO as psom01_info

# Import reports module
from modules.RP01 import bp as rp01_bp, MODULE_INFO as rp01_info

# Import accounts redesign modules
from modules.FINV01 import bp as finv01_bp, MODULE_INFO as finv01_info
from modules.SAPCFG import bp as sapcfg_bp, MODULE_INFO as sapcfg_info
from modules.GSTCFG import bp as gstcfg_bp, MODULE_INFO as gstcfg_info
from modules.FSAP01 import bp as fsap01_bp, MODULE_INFO as fsap01_info
from modules.FLOG01 import bp as flog01_bp, MODULE_INFO as flog01_info
from modules.FDCN01 import bp as fdcn01_bp, MODULE_INFO as fdcn01_info

# Register existing modules
register_module(vc01_info['code'], vc01_info['name'], vc01_bp)
register_module(vtm01_info['code'], vtm01_info['name'], vtm01_bp)
register_module(vcm01_info['code'], vcm01_info['name'], vcm01_bp)
register_module(vfm01_info['code'], vfm01_info['name'], vfm01_bp)
register_module(gm01_info['code'], gm01_info['name'], gm01_bp)
register_module(admin_info['code'], admin_info['name'], admin_bp)

# Register new master modules
register_module(vam01_info['code'], vam01_info['name'], vam01_bp)
register_module(vcum01_info['code'], vcum01_info['name'], vcum01_bp)
register_module(vcds01_info['code'], vcds01_info['name'], vcds01_bp)
register_module(vtod01_info['code'], vtod01_info['name'], vtod01_bp)
register_module(vrt01_info['code'], vrt01_info['name'], vrt01_bp)
register_module(vdm01_info['code'], vdm01_info['name'], vdm01_bp)
register_module(vcg01_info['code'], vcg01_info['name'], vcg01_bp)
register_module(vcn01_info['code'], vcn01_info['name'], vcn01_bp)
register_module(vqm01_info['code'], vqm01_info['name'], vqm01_bp)

register_module(vho01_info['code'], vho01_info['name'], vho01_bp)
register_module(pdm01_info['code'], pdm01_info['name'], pdm01_bp)
register_module(vem01_info['code'], vem01_info['name'], vem01_bp)
register_module(vbm01_info['code'], vbm01_info['name'], vbm01_bp)
register_module(vsdm01_info['code'], vsdm01_info['name'], vsdm01_bp)
register_module(ldud01_info['code'], ldud01_info['name'], ldud01_bp)
register_module(mbcm01_info['code'], mbcm01_info['name'], mbcm01_bp)
register_module(pbm01_info['code'], pbm01_info['name'], pbm01_bp)
register_module(mbcds01_info['code'], mbcds01_info['name'], mbcds01_bp)
register_module(invds01_info['code'], invds01_info['name'], invds01_bp)
register_module(mbc01_info['code'], mbc01_info['name'], mbc01_bp)
register_module(ppl01_info['code'], ppl01_info['name'], ppl01_bp)
register_module(eu01_info['code'], eu01_info['name'], eu01_bp)
register_module(crm01_info['code'], crm01_info['name'], crm01_bp)
# Register finance modules
register_module(fcrm01_info['code'], fcrm01_info['name'], fcrm01_bp)
register_module(fgrm01_info['code'], fgrm01_info['name'], fgrm01_bp)
register_module(fstm01_info['code'], fstm01_info['name'], fstm01_bp)
register_module(fcam01_info['code'], fcam01_info['name'], fcam01_bp)
register_module(fin01_info['code'], fin01_info['name'], fin01_bp)
register_module(srv01_info['code'], srv01_info['name'], srv01_bp)
register_module(vanm01_info['code'], vanm01_info['name'], vanm01_bp)
register_module(vpm01_info['code'], vpm01_info['name'], vpm01_bp)
register_module(tm01_info['code'], tm01_info['name'], tm01_bp)
register_module(psm01_info['code'], psm01_info['name'], psm01_bp)
register_module(psmm01_info['code'], psmm01_info['name'], psmm01_bp)
register_module(psom01_info['code'], psom01_info['name'], psom01_bp)

# Register reports module
register_module(rp01_info['code'], rp01_info['name'], rp01_bp)

# Register accounts redesign modules
register_module(finv01_info['code'], finv01_info['name'], finv01_bp)
register_module(sapcfg_info['code'], sapcfg_info['name'], sapcfg_bp)
register_module(gstcfg_info['code'], gstcfg_info['name'], gstcfg_bp)
register_module(fsap01_info['code'], fsap01_info['name'], fsap01_bp)
register_module(flog01_info['code'], flog01_info['name'], flog01_bp)
register_module(fdcn01_info['code'], fdcn01_info['name'], fdcn01_bp)

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated

@app.route('/')
def index():
    return redirect(url_for('login'))

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        conn = get_db()
        cur = get_cursor(conn)
        cur.execute('SELECT * FROM users WHERE username = %s AND password = %s',
                       (username, password))
        user = cur.fetchone()
        conn.close()
        if user:
            session['user_id'] = user['id']
            session['username'] = user['username']
            session['is_admin'] = bool(user['is_admin'])
            return redirect(url_for('home'))
        return render_template('login.html', error='Invalid credentials')
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

@app.route('/home')
@login_required
def home():
    return render_template('home.html', modules=MODULES, username=session.get('username'), is_admin=session.get('is_admin'))

@app.route('/api/modules/search')
@login_required
def search_modules():
    query = request.args.get('q', '').lower()
    results = [{'code': k, 'name': v['name']} for k, v in MODULES.items()
               if query in k.lower() or query in v['name'].lower()]
    return jsonify(results)

if __name__ == '__main__':
    is_production = FLASK_ENV == 'production'

    ssl_context = None
    if is_production:
        if not SSL_CERT or not SSL_KEY:
            raise RuntimeError(
                "SSL_CERT and SSL_KEY must be set in the environment for production mode."
            )
        ssl_context = (SSL_CERT, SSL_KEY)

    app.run(
        host=SERVER_HOST,
        port=SERVER_PORT,
        debug=not is_production,
        threaded=True,
        ssl_context=ssl_context,
    )
