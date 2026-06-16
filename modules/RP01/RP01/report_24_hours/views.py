from flask import (
    render_template,
    request,
    session,
    redirect,
    url_for,
    jsonify
)

from functools import wraps


from .. import bp
from database import get_db, get_cursor
from datetime import datetime, timedelta, time


# =========================================================
# LOGIN REQUIRED
# =========================================================

def login_required(f):

    @wraps(f)
    def decorated(*args, **kwargs):

        if 'user_id' not in session:
            return redirect(url_for('login'))

        return f(*args, **kwargs)

    return decorated


# =========================================================
# PAGE
# =========================================================

@bp.route('/module/RP01/report_24_hours/')
@login_required
def report_24_hours_page():

    return render_template(
        'report_24_hours/report_24_hours.html',
        username=session.get('username')
    )


# =========================================================
# API
# =========================================================

@bp.route('/module/RP01/api/report_24_hours', methods=['GET'])
@login_required
def get_24_hours_report():

    conn = None
    cur = None

    try:

        print("\n========== 24 HOURS REPORT API ==========")

        selected_date = request.args.get('report_date')

        print("SELECTED DATE:", selected_date)

        if not selected_date:

            return jsonify({
                'success': False,
                'message': 'report_date is required'
            }), 400



        # =================================================
        # FETCH PREVIOUS DATE
        # =================================================

        selected_date = request.args.get('report_date')

        print("SELECTED DATE:", selected_date)

        if not selected_date:

            return jsonify({
                'success': False,
                'message': 'report_date is required'
            }), 400

        # User selects 15-Jun -> fetch 14-Jun data
        fetch_date = (
            datetime.strptime(
                selected_date,
                '%Y-%m-%d'
            ) - timedelta(days=1)
        )

        print("REPORT DATE:", fetch_date.date())

        # =================================================
        # DATABASE CONNECTION
        # =================================================

        conn = get_db()

        print("DB CONNECTION SUCCESS")

        cur = get_cursor(conn)

        print("CURSOR CREATED")

        # =================================================
        # MV DISCHARGE
        # =================================================

        try:

            print("\n--- FETCHING MV DISCHARGE ---")

            selected_dt = datetime.strptime(
                selected_date,
                '%Y-%m-%d'
            )

            # Reporting Window
            window_start = (
                selected_dt - timedelta(days=1)
            ).replace(
                hour=8,
                minute=0,
                second=0,
                microsecond=0
            )

            window_end = selected_dt.replace(
                hour=8,
                minute=0,
                second=0,
                microsecond=0
            )

            print("WINDOW START:", window_start)
            print("WINDOW END  :", window_end)

            # Since start_time only contains dates,
            # use previous day's date for quantity calculation
            target_date = window_start.date()

            print("TARGET DATE:", target_date)

            mv_disch = 0
            mv_total_qty = 0
            mv_total_days = 0
            mv_discharge_list = []

            # ------------------------------------
            # 24 HRS DISCHARGE
            # ------------------------------------

            cur.execute("""
                SELECT COALESCE(SUM(quantity), 0) AS qty
                FROM ldud_vessel_operations
                WHERE TO_DATE(start_time, 'YYYY-MM-DD') = %s
            """, (target_date,))

            row = cur.fetchone()

            mv_disch = float(row['qty'] or 0)

            print("24 HRS MV DISCHARGE:", mv_disch)

            # ------------------------------------
            # DISCHARGE TILL DATE
            # ------------------------------------

            cur.execute("""
                SELECT COALESCE(SUM(quantity), 0) AS qty
                FROM ldud_vessel_operations
                WHERE TO_DATE(start_time, 'YYYY-MM-DD') <= %s
            """, (target_date,))

            row = cur.fetchone()

            mv_total_qty = float(row['qty'] or 0)

            print("MV DISCHARGE TILL DATE:", mv_total_qty)

        except Exception as e:

            print("MV DISCHARGE ERROR:", str(e))

            mv_disch = 0
            mv_total_qty = 0
            mv_total_days = 0
            mv_discharge_list = []
        # =================================================
        # MV WAITING
        # =================================================
        try:

            print("\n--- FETCHING MV WAITING ---")

            # Use selected date directly
            selected_dt = datetime.strptime(
                selected_date,
                '%Y-%m-%d'
            )

            # Report Window
            # Example:
            # Selected Date = 14-May-2026
            # Window Start = 13-May-2026 08:00 AM
            # Window End   = 14-May-2026 08:00 AM

            window_start = (
                selected_dt - timedelta(days=1)
            ).replace(
                hour=8,
                minute=0,
                second=0,
                microsecond=0
            )

            window_end = selected_dt.replace(
                hour=8,
                minute=0,
                second=0,
                microsecond=0
            )

            print("WINDOW START:", window_start)
            print("WINDOW END:", window_end)

            cur.execute("""
                SELECT
                    COALESCE(vcn_doc_num, '') || ' / ' ||
                    COALESCE(vessel_name, '') AS vessel_name
                FROM ldud_header
                WHERE nor_accepted IS NOT NULL
                AND discharge_commenced IS NULL
                AND TO_TIMESTAMP(
                        nor_accepted,
                        'YYYY-MM-DD"T"HH24:MI'
                    ) >= %s
                AND TO_TIMESTAMP(
                        nor_accepted,
                        'YYYY-MM-DD"T"HH24:MI'
                    ) < %s
                ORDER BY vessel_name
            """, (window_start, window_end))

            mv_waiting_rows = cur.fetchall()

            if mv_waiting_rows:

                mv_waiting_list = [
                    {
                        'vessel_name': str(row['vessel_name'])
                    }
                    for row in mv_waiting_rows
                ]

                mv_waiting_count = len(mv_waiting_list)

            else:

                mv_waiting_list = []
                mv_waiting_count = 0

            print("MV WAITING COUNT:", mv_waiting_count)

        except Exception as e:

            print("MV WAITING ERROR:", str(e))

            conn.rollback()

            mv_waiting_list = []
            mv_waiting_count = 0
        # =================================================
        # MBC WAITING
        # =================================================

        try:

            print("\n--- FETCHING MBC WAITING ---")

            cur.execute("""
                SELECT
                    h.mbc_name,
                    h.cargo_name
                FROM mbc_discharge_port_lines d
                INNER JOIN mbc_header h
                    ON h.id = d.mbc_id
                WHERE d.arrival_gull_island IS NOT NULL
                AND d.vessel_arrival_port IS NULL
                ORDER BY h.mbc_name
            """)

            mbc_waiting_rows = [
                {
                    'mbc_name': row['mbc_name'],
                    'cargo_name': row['cargo_name']
                }
                for row in cur.fetchall()
            ]

            if not mbc_waiting_rows:
                mbc_waiting_rows = [
                    {
                        'mbc_name': 'Nil',
                        'cargo_name': ''
                    }
                ]

            print("MBC WAITING COUNT:", len(mbc_waiting_rows))
            print("MBC WAITING:", mbc_waiting_rows)

        except Exception as e:

            print("MBC WAITING ERROR:", str(e))
            conn.rollback()

            mbc_waiting_rows = [
                {
                    'mbc_name': 'Nil',
                    'cargo_name': ''
                }
            ]

        # =================================================
        # MV DISCHARGING / COMPLETED IN LAST 24 HRS
        # =================================================

        mv_discharge_list = []

        try:

            selected_dt = datetime.strptime(
                selected_date,
                '%Y-%m-%d'
            )

            window_end = selected_dt.replace(
                hour=8,
                minute=0,
                second=0,
                microsecond=0
            )

            window_start = window_end - timedelta(hours=24)

            print("MV WINDOW START:", window_start)
            print("MV WINDOW END  :", window_end)

            cur.execute("""
                SELECT DISTINCT

                    h.id,
                    h.vcn_id,
                    h.vessel_name,

                    first_anchor.discharge_started
                        AS discharge_commenced,

                    last_anchor.discharge_completed
                        AS discharge_completed

                FROM ldud_header h

                LEFT JOIN LATERAL (
                    SELECT
                        MIN(a1.discharge_started)
                            AS discharge_started
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

                    AND first_anchor.discharge_started < %s

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

                ORDER BY h.vessel_name

            """, (
                window_end,
                window_start
            ))

            rows = cur.fetchall()

            print("MV ROWS FOUND:", len(rows))

            # ------------------------------------
            # DISCHARGED QTY TILL REPORT DATE
            # ------------------------------------

            lueu_total = {}

            cur.execute("""
                SELECT
                    source_id,
                    COALESCE(SUM(quantity), 0) AS qty
                FROM lueu_lines
                WHERE source_type = 'VCN'
                GROUP BY source_id
            """)

            for r in cur.fetchall():

                lueu_total[r['source_id']] = float(
                    r['qty'] or 0
                )

            # ------------------------------------
            # PROCESS VESSELS
            # ------------------------------------

            for row in rows:

                vcn_id = row['vcn_id']

                cargo_name = ''
                bl_qty = 0

                if vcn_id:

                    cur.execute("""
                        SELECT
                            STRING_AGG(
                                DISTINCT cargo_name,
                                ', '
                            ) AS cargo_names,

                            COALESCE(
                                SUM(bl_quantity),
                                0
                            ) AS total_bl

                        FROM vcn_cargo_declaration
                        WHERE vcn_id = %s
                    """, (vcn_id,))

                    cargo_row = cur.fetchone()

                    if cargo_row:

                        cargo_name = (
                            cargo_row['cargo_names']
                            or ''
                        )

                        bl_qty = float(
                            cargo_row['total_bl']
                            or 0
                        )

                discharge_qty = lueu_total.get(
                    vcn_id,
                    0
                )

                balance_qty = max(
                    bl_qty - discharge_qty,
                    0
                )

                mv_discharge_list.append({

                    'vessel_name': row['vessel_name'],

                    'cargo_name': cargo_name,

                    'bl_quantity': round(
                        bl_qty,
                        2
                    ),

                    'discharge_qty': round(
                        discharge_qty,
                        2
                    ),

                    'balance_qty': round(
                        balance_qty,
                        2
                    ),

                    'status': (
                        'Still Discharging'
                        if row['discharge_completed'] is None
                        else 'Completed'
                    )

                })

            print(
                "MV DISCHARGE LIST COUNT:",
                len(mv_discharge_list)
            )

            print(
                "MV DISCHARGE LIST:",
                mv_discharge_list
            )

        except Exception as e:

            print(
                "MV DISCHARGE LIST ERROR:",
                str(e)
            )

            conn.rollback()

            mv_discharge_list = []
            # =================================================
        # MBC DISCHARGING LAST 24 HRS
        # =================================================

        try:

            print("\n--- FETCHING MBC DISCH TOTAL ---")

            target_date = fetch_date.date()

            print("TARGET DATE:", target_date)

            cur.execute("""
                SELECT
                    ROUND(
                        COALESCE(
                            SUM(COALESCE(quantity, 0))::numeric,
                            0
                        ),
                        2
                    ) AS mbc_total_mt
                FROM lueu_lines
                WHERE source_type = 'MBC'
                AND TO_DATE(
                        entry_date,
                        'YYYY-MM-DD'
                    ) = %s
            """, (target_date,))

            mbc_disch_row = cur.fetchone()

            mbc_disch_total = float(
                mbc_disch_row['mbc_total_mt'] or 0
            ) if mbc_disch_row else 0

            print("MBC DISCH TOTAL:", mbc_disch_total)

        except Exception as e:

            print("MBC DISCH ERROR:", str(e))

            conn.rollback()

            mbc_disch_total = 0
        # =================================================
        # BARGES COUNT FOR REPORT WINDOW
        # =================================================

        try:

            barges_count = 0

            active_ldud_ids = [
                r['id']
                for r in rows
            ]

            if active_ldud_ids:

                cur.execute("""
                    SELECT
                        COUNT(DISTINCT
                            TRIM(barge_name)
                        ) AS total

                    FROM ldud_barge_lines

                    WHERE ldud_id = ANY(%s)

                    AND (
                        (commence_discharge_berth IS NOT NULL
                        AND cast_off_berth IS NULL)

                        OR

                        (along_side_berth IS NOT NULL
                        AND commence_discharge_berth IS NULL)

                        OR

                        (cast_off_mv IS NOT NULL
                        AND along_side_berth IS NULL)

                        OR

                        (commenced_loading IS NOT NULL
                        AND completed_loading IS NULL)
                    )
                """, (active_ldud_ids,))

                row = cur.fetchone()

                barges_count = int(
                    row['total'] or 0
                )

            print(
                "REPORT WINDOW BARGES:",
                barges_count
            )

        except Exception as e:

            print(
                "BARGE ERROR:",
                str(e)
            )

            barges_count = 0

        # =================================================
        # JETTY HANDLING
        # =================================================

        try:

            print("\n--- FETCHING JETTY HANDLING ---")

            # Today
            cur.execute("""
                SELECT COALESCE(SUM(quantity), 0) AS total
                FROM jetty_handling
                WHERE DATE(handling_date) = %s
            """, (fetch_date.date(),))

            row = cur.fetchone()
            jetty_today = float(row['total'] or 0) if row else 0

            # MTD
            cur.execute("""
                SELECT COALESCE(SUM(quantity), 0) AS total
                FROM jetty_handling
                WHERE DATE_TRUNC('month', handling_date) = DATE_TRUNC('month', %s::date)
                  AND handling_date <= %s
            """, (fetch_date.date(), fetch_date.date()))

            row = cur.fetchone()
            jetty_mtd = float(row['total'] or 0) if row else 0

            # YTD
            cur.execute("""
                SELECT COALESCE(SUM(quantity), 0) AS total
                FROM jetty_handling
                WHERE DATE_TRUNC('year', handling_date) = DATE_TRUNC('year', %s::date)
                  AND handling_date <= %s
            """, (fetch_date.date(), fetch_date.date()))

            row = cur.fetchone()
            jetty_ytd = float(row['total'] or 0) if row else 0

            # Cargo-wise breakdown for Today
            cur.execute("""
                SELECT
                    cargo_name,
                    COALESCE(SUM(quantity), 0) AS total
                FROM jetty_handling
                WHERE DATE(handling_date) = %s
                GROUP BY cargo_name
                ORDER BY total DESC
            """, (fetch_date.date(),))

            jetty_cargo_list = [
                {
                    'cargo_name': str(row['cargo_name']),
                    'quantity': str(float(row['total'] or 0))
                }
                for row in cur.fetchall()
            ]

            print("JETTY TODAY:", jetty_today)
            print("JETTY MTD:", jetty_mtd)
            print("JETTY YTD:", jetty_ytd)
            print("JETTY CARGO LIST:", jetty_cargo_list)

        except Exception as e:

            print("JETTY ERROR:", str(e))
            conn.rollback()  # ← ADDED
            jetty_today = 0
            jetty_mtd = 0
            jetty_ytd = 0
            jetty_cargo_list = []

        # =================================================
        # DELAYS
        # =================================================

        try:

            print("\n--- FETCHING DELAYS ---")

            # ← CHANGED: SUM(delay_hours) instead of COUNT(*),
            #             added date filter on delay_date
            cur.execute("""
                SELECT
                    delay_name,
                    ROUND(
                        COALESCE(SUM(delay_hours), 0),
                        2
                    ) AS total_hours
                FROM lueu_lines
                WHERE delay_name IS NOT NULL
                  AND DATE(delay_date) = %s
                GROUP BY delay_name
                ORDER BY total_hours DESC
            """, (fetch_date.date(),))

            delay_rows = cur.fetchall()

            print("DELAY ROW COUNT:", len(delay_rows))

        except Exception as e:

            print("DELAY ERROR:", str(e))

            delay_rows = []

        # =================================================
        # SAFE RESPONSE
        # =================================================

        safe_mbc_waiting = []

        for row in mbc_waiting_rows:

            try:

                safe_mbc_waiting.append({

                    'mbc_name': str(row[0]) if row[0] else '',
                    'cargo_name': str(row[1]) if row[1] else ''

                })

            except Exception as e:

                print("MBC ROW ERROR:", str(e))

        safe_delays = []

        for row in delay_rows:

            try:

                # ← CHANGED: key is now 'delay_name'/'total_hours' (dict row)
                safe_delays.append({

                    'delay_name': str(row['delay_name']) if row['delay_name'] else '',
                    'hours': str(row['total_hours']) if row['total_hours'] else '0'

                })

            except Exception as e:

                print("DELAY ROW ERROR:", str(e))

        response = {

            'success': True,

            'selected_date': str(selected_date),

            'fetch_date': fetch_date.strftime('%Y-%m-%d'),

            'mv_disch': str(mv_disch),
            'mv_total_days': str(mv_total_days),       # ← ADDED
            'mv_discharge_list': mv_discharge_list,
            'mv_waiting_list': mv_waiting_list,        # ← ADDED

            'barges_count': str(barges_count),

            'mbc_waiting': mbc_waiting_rows,
            'mbc_disch_total': str(mbc_disch_total),   # ← ADDED

            'jetty_today': str(jetty_today),           # ← ADDED
            'jetty_mtd': str(jetty_mtd),               # ← ADDED
            'jetty_ytd': str(jetty_ytd),               # ← ADDED
            'jetty_cargo_list': jetty_cargo_list,      # ← ADDED

            'delays': safe_delays
        }

        print("\nRESPONSE CREATED SUCCESSFULLY")

        return jsonify(response)

    except Exception as e:

        print("\nMAIN ERROR:", repr(e))

        return jsonify({

            'success': False,
            'message': str(e)

        }), 500

    finally:

        print("\nCLOSING CONNECTION")

        if cur:
            cur.close()

        if conn:
            conn.close()