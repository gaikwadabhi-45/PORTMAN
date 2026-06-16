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
from datetime import datetime, timedelta


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
            print("FETCH DATE FOR MV:", fetch_date.date())

            report_date = fetch_date.date()

            # Initialize variables
            mv_disch = 0
            mv_total_days = 0
            mv_discharge_list = []

            # Previous day for 24 Hrs discharge
            prev_date = report_date - timedelta(days=1)

            # ------------------------------------
            # 24 HRS DISCHARGE
            # ------------------------------------
            cur.execute("""
                SELECT COALESCE(SUM(quantity), 0) AS qty
                FROM ldud_vessel_operations
                WHERE start_time::date = %s
            """, (prev_date,))

            row = cur.fetchone()

            mv_disch = float(row['qty'] or 0)

            print("TOTAL MV DISCH (24 HRS):", mv_disch)

            # ------------------------------------
            # DISCHARGE TILL DATE
            # ------------------------------------
            cutoff_date = report_date - timedelta(days=1)

            cur.execute("""
                SELECT COALESCE(SUM(quantity), 0) AS qty
                FROM ldud_vessel_operations
                WHERE start_time::date <= %s
            """, (cutoff_date,))

            row = cur.fetchone()

            mv_total_qty = float(row['qty'] or 0)

            print("TOTAL MV DISCH TILL DATE:", mv_total_qty)

        except Exception as e:

            print("MV DISCHARGE ERROR:", str(e))

            mv_disch = 0
            mv_total_days = 0
            mv_discharge_list = []
        # =================================================
        # MV WAITING
        # =================================================

        try:

            print("\n--- FETCHING MV WAITING ---")

            cur.execute("""
                SELECT
                    COALESCE(vcn_doc_num, '') || ' / ' || COALESCE(vessel_name, '') AS vessel_name
                FROM ldud_header
                WHERE nor_accepted IS NOT NULL
                AND discharge_commenced IS NULL
                ORDER BY vessel_name
            """)

            mv_waiting_rows = cur.fetchall()

            if mv_waiting_rows:

                mv_waiting_list = [
                    {
                        'vessel_name': str(row['vessel_name'])
                    }
                    for row in mv_waiting_rows
                ]

            else:

                mv_waiting_list = [
                    {
                        'vessel_name': 'Nil'
                    }
                ]

            print("MV WAITING COUNT:", len(mv_waiting_list))

        except Exception as e:

            print("MV WAITING ERROR:", str(e))
            conn.rollback()

            mv_waiting_list = [
                {
                    'vessel_name': 'Nil'
                }
            ]

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
        # MBC DISCHARGING LAST 24 HRS
        # =================================================

            # =================================================
        # MBC DISCHARGING LAST 24 HRS
        # =================================================

        try:

            print("\n--- FETCHING MBC DISCH TOTAL ---")

            cur.execute("""
                SELECT

                    ROUND(
                        COALESCE(
                            SUM(
                                COALESCE(quantity, 0)
                            )::numeric,
                            0
                        ),
                        2
                    ) AS mbc_total_mt

                FROM lueu_lines

                WHERE TO_DATE(
                    entry_date,
                    'YYYY-MM-DD'
                ) = %s
            """, (fetch_date.date(),))

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
        # BARGES COUNT
        # =================================================

        try:

            print("\n--- FETCHING BARGES COUNT ---")

            cur.execute("""
                SELECT
                    COUNT(DISTINCT barge_name)
                FROM ldud_barge_lines
            """)

            barge_row = cur.fetchone()

            barges_count = (
                barge_row[0]
                if barge_row
                else 0
            )

            print("BARGES COUNT:", barges_count)

        except Exception as e:

            print("BARGE ERROR:", str(e))
            conn.rollback()  # ← ADDED
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