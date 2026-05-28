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

    # ── Vessels currently at anchorage ───────────────────────────────────────
    # vcn_anchorage: arrival set, departure null/empty → vessel is anchored
    cur.execute('''
        SELECT
            vh.id           AS vcn_id,
            vh.vessel_name,
            vh.vcn_doc_num,
            va.anchorage_name,
            va.anchorage_arrival
        FROM vcn_anchorage va
        JOIN vcn_header vh ON vh.id = va.vcn_id
        WHERE va.anchorage_arrival IS NOT NULL
          AND (va.anchorage_departure IS NULL OR va.anchorage_departure = '')
        ORDER BY va.anchorage_arrival DESC
    ''')
    anchored_vessels_raw = cur.fetchall()

    # Group by anchorage
    vessels_by_anchorage = {}
    for r in anchored_vessels_raw:
        an = r['anchorage_name'] or ''
        vessels_by_anchorage.setdefault(an, []).append({
            'vcn_id':       r['vcn_id'],
            'vessel_name':  r['vessel_name'],
            'doc_num':      r['vcn_doc_num'],
            'arrived':      str(r['anchorage_arrival']) if r['anchorage_arrival'] else None,
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

    # ── Today's berth assignments from LUEU (for barges at berth) ────────────
    cur.execute('''
        SELECT DISTINCT ON (source_id, barge_name)
            source_id, barge_name, berth_name, equipment_name, cargo_name
        FROM lueu_lines
        WHERE entry_date = %s AND is_deleted IS NOT TRUE
          AND source_type = 'VCN'
          AND barge_name IS NOT NULL AND barge_name != ''
          AND berth_name IS NOT NULL
        ORDER BY source_id, barge_name, id DESC
    ''', [today_s])
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

    def barge_stage(r):
        asv  = r['along_side_vessel']
        cmv  = r['cast_off_mv']
        agl  = r['anchored_gull_island']
        wgl  = r['aweigh_gull_island']
        asb  = r['along_side_berth']
        cob  = r['cast_off_berth']
        if asb and not cob:       return 'AT_BERTH'
        if cob:                    return 'RETURNING'
        if wgl and not asb:        return 'TRANSIT_GULL_TO_BERTH'
        if agl and not wgl:        return 'AT_GULL'
        if cmv and not agl:        return 'TRANSIT_VESSEL_TO_GULL'
        if asv:                    return 'AT_VESSEL'
        return 'PENDING'

    def midpoint(lat1, lon1, lat2, lon2):
        return (lat1 + lat2) / 2, (lon1 + lon2) / 2

    # Build berth asset lists and transit list
    berth_assets = {}   # berth_name → list of asset dicts
    transit      = []   # barges in transit

    for r in barge_lines:
        stage      = barge_stage(r)
        vcn_id     = r['vcn_id']
        bname      = r['barge_name']
        cargo      = r['cargo_name'] or ''
        vessel     = r['vessel_name'] or ''
        anch_lat   = r['anch_lat']
        anch_lon   = r['anch_lon']
        bl         = barge_bl_map.get((vcn_id, bname), 0)
        actual     = barge_actual_map.get((vcn_id, bname), 0)
        pct        = round(actual / bl * 100, 1) if bl > 0 else 0
        pct        = min(pct, 100)

        berth_info  = barge_berth_map.get((vcn_id, bname), {})
        berth_name  = berth_info.get('berth_name', '')
        equipment   = berth_info.get('equipment_name', '')
        berth_c     = berth_coords.get(berth_name, {})
        berth_lat   = berth_c.get('lat')
        berth_lon   = berth_c.get('lon')

        if stage == 'AT_BERTH' and berth_lat:
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
        elif stage in ('TRANSIT_VESSEL_TO_GULL', 'AT_GULL', 'TRANSIT_GULL_TO_BERTH', 'RETURNING'):
            # Compute approximate position
            if stage == 'TRANSIT_VESSEL_TO_GULL' and anch_lat:
                pos_lat, pos_lon = midpoint(anch_lat, anch_lon, gull_lat, gull_lon)
            elif stage == 'AT_GULL':
                pos_lat, pos_lon = gull_lat, gull_lon
            elif stage == 'TRANSIT_GULL_TO_BERTH' and berth_lat:
                pos_lat, pos_lon = midpoint(gull_lat, gull_lon, berth_lat, berth_lon)
            elif stage == 'RETURNING' and anch_lat and berth_lat:
                pos_lat, pos_lon = midpoint(berth_lat, berth_lon, anch_lat, anch_lon)
            else:
                continue  # can't position without coordinates

            transit.append({
                'type':        'BARGE',
                'name':        bname,
                'vessel_name': vessel,
                'cargo':       cargo,
                'stage':       stage,
                'lat':         round(pos_lat, 6),
                'lon':         round(pos_lon, 6),
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
        made_fast = r['vessel_all_made_fast']
        berth_name = r['vessel_unloading_berth'] or ''
        berth_c = berth_coords.get(berth_name, {})

        if agl and not dgl:
            pos_lat, pos_lon = gull_lat, gull_lon
            stage = 'AT_GULL'
        elif dgl and not made_fast and berth_c.get('lat'):
            pos_lat, pos_lon = midpoint(gull_lat, gull_lon, berth_c['lat'], berth_c['lon'])
            stage = 'TRANSIT_GULL_TO_BERTH'
        else:
            continue

        transit.append({
            'type':   'MBC',
            'name':   r['mbc_name'],
            'cargo':  r['cargo_name'] or '',
            'stage':  stage,
            'lat':    round(pos_lat, 6),
            'lon':    round(pos_lon, 6),
        })

    # ── Assign bank indices for double-banked berths ─────────────────────────
    berths_out = []
    for row in berth_rows:
        bn  = row['berth_name']
        lat = row['lat']
        lon = row['lon']
        if lat is None:
            continue
        assets = berth_assets.get(bn, [])
        for i, asset in enumerate(assets):
            # Offset each additional bank northward toward channel (~10m per bank)
            asset['bank_index'] = i
            asset['lat'] = round(lat + i * 0.00009, 6)
            asset['lon'] = round(lon, 6)

        # Today's totals from LUEU
        cur.execute('''
            SELECT COALESCE(SUM(quantity),0) AS today_qty, COUNT(*) AS ops
            FROM lueu_lines
            WHERE entry_date = %s AND berth_name = %s AND is_deleted IS NOT TRUE
        ''', [today_s, bn])
        perf = cur.fetchone()

        berths_out.append({
            'berth_name':    bn,
            'lat':           lat,
            'lon':           lon,
            'berth_sequence': row['berth_sequence'],
            'active':        len(assets) > 0,
            'assets':        assets,
            'today_qty':     round(float(perf['today_qty'] or 0), 2),
            'today_ops':     int(perf['ops'] or 0),
        })

    conn.close()

    return jsonify({
        'anchorages':  anchorages,
        'berths':      berths_out,
        'waypoints':   waypoints,
        'transit':     transit,
        'as_of':       now.strftime('%Y-%m-%d %H:%M:%S'),
    })
