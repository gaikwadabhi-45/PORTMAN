import math as _math

from flask import render_template, session, redirect, url_for, jsonify
from functools import wraps
from datetime import datetime, date, timedelta

def _bearing(lat1, lon1, lat2, lon2):
    """Great-circle bearing (°, 0=N clockwise) from point-1 to point-2."""
    r1, o1, r2, o2 = map(_math.radians, [lat1, lon1, lat2, lon2])
    x = _math.sin(o2 - o1) * _math.cos(r2)
    y = _math.cos(r1)*_math.sin(r2) - _math.sin(r1)*_math.cos(r2)*_math.cos(o2 - o1)
    return (_math.degrees(_math.atan2(x, y)) + 360) % 360

def _ang_diff(a, b):
    d = abs(a - b) % 360
    return d if d <= 180 else 360 - d

# Berth alignment line (lat, lon) — runs NNW→SSE along the port berth face.
# Barge icons align along this tangent; MBC icons face perpendicular toward channel.
_BERTH_LINE = [
    (18.714818171878463, 73.02094596215585),
    (18.712901266818648, 73.02293947956420),
    (18.710932950718330, 73.02435357541219),
    (18.709178365309256, 73.02578767430785),
    (18.706735123742760, 73.02683431310976),
    (18.704735312868834, 73.02751933948753),
]

def _berth_bearings(lat, lon):
    """Return (berth_tangent_bearing, channel_facing_bearing) for a berth at (lat, lon).

    berth_tangent: bearing along the nearest segment of _BERTH_LINE (barge long-axis)
    channel_facing: perpendicular direction toward the water/channel (MBC bow)
    """
    best_d, best_brg = float('inf'), 0.0
    for i in range(len(_BERTH_LINE) - 1):
        mlat = (_BERTH_LINE[i][0] + _BERTH_LINE[i+1][0]) / 2
        mlon = (_BERTH_LINE[i][1] + _BERTH_LINE[i+1][1]) / 2
        d = (lat - mlat)**2 + (lon - mlon)**2
        if d < best_d:
            best_d   = d
            best_brg = _bearing(_BERTH_LINE[i][0], _BERTH_LINE[i][1],
                                 _BERTH_LINE[i+1][0], _BERTH_LINE[i+1][1])
    # Two perpendicular candidates; pick the one facing west (toward channel ~270°)
    p1 = (best_brg + 90) % 360
    p2 = (best_brg - 90 + 360) % 360
    ch_brg = p1 if _ang_diff(p1, 270) <= _ang_diff(p2, 270) else p2
    return round(best_brg, 1), round(ch_brg, 1)

from .. import bp
from database import get_db, get_cursor


def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated


@bp.route('/module/RP01/port-map/')
@login_required
def port_map_index():
    return render_template('port_map/port_map.html', username=session.get('username'))


@bp.route('/api/module/RP01/port-map/data')
@login_required
def port_map_data():
    conn = get_db()
    cur  = get_cursor(conn)
    now  = datetime.now()
    today_s = date.today().strftime('%Y-%m-%d')
    if now.hour < 7:
        today_s = (date.today() - timedelta(days=1)).strftime('%Y-%m-%d')

    # ── Anchorages (master list with coordinates) ────────────────────────────
    cur.execute('''
        SELECT id, name,
               NULLIF(latitude::text,  '')::float AS lat,
               NULLIF(longitude::text, '')::float AS lon
        FROM anchorage_master
        WHERE latitude  IS NOT NULL AND latitude::text  != ''
          AND longitude IS NOT NULL AND longitude::text != ''
        ORDER BY name
    ''')
    anchorage_coords = {r['name']: {'lat': r['lat'], 'lon': r['lon'], 'id': r['id']}
                        for r in cur.fetchall()}

    # ── Waypoints ────────────────────────────────────────────────────────────
    cur.execute('''
        SELECT name, latitude::float AS lat, longitude::float AS lon, waypoint_type
        FROM port_waypoints ORDER BY name
    ''')
    waypoints = [{'name': r['name'], 'lat': r['lat'], 'lon': r['lon'], 'type': r['waypoint_type']}
                 for r in cur.fetchall()]

    # Index waypoints by name for position look-up
    wp = {w['name']: w for w in waypoints}
    gull_lat  = wp.get('Gull Island', {}).get('lat',  18.834000)
    gull_lon  = wp.get('Gull Island', {}).get('lon',  72.896800)

    # ── Berths with coordinates ───────────────────────────────────────────────
    # lat/lon/berth_sequence columns added by migration; fall back gracefully
    try:
        cur.execute('''
            SELECT id, berth_name,
                   berth_location, remarks,
                   latitude::float  AS lat,
                   longitude::float AS lon,
                   berth_sequence
            FROM port_berth_master
            ORDER BY berth_sequence NULLS LAST, berth_name
        ''')
    except Exception:
        conn.rollback()
        cur.execute('''
            SELECT id, berth_name,
                   berth_location, remarks,
                   NULL::float AS lat,
                   NULL::float AS lon,
                   0           AS berth_sequence
            FROM port_berth_master
            ORDER BY berth_name
        ''')
    berth_rows = cur.fetchall()
    berth_coords = {r['berth_name']: {'lat': r['lat'], 'lon': r['lon'],
                                       'seq': r['berth_sequence']}
                    for r in berth_rows if r['lat'] is not None}

    # ── Vessels currently at anchor: sourced from ldud_anchorage (discharge    ──
    # anchorage recording sub-table of LDUD01).                                 ──
    # A vessel is "at anchor" when it has an ldud_anchorage row with             ──
    # anchored != '' and anchor_aweigh is null/empty (hasn't weighed anchor).   ──
    # vcn_anchorage tracks port entry/exit; ldud_anchorage tracks discharge.    ──
    cur.execute('''
        SELECT DISTINCT ON (lh.vcn_id)
            vh.id            AS vcn_id,
            vh.vessel_name,
            vh.vcn_doc_num,
            lh.id            AS ldud_id,
            lh.doc_num       AS ldud_doc_num,
            la.anchorage_name,
            la.anchored      AS anchored_time,
            la.cargo_name    AS anch_cargo
        FROM ldud_anchorage la
        JOIN ldud_header lh ON lh.id = la.ldud_id
        JOIN vcn_header  vh ON vh.id = lh.vcn_id
        WHERE lh.doc_status NOT IN ('Closed')
          AND la.anchored IS NOT NULL
          AND la.anchor_aweigh IS NULL
        ORDER BY lh.vcn_id, la.anchored DESC
    ''')
    anchored_vessels_raw = cur.fetchall()

    anchored_vcn_ids  = list({r['vcn_id']  for r in anchored_vessels_raw if r['vcn_id']})
    anchored_ldud_ids = list({r['ldud_id'] for r in anchored_vessels_raw if r['ldud_id']})

    # Batch-fetch BL and ops progress for enrichment
    bl_map  = {}
    ops_map = {}
    cargo_map = {}
    if anchored_vcn_ids:
        cur.execute('''SELECT vcn_id, COALESCE(SUM(bl_quantity),0) AS bl_total
                       FROM vcn_cargo_declaration
                       WHERE vcn_id = ANY(%s) GROUP BY vcn_id''', (anchored_vcn_ids,))
        bl_map = {r['vcn_id']: float(r['bl_total']) for r in cur.fetchall()}

        cur.execute('''SELECT vcn_id, COALESCE(SUM(bl_quantity),0) AS bl_total
                       FROM vcn_export_cargo_declaration
                       WHERE vcn_id = ANY(%s) GROUP BY vcn_id''', (anchored_vcn_ids,))
        for r in cur.fetchall():
            bl_map[r['vcn_id']] = bl_map.get(r['vcn_id'], 0) + float(r['bl_total'])

    if anchored_ldud_ids:
        cur.execute('''SELECT ldud_id, COALESCE(SUM(quantity),0) AS ops_total
                       FROM ldud_vessel_operations
                       WHERE ldud_id = ANY(%s) GROUP BY ldud_id''', (anchored_ldud_ids,))
        ops_map = {r['ldud_id']: float(r['ops_total']) for r in cur.fetchall()}

        cur.execute('''SELECT ldud_id, STRING_AGG(DISTINCT cargo_name, ', ') AS cargo_str
                       FROM ldud_anchorage
                       WHERE ldud_id = ANY(%s) AND cargo_name IS NOT NULL
                       GROUP BY ldud_id''', (anchored_ldud_ids,))
        cargo_map = {r['ldud_id']: r['cargo_str'] for r in cur.fetchall()}

    vessels_by_anchorage = {}
    for r in anchored_vessels_raw:
        an  = r['anchorage_name'] or ''
        vid = r['vcn_id']
        lid = r['ldud_id']
        bl  = bl_map.get(vid, 0)
        ops = ops_map.get(lid, 0)
        balance = round(bl - ops, 2)
        pct = min(round(ops / bl * 100, 1) if bl > 0 else 0, 100)
        vessels_by_anchorage.setdefault(an, []).append({
            'vcn_id':       vid,
            'vessel_name':  r['vessel_name'],
            'doc_num':      r['vcn_doc_num'],
            'ldud_doc_num': r['ldud_doc_num'],
            'arrived':      str(r['anchored_time']) if r['anchored_time'] else None,
            'cargo':        cargo_map.get(lid, r['anch_cargo'] or ''),
            'bl_qty':       round(bl, 2),
            'ops_qty':      round(ops, 2),
            'balance':      balance,
            'pct':          pct,
        })

    # Build anchorage list
    anchorages = []
    for name, coords in anchorage_coords.items():
        anchorages.append({
            'id':      coords['id'],
            'name':    name,
            'lat':     coords['lat'],
            'lon':     coords['lon'],
            'vessels': vessels_by_anchorage.get(name, []),
        })

    # ── Active barge lines (stage detection) ─────────────────────────────────
    cur.execute('''
        SELECT
            lb.id,
            lb.barge_name,
            lb.cargo_name,
            lb.discharge_quantity,
            lb.along_side_vessel,
            lb.cast_off_mv,
            lb.anchored_gull_island,
            lb.aweigh_gull_island,
            lb.along_side_berth,
            lb.cast_off_berth,
            lh.vcn_id,
            lh.vessel_name,
            -- vessel anchorage coords via vcn_anchorage
            NULLIF(am.latitude::text,  '')::float AS anch_lat,
            NULLIF(am.longitude::text, '')::float AS anch_lon,
            va.anchorage_name
        FROM ldud_barge_lines lb
        JOIN ldud_header lh ON lh.id = lb.ldud_id
        LEFT JOIN vcn_anchorage va ON va.vcn_id = lh.vcn_id
            AND va.anchorage_arrival IS NOT NULL
            AND (va.anchorage_departure IS NULL OR va.anchorage_departure = '')
        LEFT JOIN anchorage_master am ON am.name = va.anchorage_name
        WHERE lh.doc_status != 'Closed'
          AND lb.barge_name IS NOT NULL AND lb.barge_name != ''
          AND (lb.cast_off_berth IS NULL OR lb.cast_off_berth = ''
               OR lb.along_side_berth IS NOT NULL)
        ORDER BY lh.vcn_id, lb.id
    ''')
    barge_lines = cur.fetchall()

    # ── Most-recent berth assignment per barge from LUEU ─────────────────────
    # No date filter: a barge AT_BERTH may have started yesterday; the most    ─
    # recent LUEU entry tells us which berth it is currently operating at.     ─
    cur.execute('''
        SELECT DISTINCT ON (source_id, barge_name)
            source_id, barge_name, berth_name, equipment_name, cargo_name
        FROM lueu_lines
        WHERE is_deleted IS NOT TRUE
          AND source_type = 'VCN'
          AND barge_name  IS NOT NULL AND barge_name  != ''
          AND berth_name  IS NOT NULL AND berth_name  != ''
        ORDER BY source_id, barge_name, id DESC
    ''')
    barge_berth_map = {(r['source_id'], r['barge_name']): r for r in cur.fetchall()}

    # ── BL quantities from ldud_barge_lines ──────────────────────────────────
    cur.execute('''
        SELECT lh.vcn_id, lb.barge_name,
               COALESCE(SUM(lb.discharge_quantity), 0) AS bl_qty
        FROM ldud_barge_lines lb
        JOIN ldud_header lh ON lh.id = lb.ldud_id
        WHERE lb.discharge_quantity IS NOT NULL AND lb.discharge_quantity > 0
        GROUP BY lh.vcn_id, lb.barge_name
    ''')
    barge_bl_map = {(r['vcn_id'], r['barge_name']): float(r['bl_qty']) for r in cur.fetchall()}

    # ── Actual discharged per (vcn, barge) ───────────────────────────────────
    cur.execute('''
        SELECT source_id, barge_name, COALESCE(SUM(quantity), 0) AS actual
        FROM lueu_lines
        WHERE source_type = 'VCN' AND is_deleted IS NOT TRUE
          AND barge_name IS NOT NULL AND barge_name != ''
        GROUP BY source_id, barge_name
    ''')
    barge_actual_map = {(r['source_id'], r['barge_name']): float(r['actual']) for r in cur.fetchall()}

    # ── Fleet-wide average transit times (all historical LDUD barge trips) ──────
    cur.execute('''
        SELECT AVG(secs) AS avg_v2g FROM (
            SELECT EXTRACT(EPOCH FROM (
                anchored_gull_island::timestamp - cast_off_mv::timestamp
            )) AS secs
            FROM ldud_barge_lines
            WHERE cast_off_mv IS NOT NULL AND cast_off_mv != ''
              AND anchored_gull_island IS NOT NULL AND anchored_gull_island != ''
        ) x WHERE secs BETWEEN 1800 AND 86400
    ''')
    r0 = cur.fetchone()
    avg_v2g = float(r0['avg_v2g'] or 0) or 9000.0  # default 2.5 h

    cur.execute('''
        SELECT AVG(secs) AS avg_g2b FROM (
            SELECT EXTRACT(EPOCH FROM (
                along_side_berth::timestamp - aweigh_gull_island::timestamp
            )) AS secs
            FROM ldud_barge_lines
            WHERE aweigh_gull_island IS NOT NULL AND aweigh_gull_island != ''
              AND along_side_berth IS NOT NULL AND along_side_berth != ''
        ) x WHERE secs BETWEEN 1800 AND 86400
    ''')
    r0 = cur.fetchone()
    avg_barge_g2b = float(r0['avg_g2b'] or 0) or 7200.0  # default 2 h

    cur.execute('''
        SELECT AVG(secs) AS avg_mbc_g2b FROM (
            SELECT EXTRACT(EPOCH FROM (
                vessel_all_made_fast::timestamp - departure_gull_island::timestamp
            )) AS secs
            FROM mbc_discharge_port_lines
            WHERE departure_gull_island IS NOT NULL AND departure_gull_island != ''
              AND vessel_all_made_fast IS NOT NULL AND vessel_all_made_fast != ''
        ) x WHERE secs BETWEEN 1800 AND 86400
    ''')
    r0 = cur.fetchone()
    avg_mbc_g2b = float(r0['avg_mbc_g2b'] or 0) or 7200.0  # default 2 h

    def barge_stage_info(r):
        """Return (stage_label, stage_start_datetime)."""
        asv = r['along_side_vessel']
        cmv = r['cast_off_mv']
        agl = r['anchored_gull_island']
        wgl = r['aweigh_gull_island']
        asb = r['along_side_berth']
        cob = r['cast_off_berth']
        if asb and not cob:    return 'AT_BERTH',               asb
        if cob:                return 'RETURNING',               cob
        if wgl and not asb:    return 'TRANSIT_GULL_TO_BERTH',  wgl
        if agl and not wgl:    return 'AT_GULL',                agl
        if cmv and not agl:    return 'TRANSIT_VESSEL_TO_GULL', cmv
        if asv:                return 'AT_VESSEL',               asv
        return 'PENDING', None

    def eta_info(stage_start, avg_secs):
        """Return (fraction 0-1, eta_minutes int). stage_start may be str or datetime."""
        if not stage_start or avg_secs <= 0:
            return 0.0, None
        try:
            if isinstance(stage_start, str):
                stage_start = datetime.fromisoformat(stage_start.replace('T', ' '))
            elapsed = (now - stage_start).total_seconds()
        except Exception:
            return 0.0, None
        fraction = min(max(elapsed / avg_secs, 0.0), 0.99)
        eta_mins = max(round((avg_secs - elapsed) / 60), 0)
        return round(fraction, 4), eta_mins

    # Build berth asset lists and transit list
    berth_assets = {}   # berth_name → list of asset dicts
    transit      = []   # barges in transit

    for r in barge_lines:
        stage, stage_start = barge_stage_info(r)
        vcn_id     = r['vcn_id']
        bname      = r['barge_name']
        cargo      = r['cargo_name'] or ''
        vessel     = r['vessel_name'] or ''
        anch_lat   = r['anch_lat']
        anch_lon   = r['anch_lon']
        bl         = barge_bl_map.get((vcn_id, bname), 0)
        actual     = barge_actual_map.get((vcn_id, bname), 0)
        pct        = min(round(actual / bl * 100, 1) if bl > 0 else 0, 100)

        berth_info  = barge_berth_map.get((vcn_id, bname), {})
        berth_name  = berth_info.get('berth_name', '')
        equipment   = berth_info.get('equipment_name', '')

        if stage == 'AT_BERTH':
            berth_c = berth_coords.get(berth_name, {})
            if not berth_c.get('lat'):
                continue
            berth_assets.setdefault(berth_name, []).append({
                'type':        'BARGE',
                'name':        bname,
                'vessel_name': vessel,
                'vcn_id':      vcn_id,
                'cargo':       cargo,
                'equipment':   equipment,
                'bl_qty':      round(bl, 2),
                'actual':      round(actual, 2),
                'pct':         pct,
            })

        elif stage == 'TRANSIT_VESSEL_TO_GULL':
            if not anch_lat:
                continue
            fraction, eta_min = eta_info(stage_start, avg_v2g)
            transit.append({
                'type':        'BARGE',
                'name':        bname,
                'vessel_name': vessel,
                'cargo':       cargo,
                'stage':       stage,
                'fraction':    fraction,
                'eta_minutes': eta_min,
                'eta_label':   'ETA Gull',
                'anch_lat':    round(anch_lat, 6),
                'anch_lon':    round(anch_lon, 6),
                'bl_qty':      round(bl, 2),
                'actual':      round(actual, 2),
                'pct':         pct,
            })

        elif stage == 'AT_GULL':
            transit.append({
                'type':        'BARGE',
                'name':        bname,
                'vessel_name': vessel,
                'cargo':       cargo,
                'stage':       stage,
                'fraction':    0.0,
                'eta_minutes': round(avg_barge_g2b / 60),
                'eta_label':   'ETA Port (est.)',
                'anch_lat':    round(anch_lat, 6) if anch_lat else None,
                'anch_lon':    round(anch_lon, 6) if anch_lon else None,
                'bl_qty':      round(bl, 2),
                'actual':      round(actual, 2),
                'pct':         pct,
            })

        elif stage == 'TRANSIT_GULL_TO_BERTH':
            fraction, eta_min = eta_info(stage_start, avg_barge_g2b)
            transit.append({
                'type':        'BARGE',
                'name':        bname,
                'vessel_name': vessel,
                'cargo':       cargo,
                'stage':       stage,
                'fraction':    fraction,
                'eta_minutes': eta_min,
                'eta_label':   'ETA Port',
                'anch_lat':    round(anch_lat, 6) if anch_lat else None,
                'anch_lon':    round(anch_lon, 6) if anch_lon else None,
                'bl_qty':      round(bl, 2),
                'actual':      round(actual, 2),
                'pct':         pct,
            })

    # ── Active MBCs from LUEU today ──────────────────────────────────────────
    cur.execute('''
        SELECT DISTINCT ON (ll.source_id, ll.berth_name)
            mh.id           AS mbc_id,
            mh.mbc_name,
            ll.berth_name,
            ll.cargo_name,
            ll.equipment_name,
            mh.bl_quantity  AS bl_qty,
            COALESCE(act.actual, 0) AS actual
        FROM lueu_lines ll
        JOIN mbc_header mh ON mh.id = ll.source_id AND ll.source_type = 'MBC'
        LEFT JOIN (
            SELECT source_id, COALESCE(SUM(quantity),0) AS actual
            FROM lueu_lines WHERE source_type='MBC' AND is_deleted IS NOT TRUE
            GROUP BY source_id
        ) act ON act.source_id = ll.source_id
        WHERE ll.entry_date = %s AND ll.is_deleted IS NOT TRUE
          AND ll.source_type = 'MBC'
          AND ll.berth_name IS NOT NULL
          AND mh.doc_status != 'Closed'
        ORDER BY ll.source_id, ll.berth_name, ll.id DESC
    ''', [today_s])
    for r in cur.fetchall():
        bn    = r['berth_name']
        bl    = float(r['bl_qty'] or 0)
        act   = float(r['actual'] or 0)
        pct   = round(act / bl * 100, 1) if bl > 0 else 0
        if not berth_coords.get(bn):
            continue
        berth_assets.setdefault(bn, []).append({
            'type':      'MBC',
            'name':      r['mbc_name'],
            'cargo':     r['cargo_name'] or '',
            'equipment': r['equipment_name'] or '',
            'bl_qty':    round(bl, 2),
            'actual':    round(act, 2),
            'pct':       min(pct, 100),
        })

    # ── MBCs in transit (via mbc_discharge_port_lines) ───────────────────────
    cur.execute('''
        SELECT mh.id AS mbc_id, mh.mbc_name, mh.cargo_name,
               dp.arrival_gull_island,
               dp.departure_gull_island,
               dp.vessel_all_made_fast,
               dp.vessel_cast_off,
               dp.vessel_unloading_berth
        FROM mbc_discharge_port_lines dp
        JOIN mbc_header mh ON mh.id = dp.mbc_id
        WHERE mh.doc_status != 'Closed'
          AND (dp.vessel_cast_off IS NULL OR dp.vessel_cast_off = '')
          AND dp.arrival_gull_island IS NOT NULL
          AND (dp.vessel_all_made_fast IS NULL OR dp.vessel_all_made_fast = '')
    ''')
    for r in cur.fetchall():
        agl = r['arrival_gull_island']
        dgl = r['departure_gull_island']

        if agl and not dgl:
            fraction, eta_min = 0.0, round(avg_mbc_g2b / 60)
            stage = 'AT_GULL'
            eta_label = 'ETA Port (est.)'
        elif dgl:
            fraction, eta_min = eta_info(dgl, avg_mbc_g2b)
            stage = 'TRANSIT_GULL_TO_BERTH'
            eta_label = 'ETA Port'
        else:
            continue

        transit.append({
            'type':        'MBC',
            'name':        r['mbc_name'],
            'cargo':       r['cargo_name'] or '',
            'stage':       stage,
            'fraction':    fraction,
            'eta_minutes': eta_min,
            'eta_label':   eta_label,
        })

    # ── Assign bank indices for double-banked berths ─────────────────────────
    berths_out = []
    for row in berth_rows:
        bn  = row['berth_name']
        lat = row['lat']
        lon = row['lon']
        if lat is None:
            continue

        # Tangent along berth alignment line and perpendicular toward channel
        bt_brg, ch_brg = _berth_bearings(lat, lon)
        # Assets bank along the berth face (tangent direction = along berth line)
        face_brg = _math.radians(bt_brg)
        step = 0.00011  # ~12 m between banks

        assets = berth_assets.get(bn, [])
        for i, asset in enumerate(assets):
            asset['bank_index'] = i
            asset['lat'] = round(lat + i * step * _math.cos(face_brg), 6)
            asset['lon'] = round(lon + i * step * _math.sin(face_brg) /
                                  _math.cos(_math.radians(lat)), 6)

        # Today's totals from LUEU
        cur.execute('''
            SELECT COALESCE(SUM(quantity),0) AS today_qty, COUNT(*) AS ops
            FROM lueu_lines
            WHERE entry_date = %s AND berth_name = %s AND is_deleted IS NOT TRUE
        ''', [today_s, bn])
        perf = cur.fetchone()

        berths_out.append({
            'berth_name':      bn,
            'lat':             lat,
            'lon':             lon,
            'berth_sequence':  row['berth_sequence'],
            'berth_bearing':   bt_brg,   # along berth line → barge long-axis
            'channel_bearing': ch_brg,   # perpendicular toward water → MBC bow
            'active':          len(assets) > 0,
            'assets':          assets,
            'today_qty':       round(float(perf['today_qty'] or 0), 2),
            'today_ops':       int(perf['ops'] or 0),
        })

    conn.close()

    return jsonify({
        'anchorages':  anchorages,
        'berths':      berths_out,
        'waypoints':   waypoints,
        'transit':     transit,
        'as_of':       now.strftime('%Y-%m-%d %H:%M:%S'),
    })
