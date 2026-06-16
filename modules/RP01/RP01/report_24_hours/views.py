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

            cur.execute("""
                SELECT

                    COALESCE(h.doc_num, '') AS doc_num,

                    COALESCE(
                        h.vcn_doc_num,
                        ''
                    ) || ' / ' || COALESCE(
                        h.vessel_name,
                        ''
                    ) AS vessel_name,

                    COALESCE(
                        a.anchorage_name,
                        ''
                    ) AS anchorage_name,

                    COALESCE(
                        (
                            SELECT COALESCE(SUM(vo.quantity), 0)
                            FROM ldud_vessel_operations vo
                            WHERE vo.ldud_id = h.id
                            AND vo.start_time::date = %s
                        ),
                        0
                    ) AS discharge_24h,

                    COALESCE(
                        (
                            SELECT ROUND(
                                SUM(cd.bl_quantity)::numeric,
                                2
                            )
                            FROM vcn_cargo_declaration cd
                            WHERE cd.vcn_id = h.vcn_id
                        ),
                        0
                    ) AS bl_quantity,

                    a.discharge_started,

                    a.discharge_commenced,

                    ROUND(
                        EXTRACT(
                            EPOCH FROM (
                                a.discharge_commenced
                                -
                                a.discharge_started
                            )
                        ) / 3600,
                        2
                    ) AS total_hours,

                    ROUND(
                        EXTRACT(
                            EPOCH FROM (
                                a.discharge_commenced
                                -
                                a.discharge_started
                            )
                        ) / 86400,
                        2
                    ) AS total_days

                FROM ldud_anchorage a

                LEFT JOIN ldud_header h
                    ON h.id = a.ldud_id

                WHERE DATE(a.discharge_started) = %s

                ORDER BY h.vessel_name

            """, (
                fetch_date.date(),
                fetch_date.date()
            ))

            mv_rows = cur.fetchall()

            print("MV ROW COUNT:", len(mv_rows))

            mv_disch = 0
            mv_total_days = 0

            mv_discharge_list = []

            for row in mv_rows:

                print("ROW DATA:", row)

                qty = float(
                    row['discharge_24h'] or 0
                )

                days = float(
                    row['total_days'] or 0
                )

                mv_disch += qty
                mv_total_days += days

                mv_discharge_list.append({

                    'doc_num': str(
                        row['doc_num']
                    ),

                    'vessel_name': str(
                        row['vessel_name']
                    ),

                    'anchorage_name': str(
                        row['anchorage_name']
                    ),

                    # 24 HRS DISCHARGE
                    'cargo_quantity': str(qty),

                    'bl_quantity': str(
                        float(
                            row['bl_quantity'] or 0
                        )
                    ),

                    'discharge_started': (
                        str(row['discharge_started'])
                        if row['discharge_started']
                        else ''
                    ),

                    'discharge_commenced': (
                        str(row['discharge_commenced'])
                        if row['discharge_commenced']
                        else ''
                    ),

                    'total_hours': str(
                        row['total_hours'] or 0
                    ),

                    'total_days': str(
                        row['total_days'] or 0
                    )

                })

            mv_disch = round(
                mv_disch,
                2
            )

            mv_total_days = round(
                mv_total_days,
                2
            )

            print(
                "TOTAL MV DISCH (24 HRS):",
                mv_disch
            )

            print(
                "TOTAL MV DAYS:",
                mv_total_days
            )

            print(
                "MV DISCHARGE LIST:",
                mv_discharge_list
            )

        except Exception as e:

            print(
                "MV DISCHARGE ERROR:",
                str(e)
            )

            mv_disch = 0
            mv_total_days = 0
            mv_discharge_list = []
        # =================================================
        # MV WAITING  ← FIXED: use doc_status column
        # =================================================

        try:

            print("\n--- FETCHING MV WAITING ---")

            cur.execute("""
                SELECT
                    COALESCE(h.vcn_doc_num, '') || ' / ' || COALESCE(h.vessel_name, '') AS vessel_name
                FROM ldud_header h
                WHERE h.doc_status = 'Pending'
                ORDER BY h.vessel_name
            """)

            mv_waiting_rows = cur.fetchall()

            mv_waiting_list = [
                {'vessel_name': str(row['vessel_name'])}
                for row in mv_waiting_rows
            ]

            print("MV WAITING COUNT:", len(mv_waiting_list))

        except Exception as e:

            print("MV WAITING ERROR:", str(e))
            conn.rollback()  # ← ADDED: recover transaction
            mv_waiting_list = []

        # =================================================
        # MBC WAITING
        # =================================================

        try:

            print("\n--- FETCHING MBC WAITING ---")

            cur.execute("""
                SELECT
                    mbc_name,
                    cargo_name
                FROM mbc_header
            """)

            mbc_waiting_rows = cur.fetchall()

            print("MBC ROW COUNT:", len(mbc_waiting_rows))

        except Exception as e:

            print("MBC ERROR:", str(e))
            conn.rollback()  # ← ADDED
            mbc_waiting_rows = []

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

            'mbc_waiting': safe_mbc_waiting,
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