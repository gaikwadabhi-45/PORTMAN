from flask import Blueprint, render_template, request, jsonify, session, redirect, url_for
from functools import wraps
from database import get_db, get_cursor, get_module_config
import re
import os

bp = Blueprint('AUD01', __name__, template_folder='.')
MODULE_CODE = 'AUD01'
MODULE_INFO = {'code': 'AUD01', 'name': 'Audit Logs'}

# Nginx combined log format:
# 127.0.0.1 - - [24/Apr/2026:10:00:00 +0530] "GET /module/LDUD01/ HTTP/1.1" 200 4321 "..." "..."
_NGINX_RE = re.compile(
    r'^(\S+)\s+\S+\s+\S+\s+\[([^\]]+)\]\s+"(\S+)\s+([^"]+)\s+HTTP[^"]*"\s+(\d+)\s+(\d+)'
)

# Static-asset extensions and path prefixes to ignore
_SKIP_EXTS = {'.png', '.jpg', '.jpeg', '.gif', '.ico', '.css', '.js', '.woff', '.woff2', '.ttf', '.svg', '.map'}
_SKIP_PREFIXES = ('/static/', '/favicon')

# Map URL prefixes to module codes/names
_MODULE_MAP = [
    ('/admin',          'ADMIN',    'Admin Panel'),
    ('/module/VC01',    'VC01',     'Vessel Creation'),
    ('/module/VCN01',   'VCN01',    'Vessel Call Number'),
    ('/module/LDUD01',  'LDUD01',   'Loading Unloading'),
    ('/module/MBC01',   'MBC01',    'MBC Operation'),
    ('/module/LUEU01',  'LUEU01',   'Load/Unload Equip Utilization'),
    ('/module/SRV01',   'SRV01',    'Service Recording'),
    ('/module/FIN01',   'FIN01',    'Billing'),
    ('/module/FINV01',  'FINV01',   'Invoicing'),
    ('/module/FCAM01',  'FCAM01',   'Customer Agreements'),
    ('/module/FSTM01',  'FSTM01',   'Service Type Master'),
    ('/module/FGRM01',  'FGRM01',   'GST Rate Master'),
    ('/module/FCRM01',  'FCRM01',   'Currency Master'),
    ('/module/FDCN01',  'FDCN01',   'Debit/Credit Note'),
    ('/module/FSAP01',  'FSAP01',   'SAP Integration'),
    ('/module/FLOG01',  'FLOG01',   'Integration Logs'),
    ('/module/VTM01',   'VTM01',    'Vessel Type Master'),
    ('/module/VCM01',   'VCM01',    'Vessel Country Master'),
    ('/module/VAM01',   'VAM01',    'Vessel Agent Master'),
    ('/module/VCUM01',  'VCUM01',   'Vessel Currency'),
    ('/module/VRT01',   'VRT01',    'Run Types'),
    ('/module/VDM01',   'VDM01',    'Vessel Delay Master'),
    ('/module/PDM01',   'PDM01',    'Port Delay Master'),
    ('/module/VCG01',   'VCG01',    'Cargo Master'),
    ('/module/VQM01',   'VQM01',    'Quantity UOM'),
    ('/module/VHO01',   'VHO01',    'Vessel Holds'),
    ('/module/VEM01',   'VEM01',    'Equipment Master'),
    ('/module/VBM01',   'VBM01',    'Barge Master'),
    ('/module/VSDM01',  'VSDM01',   'Vessel Stevedore Master'),
    ('/module/MBCM01',  'MBCM01',   'MBC Master'),
    ('/module/PBM01',   'PBM01',    'Port Berth Master'),
    ('/module/PPL01',   'PPL01',    'Port Payloader Master'),
    ('/module/CRM01',   'CRM01',    'Conveyor Route Master'),
    ('/module/VANM01',  'VANM01',   'Anchorage Master'),
    ('/module/VPM01',   'VPM01',    'Port Master'),
    ('/module/TM01',    'TM01',     'Tide Master'),
    ('/module/VCDS01',  'VCDS01',   'VCN Doc Series'),
    ('/module/MBCDS01', 'MBCDS01',  'MBC Doc Series'),
    ('/module/INVDS01', 'INVDS01',  'Invoice Doc Series'),
    ('/module/GSTCFG',  'GSTCFG',  'GST API Config'),
    ('/module/PSM01',   'PSM01',    'PSM'),
    ('/module/PSMM01',  'PSMM01',  'PSMM'),
    ('/module/PSOM01',  'PSOM01',  'PSOM'),
    ('/module/RP01',    'RP01',     'Reports'),
    ('/module/AUD01',   'AUD01',    'Audit Logs'),
    ('/api/',           'API',      'API Calls'),
    ('/login',          'AUTH',     'Authentication'),
    ('/logout',         'AUTH',     'Authentication'),
    ('/auth/',          'AUTH',     'Authentication'),
    ('/home',           'HOME',     'Home'),
]


def _detect_module(path):
    for prefix, code, name in _MODULE_MAP:
        if path.startswith(prefix):
            return code, name
    return 'OTHER', path


def _should_skip(path):
    if any(path.startswith(p) for p in _SKIP_PREFIXES):
        return True
    _, ext = os.path.splitext(path.split('?')[0])
    return ext.lower() in _SKIP_EXTS


def _get_log_path():
    cfg = get_module_config('AUD01') or {}
    return cfg.get('nginx_log_path', '').strip()


def _parse_nginx_log(path, limit=2000):
    if not path or not os.path.isfile(path):
        return [], {'error': f'Log file not found: {path}'}

    entries = []
    try:
        with open(path, 'r', encoding='utf-8', errors='replace') as f:
            lines = f.readlines()
    except Exception as e:
        return [], {'error': str(e)}

    for line in lines:
        line = line.strip()
        if not line:
            continue
        m = _NGINX_RE.match(line)
        if not m:
            continue
        ip, ts, method, path_qs, status, size = m.groups()
        path_only = path_qs.split('?')[0]
        if _should_skip(path_only):
            continue
        mod_code, mod_name = _detect_module(path_only)
        entries.append({
            'ip': ip,
            'timestamp': ts,
            'method': method,
            'path': path_qs,
            'status': int(status),
            'size': int(size),
            'module_code': mod_code,
            'module_name': mod_name,
        })

    # newest first, capped to limit
    entries.reverse()
    return entries[:limit], None


def _calc_stats(entries):
    from collections import Counter, defaultdict
    import re as _re

    status_counts = Counter(str(e['status']) for e in entries)
    module_counts = Counter(e['module_code'] for e in entries)
    ip_counts = Counter(e['ip'] for e in entries)
    method_counts = Counter(e['method'] for e in entries)

    # By date (group by date portion of timestamp like "24/Apr/2026")
    date_groups = defaultdict(Counter)
    for e in entries:
        date = e['timestamp'].split(':')[0]  # "24/Apr/2026"
        date_groups[date][str(e['status'])] += 1

    by_date = [{'date': d, **dict(counts)} for d, counts in sorted(date_groups.items())]

    return {
        'total': len(entries),
        'status_counts': dict(status_counts),
        'top_modules': module_counts.most_common(10),
        'top_ips': ip_counts.most_common(10),
        'method_counts': dict(method_counts),
        'by_date': by_date,
    }


def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated


@bp.route('/module/AUD01/')
@login_required
def view():
    if not session.get('is_admin'):
        return render_template('no_access.html'), 403
    return render_template('aud01.html')


@bp.route('/api/module/AUD01/data')
@login_required
def get_data():
    if not session.get('is_admin'):
        return jsonify({'error': 'Admin only'}), 403

    log_path = _get_log_path()
    entries, err = _parse_nginx_log(log_path)
    if err and not entries:
        return jsonify({'error': err.get('error', 'Unknown error')}), 500

    stats = _calc_stats(entries)
    return jsonify({
        'entries': entries,
        'stats': stats,
        'log_path': log_path,
    })
