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

# Module name lookup by code (for /api/module/{CODE}/... attribution)
_MODULE_NAMES = {
    'VC01': 'Vessel Creation', 'VCN01': 'Vessel Call Number',
    'LDUD01': 'Loading Unloading', 'MBC01': 'MBC Operation',
    'LUEU01': 'Load/Unload Equip Utilization', 'SRV01': 'Service Recording',
    'FIN01': 'Billing', 'FINV01': 'Invoicing', 'FCAM01': 'Customer Agreements',
    'FSTM01': 'Service Type Master', 'FGRM01': 'GST Rate Master',
    'FCRM01': 'Currency Master', 'FDCN01': 'Debit/Credit Note',
    'FSAP01': 'SAP Integration', 'FLOG01': 'Integration Logs',
    'VTM01': 'Vessel Type Master', 'VCM01': 'Vessel Country Master',
    'VAM01': 'Vessel Agent Master', 'VCUM01': 'Vessel Currency',
    'VRT01': 'Run Types', 'VDM01': 'Vessel Delay Master',
    'PDM01': 'Port Delay Master', 'VCG01': 'Cargo Master',
    'VQM01': 'Quantity UOM', 'VHO01': 'Vessel Holds',
    'VEM01': 'Equipment Master', 'VBM01': 'Barge Master',
    'VSDM01': 'Vessel Stevedore Master', 'MBCM01': 'MBC Master',
    'PBM01': 'Port Berth Master', 'PPL01': 'Port Payloader Master',
    'CRM01': 'Conveyor Route Master', 'VANM01': 'Anchorage Master',
    'VPM01': 'Port Master', 'TM01': 'Tide Master',
    'VCDS01': 'VCN Doc Series', 'MBCDS01': 'MBC Doc Series',
    'INVDS01': 'Invoice Doc Series', 'GSTCFG': 'GST API Config',
    'PSM01': 'PSM', 'PSMM01': 'PSMM', 'PSOM01': 'PSOM',
    'RP01': 'Reports', 'AUD01': 'Audit Logs', 'ADMIN': 'Admin Panel',
}

# Regex to extract module code from /api/module/{CODE}/... paths
_API_MODULE_RE = re.compile(r'^/api/module/([A-Z0-9]+)(?:/|$)')

# Page-level URL prefix map (for /module/XXX/ and other top-level routes)
_PREFIX_MAP = [
    ('/admin',   'ADMIN', 'Admin Panel'),
    ('/module/', None,    None),          # handled by code extraction below
    ('/login',   'AUTH',  'Authentication'),
    ('/logout',  'AUTH',  'Authentication'),
    ('/auth/',   'AUTH',  'Authentication'),
    ('/home',    'HOME',  'Home'),
]

# Regex to extract module code from /module/{CODE}/... page paths
_PAGE_MODULE_RE = re.compile(r'^/module/([A-Z0-9]+)(?:/|$)')


def _detect_module(path):
    # /api/module/{CODE}/... → attribute to that module
    m = _API_MODULE_RE.match(path)
    if m:
        code = m.group(1)
        return code, _MODULE_NAMES.get(code, code)

    # /module/{CODE}/... page load
    m = _PAGE_MODULE_RE.match(path)
    if m:
        code = m.group(1)
        return code, _MODULE_NAMES.get(code, code)

    # /admin/... (including /admin/api/...)
    if path.startswith('/admin'):
        return 'ADMIN', 'Admin Panel'

    if path.startswith('/login') or path.startswith('/auth/'):
        return 'AUTH', 'Authentication'
    if path.startswith('/logout'):
        return 'AUTH', 'Authentication'
    if path.startswith('/home'):
        return 'HOME', 'Home'
    if path.startswith('/api/'):
        return 'APP', 'App API'

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
