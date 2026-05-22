from flask import render_template, session, redirect, url_for, jsonify
from functools import wraps
from datetime import datetime, date, timedelta

from .. import bp
from database import get_db, get_cursor


def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated


@bp.route('/module/RP01/live-dashboard/')
@login_required
def live_dashboard_index():
    return render_template('live_dashboard/live_dashboard.html',
                           username=session.get('username'))


@bp.route('/api/module/RP01/live-dashboard/data')
@login_required
def live_dashboard_data():
    conn = get_db()
    cur = get_cursor(conn)

    today = date.today()
    now = datetime.now()
    h = now.hour

    # Port runs 7am–7am: before 7am we're still in the previous operational day
    if h < 7:
        ops_date = today - timedelta(days=1)
    else:
        ops_date = today

    ops_today_s     = ops_date.strftime('%Y-%m-%d')
    ops_yesterday_s = (ops_date - timedelta(days=1)).strftime('%Y-%m-%d')
    month_start_s   = ops_date.replace(day=1).strftime('%Y-%m-%d')

    # Shift: A=06–14, B=14–22, C=22–06 (matches LUEU01 logic)
    if 6 <= h < 14:
        current_shift = 'A'
    elif 14 <= h < 22:
        current_shift = 'B'
    else:
        current_shift = 'C'

    # ── Berths (alphabetical) ───────────────────────────────────────────────
    cur.execute('SELECT berth_name FROM port_berth_master ORDER BY berth_name')
    berths_raw = [r['berth_name'] for r in cur.fetchall()]

    # ── Active (source, barge) combinations per berth today ─────────────────
    # VCN entries without a barge_name are excluded — the vessel stays at
    # anchorage; only barges (and MBCs) physically come to the berth.
    cur.execute('''
        SELECT
            berth_name,
            source_type,
            source_id,
            MAX(source_display)           AS source_display,
            COALESCE(barge_name, '')      AS barge_name,
            COALESCE(MAX(cargo_name), '') AS cargo_name
        FROM lueu_lines
        WHERE entry_date = %s AND (is_deleted IS NOT TRUE)
          AND berth_name IS NOT NULL AND berth_name != ''
          AND (
              source_type = 'MBC'
              OR (source_type = 'VCN' AND barge_name IS NOT NULL AND barge_name != '')
              OR (source_type NOT IN ('VCN','MBC'))
          )
        GROUP BY berth_name, source_type, source_id, barge_name
        ORDER BY berth_name, barge_name
    ''', [ops_today_s])
    berth_sources_raw = cur.fetchall()

    berth_sources = {}
    for r in berth_sources_raw:
        bn = r['berth_name']
        berth_sources.setdefault(bn, []).append({
            'source_type':    r['source_type'],
            'source_id':      r['source_id'],
            'source_display': r['source_display'],
            'barge_name':     r['barge_name'],
            'cargo_name':     r['cargo_name'],
        })

    # ── Latest equipment per (source, barge, berth) today ──────────────────
    cur.execute('''
        SELECT DISTINCT ON (source_type, source_id, barge_name, berth_name)
            source_type, source_id,
            COALESCE(barge_name, '') AS barge_name,
            berth_name,
            equipment_name, shift_incharge, shift
        FROM lueu_lines
        WHERE entry_date = %s AND (is_deleted IS NOT TRUE)
          AND berth_name IS NOT NULL AND berth_name != ''
          AND equipment_name IS NOT NULL AND equipment_name != ''
        ORDER BY source_type, source_id, barge_name, berth_name, id DESC
    ''', [ops_today_s])
    latest_equipment = {
        (r['source_type'], r['source_id'], r['barge_name'], r['berth_name']): {
            'equipment_name': r['equipment_name'],
            'shift_incharge': r['shift_incharge'],
            'shift':          r['shift'],
        }
        for r in cur.fetchall()
    }

    # ── Routes per (source, barge, berth) today ─────────────────────────────
    cur.execute('''
        SELECT
            source_type, source_id,
            COALESCE(barge_name, '')          AS barge_name,
            berth_name,
            COALESCE(route_name, 'Unknown')   AS route_name,
            COALESCE(SUM(quantity), 0)        AS qty
        FROM lueu_lines
        WHERE entry_date = %s AND (is_deleted IS NOT TRUE)
          AND berth_name IS NOT NULL AND berth_name != ''
        GROUP BY source_type, source_id, barge_name, berth_name, route_name
        ORDER BY qty DESC
    ''', [ops_today_s])
    source_routes = {}
    for r in cur.fetchall():
        key = (r['source_type'], r['source_id'], r['barge_name'], r['berth_name'])
        source_routes.setdefault(key, []).append({
            'route': r['route_name'],
            'qty':   round(float(r['qty'] or 0), 2),
        })

    # ── Actual handled per (source, barge, berth) — all time ────────────────
    cur.execute('''
        SELECT
            source_type, source_id,
            COALESCE(barge_name, '') AS barge_name,
            berth_name,
            COALESCE(SUM(quantity), 0) AS actual
        FROM lueu_lines
        WHERE is_deleted IS NOT TRUE
          AND berth_name IS NOT NULL AND berth_name != ''
        GROUP BY source_type, source_id, barge_name, berth_name
    ''')
    barge_actual = {
        (r['source_type'], r['source_id'], r['barge_name'], r['berth_name']): float(r['actual'] or 0)
        for r in cur.fetchall()
    }

    # ── Barge BL from ldud_barge_lines (for VCN sources) ────────────────────
    # Each barge trip has a discharge_quantity — use sum per (vcn_id, barge_name)
    cur.execute('''
        SELECT
            lh.vcn_id           AS vcn_id,
            lbl.barge_name,
            COALESCE(SUM(lbl.discharge_quantity), 0) AS barge_qty
        FROM ldud_barge_lines lbl
        JOIN ldud_header lh ON lh.id = lbl.ldud_id
        WHERE lbl.discharge_quantity IS NOT NULL
          AND lbl.discharge_quantity > 0
          AND lbl.barge_name IS NOT NULL AND lbl.barge_name != ''
        GROUP BY lh.vcn_id, lbl.barge_name
    ''')
    barge_bl = {
        (r['vcn_id'], r['barge_name']): float(r['barge_qty'] or 0)
        for r in cur.fetchall()
    }

    # ── MBC BL (MBC itself comes to berth) ──────────────────────────────────
    cur.execute('''
        SELECT m.id,
            CASE WHEN COUNT(cd.id) > 0
                 THEN COALESCE(SUM(cd.quantity), 0)
                 ELSE COALESCE(m.bl_quantity, 0)
            END AS bl_qty
        FROM mbc_header m
        LEFT JOIN mbc_customer_details cd ON cd.mbc_id = m.id
        WHERE m.doc_status != 'Closed'
        GROUP BY m.id, m.bl_quantity
    ''')
    mbc_bl = {r['id']: float(r['bl_qty'] or 0) for r in cur.fetchall()}

    # ── Build berth data ─────────────────────────────────────────────────────
    berths = []
    for berth_name in berths_raw:
        sources = berth_sources.get(berth_name, [])
        vessels = []
        for s in sources:
            st, sid = s['source_type'], s['source_id']
            barge_name = s['barge_name']
            key_eq    = (st, sid, barge_name, berth_name)
            eq_info   = latest_equipment.get(key_eq, {})
            routes    = source_routes.get(key_eq, [])
            actual    = barge_actual.get(key_eq, 0)

            # BL: for VCN with barge → ldud_barge_lines; for MBC → mbc bl
            if st == 'MBC':
                bl = mbc_bl.get(sid, 0)
            elif barge_name:
                bl = barge_bl.get((sid, barge_name), 0)
            else:
                bl = 0  # VCN direct (no barge assigned yet)

            pct = round((actual / bl * 100) if bl > 0 else 0, 1)
            pct = min(pct, 100)

            vessels.append({
                'source_type':    st,
                'source_display': s['source_display'] or '',
                'barge_name':     barge_name,
                'cargo_name':     s['cargo_name'],
                'equipment':      eq_info.get('equipment_name', ''),
                'shift':          eq_info.get('shift', ''),
                'shift_incharge': eq_info.get('shift_incharge', ''),
                'bl_quantity':    round(bl, 2),
                'actual':         round(actual, 2),
                'pct':            pct,
                'routes':         routes,
            })
        berths.append({
            'berth_name': berth_name,
            'vessels':    vessels,
            'active':     len(vessels) > 0,
        })

    # ── Shift incharge (most frequent in current shift today) ────────────────
    cur.execute('''
        SELECT shift_incharge, COUNT(*) AS cnt
        FROM lueu_lines
        WHERE entry_date = %s AND (is_deleted IS NOT TRUE)
          AND shift = %s
          AND shift_incharge IS NOT NULL AND shift_incharge != ''
        GROUP BY shift_incharge
        ORDER BY cnt DESC LIMIT 1
    ''', [ops_today_s, current_shift])
    si_row = cur.fetchone()
    shift_incharge = si_row['shift_incharge'] if si_row else '—'

    # ── Today's shift breakdown ──────────────────────────────────────────────
    cur.execute('''
        SELECT shift, COALESCE(SUM(quantity), 0) AS tonnes
        FROM lueu_lines
        WHERE entry_date = %s AND (is_deleted IS NOT TRUE)
          AND shift IS NOT NULL AND shift != ''
        GROUP BY shift ORDER BY shift
    ''', [ops_today_s])
    today_shifts = {r['shift']: round(float(r['tonnes'] or 0), 2)
                    for r in cur.fetchall()}

    # ── Yesterday's shift breakdown ──────────────────────────────────────────
    cur.execute('''
        SELECT shift,
               COALESCE(SUM(quantity), 0) AS tonnes,
               COUNT(*)                   AS ops
        FROM lueu_lines
        WHERE entry_date = %s AND (is_deleted IS NOT TRUE)
          AND shift IS NOT NULL AND shift != ''
        GROUP BY shift ORDER BY shift
    ''', [ops_yesterday_s])
    yesterday_shifts = {
        r['shift']: {'tonnes': round(float(r['tonnes'] or 0), 2), 'ops': int(r['ops'] or 0)}
        for r in cur.fetchall()
    }

    # ── Route performance today ──────────────────────────────────────────────
    cur.execute('''
        SELECT COALESCE(route_name, 'Unknown') AS route_name,
               COALESCE(SUM(quantity), 0)      AS qty
        FROM lueu_lines
        WHERE entry_date = %s AND (is_deleted IS NOT TRUE)
        GROUP BY route_name
        ORDER BY qty DESC
    ''', [ops_today_s])
    route_perf = [{'route': r['route_name'], 'qty': round(float(r['qty'] or 0), 2)}
                  for r in cur.fetchall()]

    # ── Equipment performance today ──────────────────────────────────────────
    cur.execute('''
        SELECT equipment_name,
               COALESCE(SUM(quantity), 0) AS qty,
               COUNT(*)                   AS ops
        FROM lueu_lines
        WHERE entry_date = %s AND (is_deleted IS NOT TRUE)
          AND equipment_name IS NOT NULL AND equipment_name != ''
        GROUP BY equipment_name
        ORDER BY qty DESC
    ''', [ops_today_s])
    equipment_perf = [
        {'equipment': r['equipment_name'], 'qty': round(float(r['qty'] or 0), 2), 'ops': int(r['ops'] or 0)}
        for r in cur.fetchall()
    ]

    # ── Tide (last 32 h) ─────────────────────────────────────────────────────
    tide_from = (now - timedelta(hours=32)).strftime('%Y-%m-%dT%H:%M:%S')
    cur.execute('''
        SELECT tide_datetime, tide_meters
        FROM tide_master
        WHERE tide_datetime >= %s
        ORDER BY tide_datetime
    ''', [tide_from])
    tides = [{'dt': str(r['tide_datetime']), 'h': float(r['tide_meters'] or 0)}
             for r in cur.fetchall()]

    # ── KPIs ─────────────────────────────────────────────────────────────────
    cur.execute('''
        SELECT COALESCE(SUM(quantity), 0) AS mtd
        FROM lueu_lines
        WHERE entry_date >= %s AND entry_date <= %s
          AND (is_deleted IS NOT TRUE)
    ''', [month_start_s, ops_today_s])
    mtd = round(float(cur.fetchone()['mtd'] or 0), 2)

    cur.execute('''
        SELECT COALESCE(SUM(quantity), 0) AS today_total
        FROM lueu_lines
        WHERE entry_date = %s AND (is_deleted IS NOT TRUE)
    ''', [ops_today_s])
    today_total = round(float(cur.fetchone()['today_total'] or 0), 2)

    active_count = sum(len(b['vessels']) for b in berths)

    conn.close()

    return jsonify({
        'berths':           berths,
        'current_shift':    current_shift,
        'shift_incharge':   shift_incharge,
        'today_shifts':     today_shifts,
        'yesterday_shifts': yesterday_shifts,
        'route_perf':       route_perf,
        'equipment_perf':   equipment_perf,
        'tides':            tides,
        'mtd':              mtd,
        'today_total':      today_total,
        'active_sources':   active_count,
        'ops_date':         ops_today_s,
        'as_of':            now.strftime('%Y-%m-%d %H:%M:%S'),
    })
