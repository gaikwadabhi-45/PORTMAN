from flask import render_template, request, session, redirect, url_for, Response, jsonify
from functools import wraps
from datetime import date, datetime, timedelta
import io
import json

from .. import bp
from database import get_db, get_cursor

from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter

# ── Style constants (same as rest of RP01) ──────────────────────────────────
XL_NORM_SZ = 11
_thin  = Side(style='thin',   color='000000')
_bdr   = Border(left=_thin, right=_thin, top=_thin, bottom=_thin)
_ctr   = Alignment(horizontal='center', vertical='center', wrap_text=False)
_left  = Alignment(horizontal='left',   vertical='center', wrap_text=False)


def _fill(hex_color):
    return PatternFill('solid', fgColor=hex_color)


def _font(bold=False, size=XL_NORM_SZ):
    return Font(name='Calibri', bold=bold, size=size)


def _parse_dt(val):
    if not val:
        return None
    if isinstance(val, datetime):
        return val
    try:
        return datetime.fromisoformat(str(val))
    except Exception:
        return None


def _fmt_dt(val, strfmt='%d-%m-%Y %H:%M'):
    dt = _parse_dt(val)
    return dt.strftime(strfmt) if dt else ''


def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated


_MBC_OWNERS      = ['JSW INFRA', 'JSW SHIPPING', 'OTHERS']
_MBC_CARGO_TYPES = ['Break Bulk', 'Container', 'Liquid', 'Bulk']


# ── Routes ──────────────────────────────────────────────────────────────────

@bp.route('/module/RP01/daily-ops/')
@login_required
def daily_ops_index():
    return render_template('daily_ops/daily_ops.html', username=session.get('username'))


# ── Routes API (from CRM01 conveyor_routes) ─────────────────────────────────

@bp.route('/api/module/RP01/daily-ops/routes', methods=['GET'])
@login_required
def daily_ops_routes():
    """Return all active route names from conveyor_routes."""
    conn = get_db()
    cur  = get_cursor(conn)
    cur.execute("SELECT route_name FROM conveyor_routes WHERE is_active = 1 ORDER BY route_name")
    routes = [r['route_name'] for r in cur.fetchall()]
    conn.close()
    return jsonify(routes)


# ── Cutoff API ──────────────────────────────────────────────────────────────

@bp.route('/api/module/RP01/daily-ops/cutoff', methods=['GET'])
@login_required
def daily_ops_cutoff_get():
    """Return the latest cutoff record (or empty defaults)."""
    conn = get_db()
    cur  = get_cursor(conn)
    cur.execute("""
        SELECT id, cutoff_date, cutoff_values
        FROM daily_ops_cutoff
        ORDER BY id DESC LIMIT 1
    """)
    row = cur.fetchone()
    conn.close()
    if row:
        return jsonify({
            'id':            row['id'],
            'cutoff_date':   row['cutoff_date'],
            'cutoff_values': json.loads(row['cutoff_values']),
        })
    return jsonify({
        'id':            None,
        'cutoff_date':   '',
        'cutoff_values': {'mbc_cargo': {}, 'cargo_handled': {}},
    })


@bp.route('/api/module/RP01/daily-ops/cutoff', methods=['POST'])
@login_required
def daily_ops_cutoff_save():
    """Upsert cutoff: replace any existing row with the new values."""
    data = request.get_json(force=True)
    cutoff_date   = data.get('cutoff_date', '')
    cutoff_values = data.get('cutoff_values', {})

    if not cutoff_date:
        return Response('cutoff_date is required', status=400)

    values_json = json.dumps(cutoff_values)
    user = session.get('username', '')

    conn = get_db()
    cur  = get_cursor(conn)
    # Delete all existing rows (single-row table)
    cur.execute("DELETE FROM daily_ops_cutoff")
    cur.execute("""
        INSERT INTO daily_ops_cutoff (cutoff_date, cutoff_values, created_by)
        VALUES (%s, %s, %s)
    """, (cutoff_date, values_json, user))
    conn.commit()
    conn.close()
    return jsonify({'ok': True})


# ── Cutoff helper ───────────────────────────────────────────────────────────

def _load_cutoff():
    """Return (cutoff_date_str, cutoff_values_dict) or (None, {})."""
    conn = get_db()
    cur  = get_cursor(conn)
    cur.execute("""
        SELECT cutoff_date, cutoff_values
        FROM daily_ops_cutoff
        ORDER BY id DESC LIMIT 1
    """)
    row = cur.fetchone()
    conn.close()
    if row:
        return row['cutoff_date'], json.loads(row['cutoff_values'])
    return None, {}


# ── Data fetchers ───────────────────────────────────────────────────────────

from datetime import datetime, timedelta

def _fetch_data(report_date):
    window_end = datetime(
        report_date.year,
        report_date.month,
        report_date.day,
        8, 0, 0
    )

    # Previous day 08:00 AM
    window_start = window_end - timedelta(hours=24)

    ws_str = window_start.strftime('%Y-%m-%d %H:%M:%S')
    we_str = window_end.strftime('%Y-%m-%d %H:%M:%S')

    conn = get_db()
    cur = get_cursor(conn)

    # Fetch vessels that were active during the reporting window
    cur.execute("""
    SELECT DISTINCT

        h.id,
        h.vcn_id,
        h.vessel_name,
        h.operation_type,
        h.nor_tendered,

        first_anchor.discharge_started AS discharge_commenced,

        last_anchor.discharge_completed AS discharge_completed,

        CASE
            WHEN last_anchor.discharge_completed IS NULL THEN 1
            ELSE 0
        END AS sort_order,

        h.doc_status

    FROM ldud_header h

    LEFT JOIN LATERAL (
        SELECT
            MIN(a1.discharge_started) AS discharge_started
        FROM ldud_anchorage a1
        WHERE a1.ldud_id = h.id
        AND a1.discharge_started IS NOT NULL
    ) first_anchor ON TRUE

    LEFT JOIN LATERAL (
        SELECT
            CASE
                WHEN EXISTS (
                    SELECT 1
                    FROM ldud_anchorage x
                    WHERE x.ldud_id = h.id
                    AND x.discharge_started IS NOT NULL
                    AND x.discharge_commenced IS NULL
                )
                THEN NULL
                ELSE MAX(a2.discharge_commenced)
            END AS discharge_completed
        FROM ldud_anchorage a2
        WHERE a2.ldud_id = h.id
    ) last_anchor ON TRUE

    WHERE

        first_anchor.discharge_started IS NOT NULL

        -- Vessel started before report end
        AND first_anchor.discharge_started < %s

        -- Vessel active OR barges still active
        AND (
            last_anchor.discharge_completed IS NULL
            OR last_anchor.discharge_completed >= %s

            OR EXISTS (
            SELECT 1
            FROM ldud_barge_lines b
            WHERE b.ldud_id = h.id
            AND (
                    b.completed_discharge_berth IS NULL
                    OR b.cast_off_berth IS NULL
                )
        )
        )

    ORDER BY
        sort_order,
        first_anchor.discharge_started,
        h.id

    """,  (
    window_end,
    window_start
))

    vessels  = [dict(r) for r in cur.fetchall()]
    ldud_ids = [v['id'] for v in vessels]
    vcn_ids  = [v['vcn_id'] for v in vessels if v.get('vcn_id')]

    bl_import, bl_export, vcn_meta = {}, {}, {}

    if vcn_ids:
        cur.execute("""
            SELECT vcn_id, COALESCE(SUM(bl_quantity), 0) AS total
            FROM vcn_cargo_declaration
            WHERE vcn_id = ANY(%s) GROUP BY vcn_id
        """, (vcn_ids,))
        for r in cur.fetchall():
            bl_import[r['vcn_id']] = float(r['total'])

        cur.execute("""
            SELECT vcn_id, COALESCE(SUM(bl_quantity), 0) AS total
            FROM vcn_export_cargo_declaration
            WHERE vcn_id = ANY(%s) GROUP BY vcn_id
        """, (vcn_ids,))
        for r in cur.fetchall():
            bl_export[r['vcn_id']] = float(r['total'])

        cur.execute("""
            SELECT id, importer_exporter_name
            FROM vcn_header WHERE id = ANY(%s)
        """, (vcn_ids,))
        vcn_meta = {r['id']: r['importer_exporter_name'] or '' for r in cur.fetchall()}

    # ops_till — total discharged from lueu_lines up to selected date
    lueu_total = {}
    if vcn_ids:
        cur.execute("""
            SELECT source_id, COALESCE(SUM(quantity), 0) AS qty
            FROM lueu_lines
            WHERE source_type = 'VCN'
              AND source_id = ANY(%s)
              AND entry_date::date < %s::date
            GROUP BY source_id
        """, (vcn_ids, we_str))
        for r in cur.fetchall():
            lueu_total[r['source_id']] = float(r['qty'])

    # ops_24h — discharged in the 24h window
   # 24 Hr Discharge (Till Previous Day)

    ops_24h = {}

    prev_date = report_date - timedelta(days=1)

    if ldud_ids:
        cur.execute("""
            SELECT
                ldud_id,
                COALESCE(SUM(quantity), 0) AS qty
            FROM ldud_vessel_operations
            WHERE ldud_id = ANY(%s)
            AND TO_DATE(start_time, 'YYYY-MM-DD') = %s
            GROUP BY ldud_id
        """, (ldud_ids, prev_date))

        for r in cur.fetchall():
            ops_24h[r['ldud_id']] = float(r['qty'])


    #LOADED TILL DATE (TILL PREVIOUS DAY)

    ops_till = {}

    cutoff_date = report_date - timedelta(days=1)

    if ldud_ids:
        cur.execute("""
            SELECT
                ldud_id,
                COALESCE(SUM(quantity), 0) AS qty
            FROM ldud_vessel_operations
            WHERE ldud_id = ANY(%s)
            AND TO_DATE(start_time, 'YYYY-MM-DD') <= %s
            GROUP BY ldud_id
        """, (ldud_ids, cutoff_date))

        for r in cur.fetchall():
            ops_till[r['ldud_id']] = float(r['qty'])

    # Barges
    
    # Barges

    # Fetch actual discharged quantity for barges from lueu_lines

    barge_actual = {}

    cur.execute("""
        SELECT
            UPPER(TRIM(barge_name)) AS barge_name,
            SUM(COALESCE(quantity, 0)) AS actual_qty
        FROM lueu_lines
        WHERE source_type = 'MBC'
        AND is_deleted = false
        AND barge_name IS NOT NULL
        AND quantity IS NOT NULL
        AND TO_DATE(entry_date,'YYYY-MM-DD') <= %s
        GROUP BY UPPER(TRIM(barge_name))
    """, (report_date - timedelta(days=1),))

    for r in cur.fetchall():
        barge_actual[r['barge_name']] = float(r['actual_qty'])


    barge_stats = {}

    _STATUS_KEYS = (
        'at_jetty', 'waiting_discharge', 'waiting_empty_jetty',
        'at_gull_loaded', 'under_loading', 'waiting_loading',
        'in_transit_jetty_to_mv', 'Non-Operational',
    )

    if ldud_ids:
        cur.execute("""
            SELECT
                ldud_id,
                barge_name,
                discharge_quantity,
                port_crane,
                along_side_vessel,
                commenced_loading,
                completed_loading,
                cast_off_mv,
                anchored_gull_island,
                aweigh_gull_island,
                amf_at_port,
                along_side_berth,
                commence_discharge_berth,
                completed_discharge_berth,
                cast_off_berth,
                cast_off_port
            FROM ldud_barge_lines
            WHERE ldud_id = ANY(%s)
            AND (cast_off_port IS NULL OR cast_off_port > %s)
        """, (ldud_ids, ws_str))

        for r in cur.fetchall():

            lid = r['ldud_id']

            bn = (r['barge_name'] or '').strip()
            bn_key = bn.upper()

            bl_qty = float(r['discharge_quantity'] or 0)

            actual_qty = barge_actual.get(bn_key, 0)

            balance_qty = max(0, bl_qty - actual_qty)

            if lid not in barge_stats:
                barge_stats[lid] = {
                    'all': set(),
                    **{k: [] for k in _STATUS_KEYS}
                }

            if bn:
                barge_stats[lid]['all'].add(bn)

            if r['cast_off_port']:
                status = 'Non-Operational'
            elif r['completed_discharge_berth'] and not r['cast_off_berth']:
                status = 'waiting_empty_jetty'
            elif r['commence_discharge_berth'] and not r['cast_off_berth']:
                status = 'at_jetty'
            elif r['along_side_berth'] and not r['commence_discharge_berth']:
                status = 'waiting_discharge'
            elif r['cast_off_mv'] and not r['along_side_berth']:
                status = 'at_gull_loaded'
            elif r['commenced_loading'] and not r['completed_loading']:
                status = 'under_loading'
            elif r['along_side_vessel'] and not r['commenced_loading']:
                status = 'waiting_loading'
            else:
                status = None

            if status and bn:

                crane = (r['port_crane'] or '').strip()

                if status == 'at_jetty':
                    entry = (
                        f"{bn} - {crane} "
                        f"(BL:{int(round(bl_qty))} | "
                        f"Act:{int(round(actual_qty))} | "
                        f"Bal:{int(round(balance_qty))} MT)"
                    )

                elif status == 'waiting_discharge' and bl_qty:
                    entry = f"{bn} ({int(round(bl_qty))} MT)"

                else:
                    entry = bn

                barge_stats[lid][status].append(entry)
    conn.close()

    def _make_names(bs_dict, key):
        return ', '.join(bs_dict.get(key, []))

    for v in vessels:
        lid    = v['id']
        vid    = v.get('vcn_id')
        op     = v.get('operation_type', '')
        bl_qty = (bl_export.get(vid, 0) if op == 'Export' else bl_import.get(vid, 0)) if vid else 0
        actual = lueu_total.get(vid, 0)
        bs     = barge_stats.get(lid, {})

        v['stevedore_group']        = vcn_meta.get(vid, '') if vid else ''
        v['bl_qty']                 = bl_qty
        v['ops_24h']                = ops_24h.get(lid, 0)
        v['ops_till']               = ops_till.get(lid, 0)
        v['balance']                = round(bl_qty - ops_till.get(lid, 0),2
    )
        v['num_barges']             = len(bs.get('all', set())) or ''
        v['at_jetty']               = _make_names(bs, 'at_jetty')
        v['waiting_discharge']      = _make_names(bs, 'waiting_discharge')
        v['waiting_empty_jetty']    = _make_names(bs, 'waiting_empty_jetty')
        v['at_gull_loaded']         = _make_names(bs, 'at_gull_loaded')
        v['under_loading']          = _make_names(bs, 'under_loading')
        v['waiting_loading']        = _make_names(bs, 'waiting_loading')
        v['in_transit_jetty_to_mv'] = _make_names(bs, 'in_transit_jetty_to_mv')

    return vessels

def _fetch_upcoming_vessels(report_date):

    conn = get_db()
    cur = get_cursor(conn)

    cur.execute("""
    SELECT
        vh.vessel_name,

        vc.cargo_name,

        COALESCE(vc.bl_quantity, 0) AS bl_quantity,

        vh.vessel_agent_name,

        CASE
            WHEN lh.nor_tendered IS NULL
                THEN 'ETA : ' ||
                     TO_CHAR(vn.eta::timestamp, 'DD-MM-YYYY HH24:MI')

            WHEN lh.nor_tendered IS NOT NULL
                 AND fa.discharge_started IS NULL
                THEN 'ARRIVED AT : ' ||
                     TO_CHAR(lh.nor_tendered::timestamp, 'DD-MM-YYYY HH24:MI')
        END AS eta,

        CASE
            WHEN lh.nor_tendered IS NULL
                THEN 'ETA'

            WHEN lh.nor_tendered IS NOT NULL
                 AND fa.discharge_started IS NULL
                THEN 'ARRIVED'
        END AS vessel_status,

        COALESCE(
            lh.nor_tendered::timestamp,
            vn.eta::timestamp
        ) AS status_time

    FROM vcn_header vh

    JOIN vcn_nominations vn
        ON vn.vcn_id = vh.id

    LEFT JOIN ldud_header lh
        ON lh.vcn_id = vh.id

    LEFT JOIN vcn_cargo_declaration vc
        ON vc.vcn_id = vh.id

    LEFT JOIN LATERAL (
        SELECT MIN(a.discharge_started) AS discharge_started
        FROM ldud_anchorage a
        WHERE a.ldud_id = lh.id
    ) fa ON TRUE

    WHERE
        fa.discharge_started IS NULL

    ORDER BY status_time
    """)

    rows = cur.fetchall()

    conn.close()

    return rows


def _fetch_discharging_mbcs(report_date):

    window_end = datetime(
        report_date.year,
        report_date.month,
        report_date.day,
        8, 0, 0
    )

    window_start = window_end - timedelta(days=1)

    completion_start = datetime(
        report_date.year,
        report_date.month,
        report_date.day,
        6, 0, 0
    )

    completion_end = datetime(
        report_date.year,
        report_date.month,
        report_date.day,
        23, 59, 59
    )

    conn = get_db()
    cur = get_cursor(conn)

    cur.execute("""
SELECT
    h.id,
    h.mbc_name,
    p.vessel_unloaded_by AS equipment,
    h.cargo_name,

    h.bl_quantity AS bl_quantity,

    COALESCE(l.actual_qty, 0) AS actual_quantity,

    (h.bl_quantity - COALESCE(l.actual_qty, 0)) AS discharge_quantity,

    p.vessel_arrival_port,
    p.unloading_commenced,
    p.unloading_completed,

    CASE
        WHEN
            NULLIF(TRIM(p.vessel_arrival_port), '') IS NOT NULL
            AND NULLIF(TRIM(p.vessel_arrival_port), '')::timestamp >= %s
            AND NULLIF(TRIM(p.vessel_arrival_port), '')::timestamp < %s
            AND (
                p.unloading_commenced IS NULL
                OR TRIM(COALESCE(p.unloading_commenced, '')) = ''
            )
        THEN 'ARRIVED'

        WHEN
            p.unloading_commenced IS NOT NULL
            AND TRIM(COALESCE(p.unloading_commenced, '')) <> ''
            AND (
                p.unloading_completed IS NULL
                OR TRIM(COALESCE(p.unloading_completed, '')) = ''
            )
            AND NULLIF(TRIM(p.unloading_commenced), '')::timestamp >= %s
            AND NULLIF(TRIM(p.unloading_commenced), '')::timestamp < %s
        THEN 'DISCHARGING'

        WHEN
            p.unloading_completed IS NOT NULL
            AND TRIM(COALESCE(p.unloading_completed, '')) <> ''
            AND NULLIF(TRIM(p.unloading_completed), '')::timestamp >= %s
            AND NULLIF(TRIM(p.unloading_completed), '')::timestamp <= %s
        THEN 'COMPLETED'
    END AS status

FROM mbc_header h

JOIN mbc_discharge_port_lines p
    ON p.mbc_id = h.id

LEFT JOIN (
    SELECT
        source_id AS mbc_id,
        SUM(COALESCE(quantity, 0)) AS actual_qty
    FROM lueu_lines
    WHERE source_type = 'MBC'
      AND is_deleted = false
    GROUP BY source_id
) l
    ON l.mbc_id = h.id

WHERE

(
    NULLIF(TRIM(p.vessel_arrival_port), '') IS NOT NULL
    AND NULLIF(TRIM(p.vessel_arrival_port), '')::timestamp >= %s
    AND NULLIF(TRIM(p.vessel_arrival_port), '')::timestamp < %s
    AND (
        p.unloading_commenced IS NULL
        OR TRIM(COALESCE(p.unloading_commenced, '')) = ''
    )
)

OR

(
    p.unloading_commenced IS NOT NULL
    AND TRIM(COALESCE(p.unloading_commenced, '')) <> ''
    AND (
        p.unloading_completed IS NULL
        OR TRIM(COALESCE(p.unloading_completed, '')) = ''
    )
    AND NULLIF(TRIM(p.unloading_commenced), '')::timestamp >= %s
    AND NULLIF(TRIM(p.unloading_commenced), '')::timestamp < %s
)

OR

(
    p.unloading_completed IS NOT NULL
    AND TRIM(COALESCE(p.unloading_completed, '')) <> ''
    AND NULLIF(TRIM(p.unloading_completed), '')::timestamp >= %s
    AND NULLIF(TRIM(p.unloading_completed), '')::timestamp <= %s
)

ORDER BY
    CASE
        WHEN
            NULLIF(TRIM(p.vessel_arrival_port), '') IS NOT NULL
            AND (
                p.unloading_commenced IS NULL
                OR TRIM(COALESCE(p.unloading_commenced, '')) = ''
            )
        THEN 1

        WHEN
            p.unloading_commenced IS NOT NULL
            AND (
                p.unloading_completed IS NULL
                OR TRIM(COALESCE(p.unloading_completed, '')) = ''
            )
        THEN 2

        WHEN
            p.unloading_completed IS NOT NULL
        THEN 3
    END,

    COALESCE(
        NULLIF(TRIM(p.unloading_completed), '')::timestamp,
        NULLIF(TRIM(p.unloading_commenced), '')::timestamp,
        NULLIF(TRIM(p.vessel_arrival_port), '')::timestamp
    ) DESC

""", (
    window_start, window_end,          # CASE ARRIVED
    window_start, window_end,          # CASE DISCHARGING
    completion_start, completion_end,  # CASE COMPLETED

    window_start, window_end,          # WHERE ARRIVED
    window_start, window_end,          # WHERE DISCHARGING
    completion_start, completion_end   # WHERE COMPLETED
))

    rows = cur.fetchall()

    cur.close()
    conn.close()

    return rows

def _fetch_upcoming_mbcs(report_date):

    conn = get_db()
    cur = get_cursor(conn)

    cur.execute("""
        SELECT
            h.id,
            h.mbc_name,
            m.mbc_owner_name AS owner,
            h.cargo_name,
            h.bl_quantity,

            l.fwd_draft,
            l.mid_draft,
            l.aft_draft,

            CASE
                WHEN NULLIF(TRIM(l.eta), '') IS NOT NULL
                     AND NULLIF(TRIM(d.arrival_gull_island), '') IS NULL
                THEN l.eta

                WHEN NULLIF(TRIM(d.arrival_gull_island), '') IS NOT NULL
                     AND NULLIF(TRIM(d.departure_gull_island), '') IS NULL
                THEN d.arrival_gull_island

                WHEN NULLIF(TRIM(d.departure_gull_island), '') IS NOT NULL
                     AND NULLIF(TRIM(d.vessel_arrival_port), '') IS NULL
                THEN d.departure_gull_island
            END AS event_time,

            CASE
                WHEN NULLIF(TRIM(l.eta), '') IS NOT NULL
                     AND NULLIF(TRIM(d.arrival_gull_island), '') IS NULL
                THEN l.eta

                WHEN NULLIF(TRIM(d.arrival_gull_island), '') IS NOT NULL
                     AND NULLIF(TRIM(d.departure_gull_island), '') IS NULL
                THEN d.arrival_gull_island

                WHEN NULLIF(TRIM(d.departure_gull_island), '') IS NOT NULL
                     AND NULLIF(TRIM(d.vessel_arrival_port), '') IS NULL
                THEN d.departure_gull_island
            END AS event_date,

            CASE
                WHEN NULLIF(TRIM(l.eta), '') IS NOT NULL
                     AND NULLIF(TRIM(d.arrival_gull_island), '') IS NULL
                THEN 'ETA GULL'

                WHEN NULLIF(TRIM(d.arrival_gull_island), '') IS NOT NULL
                     AND NULLIF(TRIM(d.departure_gull_island), '') IS NULL
                THEN 'WAITING AT GULL'

                WHEN NULLIF(TRIM(d.departure_gull_island), '') IS NOT NULL
                     AND NULLIF(TRIM(d.vessel_arrival_port), '') IS NULL
                THEN 'ETA DHARAMTAR'
            END AS status

        FROM mbc_header h

        JOIN mbc_load_port_lines l
            ON l.mbc_id = h.id

        LEFT JOIN mbc_discharge_port_lines d
            ON d.mbc_id = h.id

        LEFT JOIN mbc_master m
            ON TRIM(m.mbc_name) = TRIM(h.mbc_name)

        WHERE
            (
                NULLIF(TRIM(l.eta), '') IS NOT NULL
                AND NULLIF(TRIM(d.arrival_gull_island), '') IS NULL
            )

            OR

            (
                NULLIF(TRIM(d.arrival_gull_island), '') IS NOT NULL
                AND NULLIF(TRIM(d.departure_gull_island), '') IS NULL
            )

            OR

            (
                NULLIF(TRIM(d.departure_gull_island), '') IS NOT NULL
                AND NULLIF(TRIM(d.vessel_arrival_port), '') IS NULL
            )

        ORDER BY
            CASE
                WHEN NULLIF(TRIM(l.eta), '') IS NOT NULL
                     AND NULLIF(TRIM(d.arrival_gull_island), '') IS NULL
                THEN 1

                WHEN NULLIF(TRIM(d.arrival_gull_island), '') IS NOT NULL
                     AND NULLIF(TRIM(d.departure_gull_island), '') IS NULL
                THEN 2

                WHEN NULLIF(TRIM(d.departure_gull_island), '') IS NOT NULL
                     AND NULLIF(TRIM(d.vessel_arrival_port), '') IS NULL
                THEN 3
            END,
            event_time
    """)

    rows = cur.fetchall()

    cur.close()
    conn.close()

    return rows

def _fetch_cargo_availability(report_date):

    conn = get_db()
    cur = get_cursor(conn)

    balance_date = report_date - timedelta(days=1)

    cur.execute("""
        SELECT
            cargo_name,
            SUM(balance_qty) AS at_jetty_qty

        FROM
        (

            /* MBC Balance */

            SELECT
                h.cargo_name,

                (
                    h.bl_quantity
                    - COALESCE(l.qty, 0)
                ) AS balance_qty

            FROM mbc_header h

            JOIN mbc_discharge_port_lines p
                ON p.mbc_id = h.id

            LEFT JOIN (
                SELECT
                    source_id,
                    SUM(COALESCE(quantity, 0)) AS qty

                FROM lueu_lines

                WHERE source_type = 'MBC'
                  AND is_deleted = false
                  AND TO_DATE(entry_date,'YYYY-MM-DD') = %s

                GROUP BY source_id

            ) l
                ON l.source_id = h.id

            WHERE
                p.unloading_commenced IS NOT NULL
                AND TRIM(COALESCE(p.unloading_commenced, '')) <> ''

                AND (
                    p.unloading_completed IS NULL
                    OR TRIM(COALESCE(p.unloading_completed, '')) = ''
                )

            UNION ALL

            /* At Jetty Barge Balance */

            SELECT
                b.cargo_name,

                GREATEST(
                    b.discharge_quantity
                    - COALESCE(lb.actual_qty, 0),
                    0
                ) AS balance_qty

            FROM ldud_barge_lines b

            LEFT JOIN (
                SELECT
                    UPPER(TRIM(barge_name)) AS barge_name,
                    SUM(COALESCE(quantity,0)) AS actual_qty

                FROM lueu_lines

                WHERE is_deleted = false
                  AND barge_name IS NOT NULL
                  AND TO_DATE(entry_date,'YYYY-MM-DD') = %s

                GROUP BY UPPER(TRIM(barge_name))

            ) lb
                ON lb.barge_name = UPPER(TRIM(b.barge_name))

            WHERE
                b.commence_discharge_berth IS NOT NULL

                AND (
                    b.cast_off_berth IS NULL
                    OR TRIM(COALESCE(b.cast_off_berth,'')) = ''
                )

        ) x

        GROUP BY cargo_name

        ORDER BY cargo_name

    """, (
        balance_date,
        balance_date
    ))

    rows = cur.fetchall()

    cur.close()
    conn.close()

    return rows

def _fetch_tide_data(report_date):

    start_datetime = report_date.strftime('%Y-%m-%d 00:00:00')

    conn = get_db()
    cur = get_cursor(conn)

    cur.execute("""
        SELECT
            tide_datetime,
            tide_meters
        FROM tide_master
        WHERE tide_datetime >= %s
        ORDER BY tide_datetime
        LIMIT 5
    """, (start_datetime,))

    rows = cur.fetchall()

    cur.close()
    conn.close()

    return rows


def _fetch_cargo_handled(report_date):
    """Fetch cargo handled by route (day + month)."""

    # Previous day window
    previous_date = report_date - timedelta(days=1)

    window_start = datetime(
        previous_date.year,
        previous_date.month,
        previous_date.day,
        0, 0, 0
    )

    window_end = datetime(
        previous_date.year,
        previous_date.month,
        previous_date.day,
        23, 59, 59
    )

    month_start = datetime(
        report_date.year,
        report_date.month,
        1,
        0, 0, 0
    )

    ws_str = window_start.strftime('%Y-%m-%d %H:%M:%S')
    we_str = window_end.strftime('%Y-%m-%d %H:%M:%S')

    cutoff_date_str, cutoff_vals = _load_cutoff()

    cargo_cutoff = cutoff_vals.get('cargo_handled', {})

    cutoff_dt = None

    if cutoff_date_str and cargo_cutoff:

        try:
            cutoff_dt = datetime.strptime(
                cutoff_date_str,
                '%Y-%m-%d'
            )

        except ValueError:
            pass

    use_cutoff = (
        cutoff_dt is not None
        and month_start < cutoff_dt
        and cutoff_dt <= report_date
    )

    conn = get_db()
    cur = get_cursor(conn)

    def _period(start, end):

        cur.execute("""
            SELECT
                route_name,
                COALESCE(SUM(quantity),0) AS qty
            FROM lueu_lines
            WHERE route_name IS NOT NULL
              AND route_name <> ''
              AND entry_date IS NOT NULL
              AND (entry_date || ' ' || COALESCE(from_time,'00:00')) >= %s
              AND (entry_date || ' ' || COALESCE(from_time,'00:00')) <= %s
            GROUP BY route_name
            ORDER BY route_name
        """, (start, end))

        return {
            r['route_name']: float(r['qty'])
            for r in cur.fetchall()
        }

    def _group_routes(data):

        grouped = {
            'DIRECT PLANT': 0,
            'STACKER / SHED': 0,
            'CEMENT SILO': 0,
            'BY ROAD': 0,
            'OTHERS': 0
        }

        for route, qty in data.items():

            route_upper = (route or '').strip().upper()

            if route_upper in (
                'C-131',
                'C-131 A',
                'C-131 C',
                'C-131 D',
                'C-131 E',
                'C-131 F'
            ):
                grouped['DIRECT PLANT'] += qty

            elif route_upper in (
                'COAL STACKER',
                'LS-01',
                'LS-02',
                'LS-03'
            ):
                grouped['STACKER / SHED'] += qty

            elif route_upper == 'LS-05':
                grouped['CEMENT SILO'] += qty

            elif route_upper == 'BY ROAD':
                grouped['BY ROAD'] += qty

            else:
                grouped['OTHERS'] += qty

        return {
            k: v
            for k, v in grouped.items()
            if v > 0
        }

    # Day Data (Previous Date)
    day_dict = _group_routes(
        _period(ws_str, we_str)
    )

    # Month Data
    if use_cutoff:

        cutoff_str = cutoff_dt.strftime(
            '%Y-%m-%d 00:00:00'
        )

        live_end = report_date.strftime(
            '%Y-%m-%d 23:59:59'
        )

        live_dict = _period(
            cutoff_str,
            live_end
        )

        month_dict = {}

        all_routes = set(
            list(cargo_cutoff.keys()) +
            list(live_dict.keys())
        )

        for route in all_routes:

            month_dict[route] = (
                float(cargo_cutoff.get(route, 0))
                + live_dict.get(route, 0)
            )

        month_dict = _group_routes(
            month_dict
        )

    else:

        month_dict = _group_routes(
            _period(
                month_start.strftime('%Y-%m-%d %H:%M:%S'),
                report_date.strftime('%Y-%m-%d 23:59:59')
            )
        )

    conn.close()

    day_rows = sorted(day_dict.items())
    month_rows = sorted(month_dict.items())

    return day_rows, month_rows


def _fetch_cargo_statistics(report_date):

    # Show previous day's report
    report_date = report_date - timedelta(days=1)

    window_end = datetime(
        report_date.year,
        report_date.month,
        report_date.day,
        7, 0, 0
    )

    window_start = window_end - timedelta(hours=24)

    month_start = datetime(
        report_date.year,
        report_date.month,
        1,
        7, 0, 0
    )

    conn = get_db()
    cur = get_cursor(conn)

    def _period(start_dt, end_dt):

        cur.execute("""
            SELECT
                CASE
                    WHEN source_type = 'VCN'
                        THEN 'Mumbai Anchorage'
                    WHEN source_type = 'MBC'
                        THEN 'MBC (Jaigad/Other)'
                    ELSE source_type
                END AS cargo_source,

                COALESCE(SUM(quantity), 0) AS qty

            FROM lueu_lines

            WHERE is_deleted = false
            AND entry_date IS NOT NULL
            AND (entry_date || ' ' || COALESCE(from_time,'00:00')) >= %s
            AND (entry_date || ' ' || COALESCE(from_time,'00:00')) < %s

            GROUP BY 1
            ORDER BY 1
        """, (
            start_dt.strftime('%Y-%m-%d %H:%M:%S'),
            end_dt.strftime('%Y-%m-%d %H:%M:%S')
        ))

        return [
            (r['cargo_source'], float(r['qty']))
            for r in cur.fetchall()
        ]

    day_rows = _period(
        window_start,
        window_end
    )

    month_rows = _period(
        month_start,
        window_end
    )

    conn.close()

    return day_rows, month_rows



def _fetch_mbc_cargo_handling(report_date):

    target_date = report_date - timedelta(days=1)

    month_start = date(
        target_date.year,
        target_date.month,
        1
    )

    conn = get_db()
    cur = get_cursor(conn)

    def _period(start_date, end_date):

        cur.execute("""
            SELECT

                h.cargo_type,

                COALESCE(
                    m.mbc_owner_name,
                    'OTHERS'
                ) AS owner,

                COALESCE(
                    SUM(l.quantity),
                    0
                ) AS qty

            FROM lueu_lines l

            JOIN mbc_header h
                ON h.id = l.source_id

            LEFT JOIN mbc_master m
                ON TRIM(m.mbc_name) = TRIM(h.mbc_name)

            WHERE l.is_deleted = false
              AND l.source_type = 'MBC'
              AND l.entry_date::date BETWEEN %s AND %s

            GROUP BY
                h.cargo_type,
                m.mbc_owner_name

            ORDER BY
                m.mbc_owner_name,
                h.cargo_type

        """, (
            start_date,
            end_date
        ))

        return cur.fetchall()

    # Day = previous day only
    day_rows = _period(
        target_date,
        target_date
    )

    # Month-to-date till previous day
    month_rows = _period(
        month_start,
        target_date
    )

    cur.close()
    conn.close()

    return day_rows, month_rows

def _fetch_mbc_status(report_date):

    conn = get_db()
    cur = get_cursor(conn)

    cur.execute("""
        SELECT
            m.mbc_name,

            CASE

                /* Empty : Waiting at Dharamtar */
                WHEN h.id IS NULL
                THEN 'EMPTY : WAITING AT DHARAMTAR'

                /* Empty : On the way to Load Port */
                WHEN
                    NULLIF(TRIM(d.unloading_completed), '') IS NOT NULL
                    AND NULLIF(TRIM(d.vessel_cast_off), '') IS NOT NULL
                    AND NULLIF(TRIM(l.arrived_load_port), '') IS NULL
                THEN
                    'EMPTY : ON THE WAY TO LOAD PORT'

                /* Empty : Waiting at Load Port */
                WHEN
                    NULLIF(TRIM(l.arrived_load_port), '') IS NOT NULL
                    AND NULLIF(TRIM(l.loading_commenced), '') IS NULL
                THEN
                    'EMPTY : WAITING AT LOAD PORT'

                /* Under Loading */
                WHEN
                    NULLIF(TRIM(l.loading_commenced), '') IS NOT NULL
                    AND NULLIF(TRIM(l.loading_completed), '') IS NULL
                THEN
                    'UNDER LOADING'

                /* Loaded : Waiting at Load Port */
                WHEN
                    NULLIF(TRIM(l.loading_completed), '') IS NOT NULL
                    AND NULLIF(TRIM(l.cast_off_load_port), '') IS NULL
                THEN
                    'LOADED : WAITING AT LOAD PORT'

                /* Loaded : On the way to Gull */
                WHEN
                    NULLIF(TRIM(l.cast_off_load_port), '') IS NOT NULL
                    AND NULLIF(TRIM(d.arrival_gull_island), '') IS NULL
                THEN
                    'LOADED : ON THE WAY TO GULL'

                /* Loaded : Waiting at Gull */
                WHEN
                    NULLIF(TRIM(d.arrival_gull_island), '') IS NOT NULL
                    AND NULLIF(TRIM(d.departure_gull_island), '') IS NULL
                THEN
                    'LOADED : WAITING AT GULL'

                /* Loaded : On the way to Dharamtar */
                WHEN
                    NULLIF(TRIM(d.departure_gull_island), '') IS NOT NULL
                    AND NULLIF(TRIM(d.vessel_arrival_port), '') IS NULL
                THEN
                    'LOADED : ON THE WAY TO DHARAMTAR'

                /* Loaded : Waiting at Dharamtar */
                WHEN
                    NULLIF(TRIM(d.vessel_arrival_port), '') IS NOT NULL
                    AND NULLIF(TRIM(d.unloading_commenced), '') IS NULL
                THEN
                    'LOADED : WAITING AT DHARAMTAR'

                /* Under Discharge */
                WHEN
                    NULLIF(TRIM(d.unloading_commenced), '') IS NOT NULL
                    AND NULLIF(TRIM(d.unloading_completed), '') IS NULL
                THEN
                    'UNDER DISCHARGE'

                /* Empty : Waiting at Dharamtar */
                WHEN
                    NULLIF(TRIM(d.unloading_completed), '') IS NOT NULL
                THEN
                    'EMPTY : WAITING AT DHARAMTAR'

                ELSE
                    '-'

            END AS mbc_status

        FROM mbc_master m

        LEFT JOIN LATERAL (
            SELECT h.*
            FROM mbc_header h
            WHERE TRIM(h.mbc_name) = TRIM(m.mbc_name)
            ORDER BY h.id DESC
            LIMIT 1
        ) h ON TRUE

        LEFT JOIN mbc_load_port_lines l
            ON l.mbc_id = h.id

        LEFT JOIN mbc_discharge_port_lines d
            ON d.mbc_id = h.id

        WHERE
            UPPER(TRIM(COALESCE(m.mbc_owner_name, '')))
            IN ('JSW INFRA', 'JSW SHIPPING')

        ORDER BY m.mbc_name
    """)

    rows = cur.fetchall()

    cur.close()
    conn.close()

    return rows
# def _fetch_mbc_cargo(report_date):
#     """Return (day_data, month_data) as dicts { owner: { cargo_type: qty } }.
#     Month values incorporate cutoff if the cutoff date falls within the report month.
#     """
#     from datetime import date as date_type
#     prev_date = report_date - timedelta(days=1)
#     day_str   = prev_date.strftime('%Y-%m-%d')
#     month_start_date = date_type(prev_date.year, prev_date.month, 1)

#     # ── Load cutoff ─────────────────────────────────────────────────────
#     cutoff_date_str, cutoff_vals = _load_cutoff()
#     mbc_cutoff = cutoff_vals.get('mbc_cargo', {})

#     cutoff_date_obj = None
#     if cutoff_date_str and mbc_cutoff:
#         try:
#             cutoff_date_obj = datetime.strptime(cutoff_date_str, '%Y-%m-%d').date()
#         except ValueError:
#             pass

#     use_cutoff = (cutoff_date_obj is not None
#                   and month_start_date < cutoff_date_obj
#                   and cutoff_date_obj <= prev_date)

#     conn = get_db()
#     cur  = get_cursor(conn)

#     def _period(date_from, date_to):
#         cur.execute("""
#             SELECT COALESCE(m.mbc_owner_name, 'OTHERS') AS owner,
#                    h.cargo_type,
#                    SUM(h.bl_quantity) AS qty
#             FROM mbc_header h
#             LEFT JOIN mbc_master m ON m.mbc_name = h.mbc_name
#             WHERE h.doc_date IS NOT NULL
#               AND h.doc_date >= %s
#               AND h.doc_date <= %s
#             GROUP BY owner, h.cargo_type
#         """, (date_from, date_to))
#         data = {o: {ct: 0.0 for ct in _MBC_CARGO_TYPES} for o in _MBC_OWNERS}
#         for r in cur.fetchall():
#             owner = r['owner'] if r['owner'] in _MBC_OWNERS else 'OTHERS'
#             ct    = r['cargo_type']
#             if ct in _MBC_CARGO_TYPES:
#                 data[owner][ct] += float(r['qty'] or 0)
#         return data

#     day_data = _period(day_str, day_str)

#     if use_cutoff:
#         # Query only from the day after cutoff onwards
#         live_from = (cutoff_date_obj + timedelta(days=1)).strftime('%Y-%m-%d')
#         live_data = _period(live_from, day_str)
#         # Merge cutoff + live
#         month_data = {o: {ct: 0.0 for ct in _MBC_CARGO_TYPES} for o in _MBC_OWNERS}
#         for owner in _MBC_OWNERS:
#             for ct in _MBC_CARGO_TYPES:
#                 co_key = f'{owner}|{ct}'
#                 co_val = float(mbc_cutoff.get(co_key, 0))
#                 live_val = live_data[owner][ct]
#                 month_data[owner][ct] = co_val + live_val
#     else:
#         mth_str = month_start_date.strftime('%Y-%m-%d')
#         month_data = _period(mth_str, day_str)

#     conn.close()
#     return day_data, month_data




def _fmt_tide_dt(dt_str):
    """'2026-01-27T16:00' -> '27/16:00'"""
    try:
        dt = datetime.fromisoformat(dt_str)
        return dt.strftime('%d/%H:%M')
    except Exception:
        return dt_str


# ── Excel builder ───────────────────────────────────────────────────────────

def _build_excel(vessels, report_date,
                 day_rows=None, month_rows=None, tide_rows=None,
                 mbc_day=None, mbc_month=None):
    from openpyxl import Workbook
    wb = Workbook()
    ws = wb.active
    ws.title = 'Daily Ops'
    day_rows   = day_rows   or []
    month_rows = month_rows or []
    tide_rows  = tide_rows  or []
    _empty_mbc = lambda: {o: {ct: 0.0 for ct in _MBC_CARGO_TYPES} for o in _MBC_OWNERS}
    mbc_day    = mbc_day   or _empty_mbc()
    mbc_month  = mbc_month or _empty_mbc()

        # Dynamic column calculation
    vessel_end_col = 1 + len(vessels)
    doc_col = vessel_end_col + 1
    issue_col = vessel_end_col + 2

    # Dynamic widths
    col_widths = {1: 30}

    # Vessel columns
    for i in range(len(vessels)):
        col_widths[2 + i] = 35

    # Extra columns
    col_widths[doc_col] = 32
    col_widths[issue_col] = 22

    # Apply widths
    for ci, w in col_widths.items():
        ws.column_dimensions[get_column_letter(ci)].width = w

    def _cell(r, c, val='', bold=False, fill='FFFFFF', align=_ctr):
        cell = ws.cell(r, c, val)
        cell.font      = _font(bold=bold)
        cell.fill      = _fill(fill)
        cell.alignment = align
        cell.border    = _bdr
        return cell

    def _merge_row(r, c1, c2, val='', bold=False, fill='FFFFFF', align=_ctr):
        ws.merge_cells(start_row=r, start_column=c1, end_row=r, end_column=c2)
        for ci in range(c1, c2 + 1):
            b = Border(
                left   = _thin if ci == c1 else None,
                right  = _thin if ci == c2 else None,
                top    = _thin,
                bottom = _thin,
            )
            try:
                cell        = ws.cell(r, ci)
                cell.fill   = _fill(fill)
                cell.border = b
            except AttributeError:
                pass
        anchor           = ws.cell(r, c1)
        anchor.value     = val
        anchor.font      = _font(bold=bold)
        anchor.alignment = align

    def _merge_col(r1, r2, c, val='', bold=False, fill='FFFFFF', align=_ctr):
        ws.merge_cells(start_row=r1, start_column=c, end_row=r2, end_column=c)
        for ri in range(r1, r2 + 1):
            b = Border(
                left   = _thin,
                right  = _thin,
                top    = _thin if ri == r1 else None,
                bottom = _thin if ri == r2 else None,
            )
            try:
                cell        = ws.cell(ri, c)
                cell.fill   = _fill(fill)
                cell.border = b
            except AttributeError:
                pass
        anchor           = ws.cell(r1, c)
        anchor.value     = val
        anchor.font      = _font(bold=bold)
        anchor.alignment = align

    date_str  = f"{report_date.day}.{report_date.month}.{report_date.year}"
    title_str = f'Daily Report of JSW Dharamtar Port Operation : {date_str}'

    # Row 1
    # Row 1
    ws.row_dimensions[1].height = 20

    vessel_end_col = 1 + len(vessels)
    doc_col = vessel_end_col + 1
    issue_col = vessel_end_col + 2

    _cell(1, 1, report_date.strftime('%d-%m-%Y'), align=_left)

    _merge_row(1, 2, vessel_end_col, title_str, align=_ctr)

    _cell(doc_col, 1)
    _cell(1, doc_col, 'Doc No. | REV.02 | Issue no. 02', align=_left)

    _cell(issue_col, 1)
    _cell(1, issue_col, f'Issue Date: {report_date.strftime("%d-%m-%Y")}', align=_left)

    # Row 2: vessel name headers
# Row 2: vessel name headers
    ws.row_dimensions[2].height = 20

    _cell(2, 1, '')

    for i, v in enumerate(vessels):
        _cell(
            2,
            2 + i,
            f'Vessel {i + 1}: {v["vessel_name"]}',
            bold=True,
            align=_ctr
        )

    # Empty cells after vessels
    _cell(2, doc_col, '')
    _cell(2, issue_col, '')

    label_discharge = 'Unloaded till Date (LUEU)'
    label_balance   = 'Balance'
    label_commenced = 'Disch Commenced'
    label_completed = 'Disch Completed'

    _q = lambda x: int(round(x)) if x else ''
    _n = lambda x: x if x else ''
    ROWS = [
        ('Stevedore/ Barge Group',          'stevedore_group',          None,       _left),
        ('BL Qty',                          'bl_qty',                   _q,         _ctr),
        ('24 hrs Discharge',                'ops_24h',                  _q,         _ctr),
        (label_discharge,                   'ops_till',                 _q,         _ctr),
        (label_balance,                     'balance',                  _q,         _ctr),
        ('Vsl Arrived/NOR',                 'nor_tendered',             _fmt_dt,    _ctr),
        (label_commenced,                   'discharge_commenced',      _fmt_dt,    _ctr),
        (label_completed,                   'discharge_completed',      _fmt_dt,    _ctr),
        (None, None, None, None),
        ('No of Barges',                    'num_barges',               _n,         _ctr),
        ('At Jetty',                        'at_jetty',                 _n,         _left),
        ('Waiting for Discharge',           'waiting_discharge',        _n,         _left),
        ('Waiting Empty at Jetty',          'waiting_empty_jetty',      _n,         _left),
        ('In transit- MV/Gull to Jetty',    None,                       None,       _left),
        ('At Gull- waiting (Loaded)',        'at_gull_loaded',           _n,         _left),
        ('Under Loading at MV',             'under_loading',            _n,         _left),
        ('Waiting for loading',             'waiting_loading',          _n,         _left),
        ('In transit- from Jetty to MV',    'in_transit_jetty_to_mv',   _n,         _left),
    ]

    for idx, (label, field, formatter, align) in enumerate(ROWS):
        r = 3 + idx
        ws.row_dimensions[r].height = 18

        if label is None:
            for ci in range(1, 10):
                _cell(r, ci, '')
            continue

        _cell(r, 1, label, bold=True, align=_left)
        for i, v in enumerate(vessels):
            raw = v.get(field)
            val = formatter(raw) if (formatter and raw is not None) else (raw or '')
            _cell(r, 2 + i, val, align=align)
            _cell(r, doc_col, '')
            _cell(r, issue_col, '')

    # ── Cargo Handled section ────────────────────────────────────────────────
    cargo_start = 3 + len(ROWS)

    def _cargo_section(row_start, period_rows, period_label):
        r = row_start
        n = len(period_rows) + 1
        _merge_col(r, r + n - 1, 1, period_label, bold=True, align=_ctr)
        for route_name, qty in period_rows:
            _cell(r, 2, route_name, align=_left)
            _cell(r, 3, int(round(qty)) if qty else '', align=_ctr)
            for ci in range(4, 10):
                _cell(r, ci, '')
            ws.row_dimensions[r].height = 18
            r += 1
        total = sum(q for _, q in period_rows)
        _cell(r, 2, 'Total:', bold=True, align=_left)
        _cell(r, 3, int(round(total)) if total else '', bold=True, align=_ctr)
        for ci in range(4, 10):
            _cell(r, ci, '')
        ws.row_dimensions[r].height = 18
        r += 1
        return r

    r = cargo_start
    for ci in range(1, 10):
        _cell(r, ci, '')
    ws.row_dimensions[r].height = 18
    r += 1
    _merge_row(r, 1, 3, 'Cargo Handled', bold=True, align=_left)
    for ci in range(4, 10):
        _cell(r, ci, '')
    ws.row_dimensions[r].height = 18
    r += 1
    r = _cargo_section(r, day_rows, 'For the Day')
    r = _cargo_section(r, month_rows, 'For the Month')

    # ── Tide — Dharamtar Port section ────────────────────────────────────────
    for ci in range(1, 10):
        _cell(r, ci, '')
    ws.row_dimensions[r].height = 18
    r += 1
    _merge_row(r, 1, 2, 'Tide- Dharamtar Port', bold=False, align=_ctr)
    for ci in range(3, 10):
        ws.cell(r, ci).value = None
    ws.row_dimensions[r].height = 18
    r += 1
    _cell(r, 1, 'Time', align=_ctr)
    _cell(r, 2, 'Tide', align=_ctr)
    ws.row_dimensions[r].height = 18
    r += 1
    for td_str, td_m in tide_rows:
        _cell(r, 1, _fmt_tide_dt(td_str), align=_ctr)
        _cell(r, 2, td_m, align=_ctr)
        ws.row_dimensions[r].height = 18
        r += 1

    # ── MBC's Cargo Handling section ─────────────────────────────────────────
    # Layout: col1=Owner | cols2-6=Day(BB,Container,Liquid,Bulk,Total)
    #                     | cols7-11=MTD(BB,Container,Liquid,Bulk,Total)
    MBC_TOTAL_COLS = 11

    for ci in range(1, MBC_TOTAL_COLS + 1):
        ws.cell(r, ci).value = None
    ws.row_dimensions[r].height = 18
    r += 1

    _merge_row(r, 1, MBC_TOTAL_COLS, "MBC's Cargo Handling", bold=False, align=_ctr)
    ws.row_dimensions[r].height = 18
    r += 1

    _merge_col(r, r + 1, 1, '', bold=False, align=_ctr)
    _merge_row(r, 2, 6,              'Day', bold=False, align=_ctr)
    _merge_row(r, 7, MBC_TOTAL_COLS, 'MTD', bold=False, align=_ctr)
    ws.row_dimensions[r].height = 18
    r += 1

    # Sub-header row 2: cargo type labels + Total for both Day and MTD
    for ci in range(2, 7):
        label = (_MBC_CARGO_TYPES + ['Total'])[ci - 2]
        _cell(r, ci, label, align=_ctr)
    for ci in range(7, 12):
        label = (_MBC_CARGO_TYPES + ['Total'])[ci - 7]
        _cell(r, ci, label, align=_ctr)
    ws.row_dimensions[r].height = 18
    r += 1

    # Widen col 11 for MTD Total
    ws.column_dimensions[get_column_letter(11)].width = 12

    totals_day   = {ct: 0.0 for ct in _MBC_CARGO_TYPES}
    totals_month = {ct: 0.0 for ct in _MBC_CARGO_TYPES}
    for owner in _MBC_OWNERS:
        _cell(r, 1, owner, align=_ctr)
        day_row   = mbc_day.get(owner,   {})
        month_row = mbc_month.get(owner, {})
        day_total = 0.0
        mtd_total = 0.0
        for idx, ct in enumerate(_MBC_CARGO_TYPES):
            dv = day_row.get(ct, 0.0)
            mv = month_row.get(ct, 0.0)
            _cell(r, 2 + idx, int(round(dv)) if dv else '', align=_ctr)
            _cell(r, 7 + idx, int(round(mv)) if mv else '', align=_ctr)
            day_total        += dv
            mtd_total        += mv
            totals_day[ct]   += dv
            totals_month[ct] += mv
        _cell(r, 6, int(round(day_total)) if day_total else '', align=_ctr)
        _cell(r, 11, int(round(mtd_total)) if mtd_total else '', align=_ctr)
        ws.row_dimensions[r].height = 18
        r += 1

    # Grand total row
    _cell(r, 1, 'Total', align=_ctr)
    grand_day = 0.0
    grand_mtd = 0.0
    for idx, ct in enumerate(_MBC_CARGO_TYPES):
        td = totals_day[ct]
        tm = totals_month[ct]
        _cell(r, 2 + idx, int(round(td)) if td else '', align=_ctr)
        _cell(r, 7 + idx, int(round(tm)) if tm else '', align=_ctr)
        grand_day += td
        grand_mtd += tm
    _cell(r, 6, int(round(grand_day)) if grand_day else '', align=_ctr)
    _cell(r, 11, int(round(grand_mtd)) if grand_mtd else '', align=_ctr)
    ws.row_dimensions[r].height = 18
    r += 1

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf


@bp.route('/api/module/RP01/daily-ops/preview')
@login_required
def daily_ops_preview():

    date_str = request.args.get(
        'report_date',
        date.today().strftime('%Y-%m-%d')
    )

    try:
        report_date = datetime.strptime(
            date_str,
            '%Y-%m-%d'
        ).date()
    except ValueError:
        return Response('Invalid date', status=400)

    vessels = _fetch_data(report_date)

    html = """
<div style="
    width:100%;
    overflow-x:auto;
    overflow-y:hidden;
">
<table style="
    border-collapse:collapse;
    font-family:Arial;
    table-layout:fixed;
    min-width:max-content;
">
    <tr style='background:#4a90d9;color:white'>
        <th style="
            border:1px solid #ccc;
            padding:8px;
            min-width:220px;
            width:220px;
            position:sticky;
            left:0;
            background:#4a90d9;
            z-index:10;
        ">
            Parameter
        </th>
"""

    for i, v in enumerate(vessels):
        html += f"""
            <th style="
                border:1px solid #ccc;
                padding:8px;
                min-width:280px;
                width:280px;
                max-width:280px;
                word-wrap:break-word;
                white-space:normal;
            ">
                Vessel {i+1}<br>{v['vessel_name']}
            </th>
        """

    html += "</tr>"

    rows = [
    ("Stevedore Group", "stevedore_group"),
    ("BL Qty", "bl_qty"),
    ("24 Hrs Discharge", "ops_24h"),
    ("Unloaded Till Date", "ops_till"),
    ("Balance", "balance"),
    ("Vsl Arrived/NOR", "nor_tendered"),
    ("Disch Commenced", "discharge_commenced"),
    ("Disch Completed", "discharge_completed"),
    ("No Of Barges", "num_barges"),
    ("At Jetty", "at_jetty"),
    ("Waiting Discharge", "waiting_discharge"),
    ("Waiting Empty At Jetty", "waiting_empty_jetty"),
    ("At Gull-waiting(Loaded)", "at_gull_loaded"),
    ("Under Loading", "under_loading"),
    ("Waiting Loading", "waiting_loading"),
    ("In Transit Jetty To MV", "in_transit_jetty_to_mv")
    ]

    for label, field in rows:

        html += f"""
        <tr>
            <td style="
                border:1px solid #ccc;
                padding:8px;
                font-weight:bold;
                min-width:220px;
                width:220px;
                position:sticky;
                left:0;
                background:white;
                z-index:5;
            ">
                {label}
            </td>
        """

        for v in vessels:

            value = v.get(field, '')

            if field in (
                'nor_tendered',
                'discharge_commenced',
                'discharge_completed'
            ):
                value = _fmt_dt(value)

            html += f"""
            <td style="
                border:1px solid #ccc;
                padding:8px;
                min-width:280px;
                width:280px;
                max-width:280px;
                vertical-align:top;
                white-space:normal;
                word-break:break-word;
            ">
                {value}
            </td>
            """

        html += "</tr>"

    html += """
    </table>
    </div>
    """

    upcoming_vessels = _fetch_upcoming_vessels(report_date)

    html += """
    <br><br>
    <h3>Upcoming Vessels</h3>
    <p>


    <table style='width:100%;border-collapse:collapse;font-family:Arial'>
        <tr style='background:#4a90d9;color:white'>
            <th style='border:1px solid #ccc;padding:8px'>Vessel Name</th>
            <th>Cargo</th>
            <th>Qty (MT)</th>
            <th style='border:1px solid #ccc;padding:8px'>Vessel Agent</th>
            <th style='border:1px solid #ccc;padding:8px'>ETA</th>
        </tr>
    """

    for v in upcoming_vessels:
        html += f"""
        <tr>
            <td style='border:1px solid #ccc;padding:8px'>{v['vessel_name']}</td>
            <td style='border:1px solid #ccc;padding:8px'>{v['cargo_name'] or '-'}</td>
            <td style='border:1px solid #ccc;padding:8px'>{v['bl_quantity'] or '-'}</td>
            <td style='border:1px solid #ccc;padding:8px'>{v['vessel_agent_name']}</td>
            <td style='border:1px solid #ccc;padding:8px'>{v['eta']}</td>
        </tr>
        """

    html += "</table>"

    discharging_mbcs = _fetch_discharging_mbcs(report_date)

    html += """
    <br><br>
    <h3>MBCs Discharging</h3>

    <table style='width:100%;border-collapse:collapse;font-family:Arial'>
        <tr style='background:#4a90d9;color:white'>
            <th style='border:1px solid #ccc;padding:8px'>MBC Name</th>
            <th style='border:1px solid #ccc;padding:8px'>Equipment</th>
            <th style='border:1px solid #ccc;padding:8px'>Cargo Name</th>
            <th style='border:1px solid #ccc;padding:8px'>Discharge Quantity (MT)</th>
        </tr>
    """

    for m in discharging_mbcs:

        equipment = m['equipment'] or '-'

        if m['status'] == 'DISCHARGING':
            row_style = "background-color:#fff3cd;"   # Yellow

        elif m['status'] == 'ARRIVED':
            row_style = "background-color:#f8d7da;"   # Light Red

        else:
            row_style = ""

        html += f"""
        <tr style="{row_style}">
            <td style='border:1px solid #ccc;padding:8px'>
                {m['mbc_name']}
            </td>

            <td style='border:1px solid #ccc;padding:8px;text-align:center'>
                {equipment}
            </td>

            <td style='border:1px solid #ccc;padding:8px'>
                {m['cargo_name']}
            </td>

            <td style='border:1px solid #ccc;padding:8px;text-align:right'>
                {float(m['discharge_quantity']):,.2f}
            </td>
        </tr>
        """

    html += "</table>"

    upcoming_mbcs = _fetch_upcoming_mbcs(report_date)

    html += """
    <br><br>
    <h3>Upcoming MBCs</h3>

    <table style='width:100%;border-collapse:collapse;font-family:Arial'>
        <tr style='background:#4a90d9;color:white'>
            <th style='border:1px solid #ccc;padding:8px'>MBC Name</th>
            <th style='border:1px solid #ccc;padding:8px'>Owner</th>
            <th style='border:1px solid #ccc;padding:8px'>Cargo Name</th>
            <th style='border:1px solid #ccc;padding:8px'>Quantity (MT)</th>
            <th style='border:1px solid #ccc;padding:8px'>FWD</th>
            <th style='border:1px solid #ccc;padding:8px'>MID</th>
            <th style='border:1px solid #ccc;padding:8px'>AFT</th>
            <th style='border:1px solid #ccc;padding:8px'>Date</th>
            <th style='border:1px solid #ccc;padding:8px'>Status</th>
        </tr>
    """

    for m in upcoming_mbcs:

        row_color = "#d1ecf1" if m["status"] == "AT GULL" else "#fff3cd"

        html += f"""
        <tr style="background-color:{row_color};">
            <td style='border:1px solid #ccc;padding:8px'>
                {m['mbc_name']}
            </td>

            <td style='border:1px solid #ccc;padding:8px'>
                {m.get('owner', '-')}
            </td>

            <td style='border:1px solid #ccc;padding:8px'>
                {m['cargo_name']}
            </td>

            <td style='border:1px solid #ccc;padding:8px;text-align:right'>
                {float(m['bl_quantity']):,.2f}
            </td>

            <td style='border:1px solid #ccc;padding:8px;text-align:center'>
                {m['fwd_draft'] if m['fwd_draft'] else '-'}
            </td>

            <td style='border:1px solid #ccc;padding:8px;text-align:center'>
                {m['mid_draft'] if m['mid_draft'] else '-'}
            </td>

            <td style='border:1px solid #ccc;padding:8px;text-align:center'>
                {m['aft_draft'] if m['aft_draft'] else '-'}
            </td>

            <td style='border:1px solid #ccc;padding:8px'>
                {datetime.fromisoformat(m['event_date']).strftime('%d-%m-%Y %H:%M')
                if m['event_date']
                    else '-'
                }
            </td>

            <td style='border:1px solid #ccc;padding:8px'>
                {m['status']}
            </td>
        </tr>
        """

    html += """
    </table>
    """
    cargo_availability = _fetch_cargo_availability(report_date)

    html += """
    <br><br>
    <h3>Cargo Availability for the Day</h3>

    <table style='width:100%;border-collapse:collapse;font-family:Arial;font-size:12px'>
    """

    # Header Row
    html += """
    <tr style='background:#4a90d9;color:white'>
        <th style='border:1px solid #ccc;padding:8px'></th>
    """

    for c in cargo_availability:
        html += f"""
        <th style='border:1px solid #ccc;padding:8px;text-align:center;min-width:100px'>
            {c['cargo_name']}
        </th>
        """

    html += "</tr>"

    # At Jetty Row
    html += """
    <tr>
        <td style='border:1px solid #ccc;padding:8px;font-weight:bold'>
            At Jetty
        </td>
    """

    grand_total = 0

    for c in cargo_availability:

        qty = c["at_jetty_qty"]

        display_value = ""

        if qty is not None:
            qty = float(qty)
            grand_total += qty
            display_value = f"{qty:,.0f}"

        html += f"""
        <td style='border:1px solid #ccc;padding:8px;text-align:right'>
            {display_value}
        </td>
        """

    html += "</tr>"

    # Total Row
    html += """
    <tr style='background:#f2f2f2;font-weight:bold'>
        <td style='border:1px solid #ccc;padding:8px'>
            Total
        </td>
    """

    for c in cargo_availability:

        qty = c["at_jetty_qty"]

        display_value = ""

        if qty is not None:
            display_value = f"{float(qty):,.0f}"

        html += f"""
        <td style='border:1px solid #ccc;padding:8px;text-align:right'>
            {display_value}
        </td>
        """

    html += """
    </tr>
    </table>
    """

    tide_rows = _fetch_tide_data(report_date)

    html += """
    <br><br>
    <h3>Tide - Dharamtar Port</h3>

    <table style='width:100%;border-collapse:collapse;font-family:Arial'>
    <tr style='background:#4a90d9;color:white'>
        <th style='border:1px solid #ccc;padding:8px'>Time</th>
        <th style='border:1px solid #ccc;padding:8px'>Tide (Meters)</th>
    </tr>
    """

    for row in tide_rows:

        tide_dt = datetime.fromisoformat(
            str(row['tide_datetime'])
        ).strftime('%d-%m-%Y %H:%M')

        tide_meters = f"{float(row.get('tide_meters') or 0):05.2f}"

        html += f"""
        <tr>
            <td style='border:1px solid #ccc;padding:8px'>
                {tide_dt}
            </td>

            <td style='border:1px solid #ccc;padding:8px;text-align:right'>
                {tide_meters}
            </td>
        </tr>
        """



    html += "</table>"

    day_rows, month_rows = _fetch_cargo_handled(report_date)

    html += """
    <br><br>
    <h3>Cargo Handled - For the Day</h3>

    <table style='width:100%;border-collapse:collapse;font-family:Arial'>
    <tr style='background:#4a90d9;color:white'>
        <th style='border:1px solid #ccc;padding:8px'>Route</th>
        <th style='border:1px solid #ccc;padding:8px'>Quantity (MT)</th>
    </tr>
    """

    day_total = 0

    for route_name, qty in day_rows:

        day_total += qty

        html += f"""
        <tr>
            <td style='border:1px solid #ccc;padding:8px'>
                {route_name}
            </td>

            <td style='border:1px solid #ccc;padding:8px;text-align:right'>
                {qty:,.0f}
            </td>
        </tr>
        """

    html += f"""
    <tr style='font-weight:bold;background:#f2f2f2'>
        <td style='border:1px solid #ccc;padding:8px'>
            TOTAL
        </td>

        <td style='border:1px solid #ccc;padding:8px;text-align:right'>
            {day_total:,.0f}
        </td>
    </tr>
    """

    html += "</table>"

    html += """
    <br><br>
    <h3>Cargo Handled - MTD</h3>

    <table style='width:100%;border-collapse:collapse;font-family:Arial'>
    <tr style='background:#4a90d9;color:white'>
        <th style='border:1px solid #ccc;padding:8px'>Route</th>
        <th style='border:1px solid #ccc;padding:8px'>Quantity (MT)</th>
    </tr>
    """

    month_total = 0

    for route_name, qty in month_rows:

        month_total += qty

        html += f"""
        <tr>
            <td style='border:1px solid #ccc;padding:8px'>
                {route_name}
            </td>

            <td style='border:1px solid #ccc;padding:8px;text-align:right'>
                {qty:,.0f}
            </td>
        </tr>
        """

    html += f"""
    <tr style='font-weight:bold;background:#f2f2f2'>
        <td style='border:1px solid #ccc;padding:8px'>
            TOTAL
        </td>

        <td style='border:1px solid #ccc;padding:8px;text-align:right'>
            {month_total:,.0f}
        </td>
    </tr>
    """

    html += "</table>"

    cargo_stat_day, cargo_stat_month = _fetch_cargo_statistics(report_date)

    html += """
    <br><br>
    <h3>Cargo Statistics - For the Day</h3>

    <table style='width:100%;border-collapse:collapse;font-family:Arial'>
    <tr style='background:#4a90d9;color:white'>
        <th style='border:1px solid #ccc;padding:8px'>Location</th>
        <th style='border:1px solid #ccc;padding:8px'>Quantity (MT)</th>
    </tr>
    """

    total_qty = 0

    for source_name, qty in cargo_stat_day:

        if not source_name:
            continue

        total_qty += qty

        html += f"""
        <tr>
            <td style='border:1px solid #ccc;padding:8px'>
                {source_name}
            </td>

            <td style='border:1px solid #ccc;padding:8px;text-align:right'>
                {qty:,.0f}
            </td>
        </tr>
        """

    html += f"""
    <tr style='font-weight:bold'>
        <td style='border:1px solid #ccc;padding:8px'>
            Total
        </td>

        <td style='border:1px solid #ccc;padding:8px;text-align:right'>
            {total_qty:,.0f}
        </td>
    </tr>
    """

    html += "</table>"

    mbc_day_rows, mbc_month_rows = _fetch_mbc_cargo_handling(report_date)

    day_dict = {
        (r['owner'], r['cargo_type']): float(r['qty'])
        for r in mbc_day_rows
    }

    month_dict = {
        (r['owner'], r['cargo_type']): float(r['qty'])
        for r in mbc_month_rows
    }

    owners = sorted(
        set(owner for owner, cargo in day_dict.keys()) |
        set(owner for owner, cargo in month_dict.keys())
    )

    cargo_types = sorted(
        set(cargo for owner, cargo in day_dict.keys()) |
        set(cargo for owner, cargo in month_dict.keys())
    )

    html += """
    <br><br>
    <h3>MBC's - Cargo Handling</h3>

    <table style='width:100%;border-collapse:collapse;font-family:Arial'>
    """

    # Header row 1
    html += f"""
    <tr style='background:#4a90d9;color:white'>
        <th rowspan='2' style='border:1px solid #ccc;padding:8px'>Owner</th>

        <th colspan='{len(cargo_types) + 1}'
            style='border:1px solid #ccc;padding:8px'>
            Day
        </th>

        <th colspan='{len(cargo_types) + 1}'
            style='border:1px solid #ccc;padding:8px'>
            MTD
        </th>
    </tr>
    """

    # Header row 2
    html += "<tr style='background:#4a90d9;color:white'>"

    for cargo in cargo_types:
        html += f"""
        <th style='border:1px solid #ccc;padding:8px'>
            {cargo}
        </th>
        """

    html += """
    <th style='border:1px solid #ccc;padding:8px'>Total</th>
    """

    for cargo in cargo_types:
        html += f"""
        <th style='border:1px solid #ccc;padding:8px'>
            {cargo}
        </th>
        """

    html += """
    <th style='border:1px solid #ccc;padding:8px'>Total</th>
    </tr>
    """

    # Owner rows
    # Owner rows
    for owner in owners:

        html += f"""
        <tr>
            <td style='border:1px solid #ccc;padding:8px'>
                {owner}
            </td>
        """

        day_total = 0

        for cargo in cargo_types:

            qty = day_dict.get((owner, cargo), 0)
            day_total += qty

            display_qty = "-" if qty == 0 else format(qty, ",.0f")

            html += f"""
            <td style='border:1px solid #ccc;padding:8px;text-align:right'>
                {display_qty}
            </td>
            """

        html += f"""
        <td style='border:1px solid #ccc;padding:8px;text-align:right;font-weight:bold'>
            {format(day_total, ",.0f")}
        </td>
        """

        month_total = 0

        for cargo in cargo_types:

            qty = month_dict.get((owner, cargo), 0)
            month_total += qty

            display_qty = "-" if qty == 0 else format(qty, ",.0f")

            html += f"""
            <td style='border:1px solid #ccc;padding:8px;text-align:right'>
                {display_qty}
            </td>
            """

        html += f"""
        <td style='border:1px solid #ccc;padding:8px;text-align:right;font-weight:bold'>
            {format(month_total, ",.0f")}
        </td>
        </tr>
        """

   
    # Grand total row
    html += """
    <tr style='font-weight:bold;background:#f2f2f2'>
        <td style='border:1px solid #ccc;padding:8px'>
            Total
        </td>
    """

    for cargo in cargo_types:

        total = sum(
            day_dict.get((owner, cargo), 0)
            for owner in owners
        )

        html += f"""
        <td style='border:1px solid #ccc;padding:8px;text-align:right'>
            {format(total, ",.0f")}
        </td>
        """

    html += f"""
    <td style='border:1px solid #ccc;padding:8px;text-align:right'>
        {format(sum(day_dict.values()), ",.0f")}
    </td>
    """

    for cargo in cargo_types:

        total = sum(
            month_dict.get((owner, cargo), 0)
            for owner in owners
        )

        html += f"""
        <td style='border:1px solid #ccc;padding:8px;text-align:right'>
            {format(total, ",.0f")}
        </td>
        """

    html += f"""
    <td style='border:1px solid #ccc;padding:8px;text-align:right'>
        {format(sum(month_dict.values()), ",.0f")}
    </td>
    </tr>
    """

    html += "</table>"

    mbc_status_rows = _fetch_mbc_status(report_date)

    html += """
    <br><br>
    <h3>MBC Status</h3>

    <table style='width:100%;border-collapse:collapse;font-family:Arial'>
    <tr style='background:#4a90d9;color:white'>
        <th style='border:1px solid #ccc;padding:8px'>MBC Name</th>
        <th style='border:1px solid #ccc;padding:8px'>Status</th>
    </tr>
    """

    for r in mbc_status_rows:

        html += f"""
        <tr>
            <td style='border:1px solid #ccc;padding:8px'>
                {r['mbc_name']}
            </td>

            <td style='border:1px solid #ccc;padding:8px'>
                {r['mbc_status']}
            </td>
        </tr>
        """

    html += "</table>"

    
    return html

# ── Download endpoint ───────────────────────────────────────────────────────

@bp.route('/api/module/RP01/daily-ops/download')
@login_required
def daily_ops_download():
    date_str = request.args.get('report_date', date.today().strftime('%Y-%m-%d'))

    try:
        report_date = datetime.strptime(date_str, '%Y-%m-%d').date()
    except ValueError:
        return Response('Invalid date', status=400)

    vessels = _fetch_data(report_date)
    if not vessels:
        return Response('No active (non-closed) vessels found', status=404)

    day_rows, month_rows = _fetch_cargo_handled(report_date)
    tide_rows            = _fetch_tide_data(report_date)
    mbc_day, mbc_month   = _fetch_mbc_cargo(report_date)
    buf = _build_excel(vessels, report_date,
                       day_rows, month_rows, tide_rows, mbc_day, mbc_month)
    fname = f'DailyOps_{date_str}.xlsx'
    return Response(
        buf.getvalue(),
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        headers={'Content-Disposition': f'attachment; filename="{fname}"'},
    )

    
