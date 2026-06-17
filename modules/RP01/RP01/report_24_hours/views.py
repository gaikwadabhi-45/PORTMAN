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
from datetime import datetime, timedelta, date

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
                    )

                ORDER BY
                    h.vessel_name

            """, (
                window_end,
                window_start
            ))

            rows = cur.fetchall()

            print("MV ROWS FOUND:", len(rows))

            # ------------------------------------
            # SAME LOGIC AS ops_24h / ops_till
            # ------------------------------------

            ops_24h = {}
            ops_till = {}

            ldud_ids = [r['id'] for r in rows]

            prev_date = selected_dt.date() - timedelta(days=1)

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

                    ops_24h[r['ldud_id']] = float(
                        r['qty'] or 0
                    )

                cur.execute("""
                    SELECT
                        ldud_id,
                        COALESCE(SUM(quantity), 0) AS qty
                    FROM ldud_vessel_operations
                    WHERE ldud_id = ANY(%s)
                    AND TO_DATE(start_time, 'YYYY-MM-DD') <= %s
                    GROUP BY ldud_id
                """, (ldud_ids, prev_date))

                for r in cur.fetchall():

                    ops_till[r['ldud_id']] = float(
                        r['qty'] or 0
                    )

            # ------------------------------------
            # PROCESS VESSELS
            # ------------------------------------

            for row in rows:

                ldud_id = row['id']
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

                # ------------------------------------
                # SAME AS ops_24h
                # ------------------------------------

                discharge_24hrs = ops_24h.get(
                    ldud_id,
                    0
                )

                # ------------------------------------
                # SAME AS ops_till
                # ------------------------------------

                discharge_qty = ops_till.get(
                    ldud_id,
                    0
                )

                balance_qty = max(
                    bl_qty - discharge_qty,
                    0
                )


                # ------------------------------------
                # PREVIOUS DAY DELAYS FOR SAME VESSEL
                # ------------------------------------

                delay_text = ''

                prev_date = selected_dt.date() - timedelta(days=1)

                cur.execute("""
                    SELECT
                        delay_name,
                        COALESCE(
                            SUM(total_time_mins),
                            0
                        ) AS total_mins
                    FROM ldud_delays
                    WHERE ldud_id = %s
                    AND TO_DATE(
                        LEFT(start_datetime, 10),
                        'YYYY-MM-DD'
                    ) = %s
                    GROUP BY delay_name
                    ORDER BY delay_name
                """, (
                    ldud_id,
                    prev_date
                ))

                delay_rows = cur.fetchall()

                delay_parts = []

                for d in delay_rows:

                    delay_name = d['delay_name'] or ''

                    calculated_hrs = round(
                        float(
                            d['total_mins'] or 0
                        ) / 60,
                        2
                    )

                    delay_parts.append(
                        f"{delay_name} - {calculated_hrs} Hrs"
                    )

                delay_text = ", ".join(delay_parts)

                print(
                    "VESSEL:",
                    row['vessel_name'],
                    "LDUD:",
                    ldud_id,
                    "PREVIOUS DATE:",
                    prev_date,
                    "DELAYS:",
                    delay_text
                )

                mv_discharge_list.append({

                    'vessel_name': row['vessel_name'],

                    'cargo_name': cargo_name,

                    'discharge_24hrs': round(
                        discharge_24hrs,
                        2
                    ),

                    'balance_qty': round(
                        balance_qty,
                        2
                    ),

                    'delay_name': delay_text

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
        # BARGE SUMMARY
        # =================================================

        total_barges = 0
        cement_barges = 0
        steel_barges = 0
        barges_count = 0

        try:

            active_ldud_ids = [r['id'] for r in rows]

            print("\n========== BARGE DEBUG ==========")
            print("ACTIVE LDUD IDS:", active_ldud_ids)

            if active_ldud_ids:

                cur.execute("""
                    SELECT DISTINCT
                        TRIM(barge_name) AS barge_name,
                        TRIM(
                            UPPER(
                                COALESCE(cargo_name, '')
                            )
                        ) AS cargo_name
                    FROM ldud_barge_lines
                    WHERE ldud_id = ANY(%s)
                    AND (
                        (
                            commence_discharge_berth IS NOT NULL
                            AND cast_off_berth IS NULL
                        )
                        OR
                        (
                            along_side_berth IS NOT NULL
                            AND commence_discharge_berth IS NULL
                        )
                        OR
                        (
                            cast_off_mv IS NOT NULL
                            AND along_side_berth IS NULL
                        )
                        OR
                        (
                            commenced_loading IS NOT NULL
                            AND completed_loading IS NULL
                        )
                    )
                    ORDER BY barge_name
                """, (active_ldud_ids,))

                barge_rows = cur.fetchall()

                print(
                    "ACTIVE BARGE ROWS COUNT:",
                    len(barge_rows)
                )

                print("\n========== ACTIVE BARGES ==========")

                total_barges = len(barge_rows)

                for r in barge_rows:

                    barge_name = r['barge_name'] or ''

                    cargo = (
                        r['cargo_name'] or ''
                    ).strip().upper()

                    print(
                        f"BARGE: {barge_name} | CARGO: {cargo}"
                    )

                    # Cement Barges = CLINKER + SLAG only
                    if (
                        'CLINKER' in cargo
                        or 'SLAG' in cargo
                    ):
                        cement_barges += 1

                print("========== END ACTIVE BARGES ==========\n")

                steel_barges = (
                    total_barges - cement_barges
                )

                barges_count = total_barges

            print("TOTAL BARGES :", total_barges)
            print("CEMENT BARGES:", cement_barges)
            print("STEEL BARGES :", steel_barges)

            print("========== END BARGE DEBUG ==========")

        except Exception as e:

            print("BARGE ERROR:", str(e))

            conn.rollback()

            total_barges = 0
            cement_barges = 0
            steel_barges = 0
            barges_count = 0

        # =================================================
        # STEEL / CEMENT CARGO TOTAL
        # =================================================

        steel_cargo = 0
        cement_cargo = 0

        try:

            fetch_date = (
                datetime.strptime(
                    selected_date,
                    '%Y-%m-%d'
                ).date()
                - timedelta(days=1)
            )

            cur.execute("""
                SELECT

                    COALESCE(
                        vc.cargo_type,
                        'OTHERS'
                    ) AS cargo_type,

                    COALESCE(
                        SUM(l.quantity),
                        0
                    ) AS qty

                FROM lueu_lines l

                LEFT JOIN vessel_cargo vc
                    ON UPPER(TRIM(vc.cargo_name))
                    = UPPER(TRIM(l.cargo_name))

                WHERE
                    l.is_deleted = false
                    AND TO_DATE(
                        l.entry_date,
                        'YYYY-MM-DD'
                    ) = %s

                GROUP BY
                    COALESCE(
                        vc.cargo_type,
                        'OTHERS'
                    )

            """, (fetch_date,))

            cargo_rows = cur.fetchall()

            total_qty = 0
            clinker_qty = 0
            slag_qty = 0

            for r in cargo_rows:

                cargo_type = (
                    r['cargo_type']
                    or ''
                ).upper()

                qty = float(
                    r['qty']
                    or 0
                )

                total_qty += qty

                if cargo_type == 'Clinker':
                    clinker_qty += qty

                elif cargo_type == 'Slag':
                    slag_qty += qty

            # Same logic as throughput report

            cement_cargo = (
                clinker_qty
                + slag_qty
            )

            steel_cargo = (
                total_qty
                - clinker_qty
                - slag_qty
            )

            print(
                "CLINKER QTY:",
                clinker_qty
            )

            print(
                "SLAG QTY:",
                slag_qty
            )

            print(
                "CEMENT CARGO:",
                cement_cargo
            )

            print(
                "STEEL CARGO:",
                steel_cargo
            )

        except Exception as e:

            print(
                "CARGO TOTAL ERROR:",
                str(e)
            )

            cement_cargo = 0
            steel_cargo = 0

        # =================================================
        # JETTY HANDLING
        # =================================================

        try:

            print("\n--- FETCHING JETTY HANDLING ---")

            target_date = fetch_date

            print("TARGET DATE:", target_date)

            month_start = date(
                target_date.year,
                target_date.month,
                1
            )

            if target_date.month >= 4:
                fy_start = date(
                    target_date.year,
                    4,
                    1
                )
            else:
                fy_start = date(
                    target_date.year - 1,
                    4,
                    1
                )

            cur.execute("""
                WITH hist AS (

                    SELECT
                        entry_date,
                        SUM(quantity) qty
                    FROM rp01_historical_lueu
                    WHERE cargo_name IS NOT NULL
                    GROUP BY entry_date

                ),

                live AS (

                    SELECT
                        TO_DATE(entry_date,'YYYY-MM-DD') AS entry_date,
                        SUM(quantity) qty
                    FROM lueu_lines
                    WHERE is_deleted = false
                    AND cargo_name IS NOT NULL
                    GROUP BY TO_DATE(entry_date,'YYYY-MM-DD')

                ),

                throughput AS (

                    SELECT
                        h.entry_date,
                        h.qty
                    FROM hist h

                    UNION ALL

                    SELECT
                        l.entry_date,
                        l.qty
                    FROM live l
                    WHERE NOT EXISTS (
                        SELECT 1
                        FROM hist h
                        WHERE h.entry_date = l.entry_date
                    )
                )

                SELECT

                    COALESCE(
                        SUM(
                            CASE
                                WHEN entry_date = %s
                                THEN qty
                                ELSE 0
                            END
                        ),
                        0
                    ) AS day_qty,

                    COALESCE(
                        SUM(
                            CASE
                                WHEN entry_date BETWEEN %s AND %s
                                THEN qty
                                ELSE 0
                            END
                        ),
                        0
                    ) AS month_qty,

                    COALESCE(
                        SUM(
                            CASE
                                WHEN entry_date BETWEEN %s AND %s
                                THEN qty
                                ELSE 0
                            END
                        ),
                        0
                    ) AS year_qty

                FROM throughput

            """, (
                target_date,
                month_start,
                target_date,
                fy_start,
                target_date
            ))

            row = cur.fetchone()

            jetty_today = float(row['day_qty'] or 0)
            jetty_mtd = float(row['month_qty'] or 0)
            jetty_ytd = float(row['year_qty'] or 0)

            print("DAY QTY   :", jetty_today)
            print("MONTH QTY :", jetty_mtd)
            print("YEAR QTY  :", jetty_ytd)

        except Exception as e:

            print("JETTY ERROR:", str(e))

            conn.rollback()

            jetty_today = 0
            jetty_mtd = 0
            jetty_ytd = 0

            # -----------------------------------------
        # JETTY CARGO BREAKDOWN
        # -----------------------------------------

        # Previous day's data
        target_date = datetime.strptime(
            selected_date,
            '%Y-%m-%d'
        ).date() - timedelta(days=1)

        cur.execute("""
            SELECT
                l.cargo_name,
                COALESCE(SUM(l.quantity), 0) AS qty

            FROM lueu_lines l

            WHERE l.entry_date = %s
            AND l.quantity > 0
            AND l.cargo_name IS NOT NULL
            AND l.cargo_name != ''

            GROUP BY l.cargo_name

            ORDER BY qty DESC

        """, (target_date.strftime('%Y-%m-%d'),))

        jetty_cargo_list = [
            {
                'cargo_name': r['cargo_name'],
                'quantity': float(r['qty'] or 0)
            }
            for r in cur.fetchall()
        ]

        print("TARGET DATE:", target_date)
        print("JETTY CARGO LIST:", jetty_cargo_list)

        # =================================================
        # RHMS, NO CARGO & MAINTENANCE DELAYS
        # =================================================

        delay_rows = []

        rhms_delay_hours = 0
        no_cargo_hours = 0
        maintenance_delay_hours = 0

        try:

            print("\n--- FETCHING DELAYS ---")

            cur.execute("""
                SELECT
                    COALESCE(d.type, 'Other') AS delay_type,
                    l.delay_name,
                    l.from_time,
                    l.to_time

                FROM lueu_lines l

                LEFT JOIN port_delay_types d
                    ON d.name = l.delay_name

                WHERE l.entry_date = %s
                AND l.delay_name IS NOT NULL
                AND l.delay_name != ''

            """, (fetch_date.strftime('%Y-%m-%d'),))

            rows = cur.fetchall()

            for row in rows:

                delay_type = (row['delay_type'] or '').strip()
                delay_name = (row['delay_name'] or '').strip().upper()

                from_t = (row['from_time'] or '').strip()
                to_t = (row['to_time'] or '').strip()

                if not from_t or not to_t:
                    continue

                try:

                    start = datetime.strptime(
                        from_t,
                        '%H:%M'
                    )

                    end = datetime.strptime(
                        to_t,
                        '%H:%M'
                    )

                    minutes = (
                        end - start
                    ).total_seconds() / 60

                    if minutes < 0:
                        minutes += (24 * 60)

                    hours = minutes / 60

                except Exception:
                    continue

                if delay_name == 'NO CARGO':

                    no_cargo_hours += hours

                elif delay_type == 'RMHS Delays':

                    rhms_delay_hours += hours

                elif delay_type == 'Maintenance Delays':

                    maintenance_delay_hours += hours

            rhms_delay_hours = round(rhms_delay_hours, 2)
            no_cargo_hours = round(no_cargo_hours, 2)
            maintenance_delay_hours = round(maintenance_delay_hours, 2)

            delay_rows = [

                {
                    'delay_name': 'RHMS Delays',
                    'total_hours': rhms_delay_hours
                },

                {
                    'delay_name': 'No Cargo',
                    'total_hours': no_cargo_hours
                },

                {
                    'delay_name': 'Maintenance Delays',
                    'total_hours': maintenance_delay_hours
                }
            ]

            print("RHMS DELAYS:", rhms_delay_hours)
            print("NO CARGO:", no_cargo_hours)
            print("MAINTENANCE DELAYS:", maintenance_delay_hours)

        except Exception as e:

            print("DELAY ERROR:", str(e))

            delay_rows = []

            rhms_delay_hours = 0
            no_cargo_hours = 0
            maintenance_delay_hours = 0

        response = {

            'success': True,

            'selected_date': datetime.strptime(
                selected_date,
                '%Y-%m-%d'
            ).strftime('%d/%m/%Y'),

            'fetch_date': fetch_date.strftime('%Y-%m-%d'),

            'mv_disch': str(mv_disch),
            'mv_total_days': str(mv_total_days),       # ← ADDED
            'mv_discharge_list': mv_discharge_list,
            'mv_waiting_list': mv_waiting_list,        # ← ADDED

            'barges_count': str(barges_count),
            'cement_barges': cement_barges,
            'steel_barges': steel_barges,
            'cement_cargo': round(cement_cargo, 2),
            'steel_cargo': round(steel_cargo, 2),

            'mbc_waiting': mbc_waiting_rows,
            'mbc_disch_total': str(mbc_disch_total),   # ← ADDED

            'jetty_today': str(jetty_today),
            'jetty_mtd': str(jetty_mtd),
            'jetty_ytd': str(jetty_ytd),
            
            'jetty_cargo_list': jetty_cargo_list, 

            'rhms_delay_hours': rhms_delay_hours,
            'maintenance_delay_hours': maintenance_delay_hours,
            'no_cargo_hours': no_cargo_hours,
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