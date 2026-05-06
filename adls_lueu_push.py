"""
RP01 Custom Report Designer → Azure Data Lake Storage (ADLS) push utility.

Pulls ALL rows from each of the RP01 custom report designer data sources
(mbc-ops, vessel-ops, vessel-barge, lueu-equipment, mbc-tat) using the
same SQL & post-processing as the in-app pivot endpoint, converts each
to CSV, and uploads to ADLS.

No date filter, no LIMIT — full table dumps for downstream analytics.

Designed to be scheduled via Windows Task Scheduler.
Logs are written to D:\\DHRISTI\\logs\\rp01_push_YYYYMMDD.log
"""

import sys
import logging
import traceback
from datetime import datetime, date
from io import BytesIO
from pathlib import Path

import pandas as pd
import psycopg2
import psycopg2.extras
from azure.identity import ClientSecretCredential
from azure.storage.filedatalake import DataLakeFileClient

# ── Project config (DATABASE_URL) ────────────────────────────────────────────
sys.path.insert(0, str(Path(__file__).parent))
from config import DATABASE_URL

# ─────────────────────────────────────────────────────────────────────────────
# ADLS CONFIG
# ─────────────────────────────────────────────────────────────────────────────
ACCOUNT_NAME  = "stdincldl01"
CONTAINER     = "std-inc-ldl-dfs-001"
TENANT_ID     = "1250f2eb-4784-4223-98dc-d6e33445565c"
CLIENT_ID     = "19b5a145-6002-4f0b-9762-7cd08165d8a4"
CLIENT_SECRET = ""

ADLS_BASE = "landing/iportman/incremental"

# ─────────────────────────────────────────────────────────────────────────────
# LOGGING  (file + console)
# ─────────────────────────────────────────────────────────────────────────────
LOG_DIR = Path(r"D:\DHRISTI\logs")
LOG_DIR.mkdir(parents=True, exist_ok=True)

log_file = LOG_DIR / f"rp01_push_{datetime.now().strftime('%Y%m%d')}.log"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(log_file, encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ]
)
log = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# SQL — mirrors modules/RP01/RP01/custom_report/views.py, WHERE & LIMIT removed
# ─────────────────────────────────────────────────────────────────────────────

MBC_OPS_SQL = """
SELECT
    h.doc_num                                           AS "Doc No",
    COALESCE(h.doc_series, '')                          AS "Doc Series",
    COALESCE(h.mbc_name, '')                            AS "MBC Name",
    COALESCE(h.operation_type, '')                      AS "Operation Type",
    COALESCE(h.cargo_type, '')                          AS "Cargo Type",
    COALESCE(h.cargo_name, '')                          AS "Cargo Name",
    COALESCE(vc.cargo_category, '')                     AS "Cargo Category",
    COALESCE(vc.cargo_category_2, '')                   AS "Cargo Category 2",
    COALESCE(vc.cargo_sub_category, '')                 AS "Cargo Sub Category",
    COALESCE(vc.cargo_sub_category_2, '')               AS "Cargo Sub Category 2",
    COALESCE(h.bl_quantity, 0)                          AS "BL Qty",
    COALESCE(h.quantity_uom, '')                        AS "UOM",
    COALESCE(h.doc_status, '')                          AS "Status",
    COALESCE(h.created_by, '')                          AS "Created By",
    COALESCE(
        STRING_AGG(DISTINCT cd.customer_name, ', ')
            FILTER (WHERE cd.customer_name IS NOT NULL),
    '')                                                 AS "Customer",
    COALESCE(elp.unloaded_by, '')                       AS "LP Unloaded By (Export)",
    COALESCE(elp.berth_master, '')                      AS "LP Berth Master (Export)",
    COALESCE(dp.vessel_unloaded_by, '')                 AS "DP Vessel Unloaded By",
    COALESCE(dp.vessel_unloading_berth, '')             AS "DP Unloading Berth",
    COALESCE(dp.discharge_stop_shifting, '')            AS "DP Stop Shifting",
    COALESCE(dp.discharge_start_shifting, '')           AS "DP Start Shifting",
    COALESCE(h.doc_date::TEXT, '')                      AS "Doc Date",
    COALESCE(LEFT(h.doc_date::TEXT, 4), '')             AS "Year",
    COALESCE(LEFT(h.doc_date::TEXT, 7), '')             AS "Year-Month"
FROM mbc_header h
LEFT JOIN mbc_load_port_lines        lp  ON lp.mbc_id  = h.id
LEFT JOIN mbc_export_load_port_lines elp ON elp.mbc_id = h.id
LEFT JOIN mbc_discharge_port_lines   dp  ON dp.mbc_id  = h.id
LEFT JOIN mbc_customer_details       cd  ON cd.mbc_id  = h.id
LEFT JOIN LATERAL (
    SELECT cargo_category, cargo_category_2, cargo_sub_category, cargo_sub_category_2
    FROM vessel_cargo WHERE cargo_name = h.cargo_name LIMIT 1
) vc ON TRUE
GROUP BY h.id, h.doc_date, lp.id, elp.id, dp.id,
         vc.cargo_category, vc.cargo_category_2,
         vc.cargo_sub_category, vc.cargo_sub_category_2
ORDER BY h.id ASC
"""

VESSEL_OPS_SQL = """
SELECT
    h.doc_num                                           AS "Doc No",
    h.vcn_doc_num                                       AS "VCN No",
    COALESCE(h.vessel_name, '')                         AS "Vessel",
    COALESCE(v.operation_type, h.operation_type, '')    AS "Operation Type",
    COALESCE(v.vessel_agent_name, '')                   AS "Vessel Agent",
    COALESCE(STRING_AGG(DISTINCT cd.cargo_name, ', '), '') AS "Cargo",
    COALESCE(ROUND(CAST(SUM(cd.bl_quantity) AS NUMERIC), 0), 0) AS "BL Qty (MT)",
    CASE
        WHEN NULLIF(h.discharge_commenced, '') IS NOT NULL
         AND NULLIF(h.discharge_completed,  '') IS NOT NULL
        THEN ROUND(CAST(
            EXTRACT(EPOCH FROM (
                CAST(h.discharge_completed  AS TIMESTAMP) -
                CAST(h.discharge_commenced  AS TIMESTAMP)
            )) / 86400.0 AS NUMERIC
        ), 2)
        ELSE NULL
    END                                                 AS "Actual Days",
    COALESCE(h.doc_status, '')                          AS "Status",
    COALESCE(LEFT(h.nor_tendered::TEXT, 10), '')        AS "NOR Date",
    COALESCE(LEFT(h.nor_tendered::TEXT, 4), '')         AS "Year",
    COALESCE(LEFT(h.nor_tendered::TEXT, 7), '')         AS "Year-Month"
FROM ldud_header h
LEFT JOIN vcn_header v ON v.id = h.vcn_id
LEFT JOIN vcn_cargo_declaration cd ON cd.vcn_id = h.vcn_id
GROUP BY h.id, h.doc_num, h.vcn_doc_num, h.vessel_name,
         v.operation_type, h.operation_type, v.vessel_agent_name,
         h.nor_tendered, h.discharge_commenced, h.discharge_completed, h.doc_status
ORDER BY h.nor_tendered ASC
"""

VESSEL_BARGE_SQL = """
SELECT
    h.doc_num                                               AS "Doc No",
    COALESCE(h.vcn_doc_num, '')                            AS "VCN No",
    COALESCE(h.vessel_name, '')                            AS "Vessel",
    COALESCE(v.operation_type, h.operation_type, '')       AS "Operation Type",
    COALESCE(v.vessel_agent_name, '')                      AS "Vessel Agent",
    COALESCE(h.doc_status, '')                             AS "Status",
    COALESCE(h.created_by, '')                             AS "Created By",
    COALESCE(h.initial_draft_survey_quantity::TEXT, '')    AS "Initial Draft Survey Qty",
    COALESCE(bl.trip_number::TEXT, '')                     AS "Trip No",
    COALESCE(bl.hold_name, '')                             AS "Hold",
    COALESCE(bl.barge_name, '')                            AS "Barge",
    COALESCE(bl.contractor_name, '')                       AS "Contractor",
    COALESCE(bl.cargo_name, '')                            AS "Cargo",
    COALESCE(bl.bpt_bfl, '')                               AS "BPT/BFL",
    COALESCE(bl.discharge_quantity::TEXT, '')              AS "Discharge Qty",
    COALESCE(bl.crane_loaded_from, '')                     AS "Crane Loaded From",
    COALESCE(bl.port_crane, '')                            AS "Port Crane",
    COALESCE(vc.cargo_type, '')                            AS "Cargo Type",
    COALESCE(vc.cargo_category, '')                        AS "Cargo Category",
    COALESCE(vc.cargo_category_2, '')                      AS "Cargo Category 2",
    COALESCE(vc.cargo_sub_category, '')                    AS "Cargo Sub Category",
    COALESCE(vc.cargo_sub_category_2, '')                  AS "Cargo Sub Category 2",
    COALESCE(LEFT(h.nor_tendered::TEXT, 10), '')           AS "NOR Date",
    COALESCE(LEFT(h.nor_tendered::TEXT, 4), '')            AS "Year",
    COALESCE(LEFT(h.nor_tendered::TEXT, 7), '')            AS "Year-Month"
FROM ldud_header h
LEFT JOIN vcn_header v ON v.id = h.vcn_id
LEFT JOIN ldud_barge_lines bl ON bl.ldud_id = h.id
LEFT JOIN LATERAL (
    SELECT cargo_type, cargo_category, cargo_category_2, cargo_sub_category, cargo_sub_category_2
    FROM vessel_cargo WHERE cargo_name = bl.cargo_name LIMIT 1
) vc ON TRUE
ORDER BY h.nor_tendered ASC, h.id, bl.trip_number
"""

LUEU_SQL = """
SELECT
    COALESCE(l.equipment_name, '')          AS "Equipment",
    COALESCE(l.shift, '')                   AS "Shift",
    COALESCE(l.source_display, '')          AS "VCN / MBC",
    COALESCE(l.barge_name, '')              AS "Barge / MBC Name",
    COALESCE(l.cargo_name, '')              AS "Cargo",
    COALESCE(l.delay_name, '')              AS "Delay",
    COALESCE(l.system_name, '')             AS "System",
    COALESCE(l.route_name, '')              AS "Route",
    COALESCE(l.berth_name, '')              AS "Berth",
    COALESCE(l.shift_incharge, '')          AS "Shift Incharge",
    COALESCE(l.operator_name, '')           AS "Operator",
    COALESCE(l.quantity_uom, '')            AS "UOM",
    COALESCE(CAST(l.quantity AS TEXT), '')  AS "Quantity",
    COALESCE(l.from_time, '')               AS "_from_time",
    COALESCE(l.to_time, '')                 AS "_to_time",
    COALESCE(pdt.to_sof, '')                AS "Delay To SOF",
    COALESCE(pdt.type, '')                  AS "Delay Type",
    COALESCE(vc.cargo_type, '')             AS "Cargo Type",
    COALESCE(vc.cargo_category, '')         AS "Cargo Category",
    COALESCE(vc.cargo_category_2, '')       AS "Cargo Category 2",
    COALESCE(vc.cargo_sub_category, '')     AS "Cargo Sub Category",
    COALESCE(vc.cargo_sub_category_2, '')   AS "Cargo Sub Category 2",
    COALESCE(l.entry_date::TEXT, '')        AS "Date",
    COALESCE(LEFT(l.entry_date::TEXT, 4), '') AS "Year",
    COALESCE(LEFT(l.entry_date::TEXT, 7), '') AS "Year-Month"
FROM lueu_lines l
LEFT JOIN LATERAL (
    SELECT to_sof, type
    FROM port_delay_types
    WHERE name = l.delay_name
    LIMIT 1
) pdt ON TRUE
LEFT JOIN LATERAL (
    SELECT cargo_type, cargo_category, cargo_category_2,
           cargo_sub_category, cargo_sub_category_2
    FROM vessel_cargo
    WHERE cargo_name = l.cargo_name
    LIMIT 1
) vc ON TRUE
ORDER BY l.id ASC
"""

MBC_TAT_SQL = """
SELECT
    h.doc_num                                           AS doc_num,
    COALESCE(h.mbc_name, '')                           AS mbc_name,
    COALESCE(h.operation_type, '')                     AS operation_type,
    COALESCE(h.cargo_name, '')                         AS cargo_name,
    COALESCE(CAST(h.bl_quantity AS TEXT), '')          AS bl_quantity,
    COALESCE(h.doc_status, '')                         AS doc_status,
    COALESCE(h.created_by, '')                         AS created_by,
    COALESCE(vc.cargo_type, '')                        AS cargo_type,
    COALESCE(vc.cargo_category, '')                    AS cargo_category,
    COALESCE(vc.cargo_category_2, '')                  AS cargo_category_2,
    COALESCE(vc.cargo_sub_category, '')                AS cargo_sub_category,
    COALESCE(vc.cargo_sub_category_2, '')              AS cargo_sub_category_2,
    COALESCE(h.doc_date::TEXT, '')                     AS doc_date,
    lp.arrived_load_port,    lp.loading_commenced,   lp.loading_completed,
    lp.cast_off_load_port,
    dp.arrival_gull_island,  dp.departure_gull_island, dp.vessel_arrival_port,
    dp.unloading_commenced,  dp.unloading_completed,
    dp.vessel_cast_off,      dp.sailed_out_load_port
FROM mbc_header h
LEFT JOIN mbc_load_port_lines      lp ON lp.mbc_id = h.id
LEFT JOIN mbc_discharge_port_lines dp ON dp.mbc_id = h.id
LEFT JOIN LATERAL (
    SELECT cargo_type, cargo_category, cargo_category_2, cargo_sub_category, cargo_sub_category_2
    FROM vessel_cargo WHERE cargo_name = h.cargo_name LIMIT 1
) vc ON TRUE
ORDER BY h.doc_date ASC, h.id ASC
"""


# ─────────────────────────────────────────────────────────────────────────────
# POST-PROCESSORS  (mirror custom_report/views.py logic)
# ─────────────────────────────────────────────────────────────────────────────

def _calc_diff_hrs(from_t: str, to_t: str):
    """Duration in hours between two HH:MM strings (wraps midnight)."""
    try:
        fh, fm = int(from_t[:2]), int(from_t[3:5])
        th, tm = int(to_t[:2]),   int(to_t[3:5])
        from_mins = fh * 60 + fm
        to_mins   = th * 60 + tm
        diff = to_mins - from_mins if to_mins >= from_mins else 1440 - from_mins + to_mins
        return round(diff / 60, 2)
    except Exception:
        return None


def _diff_mins(row, col_from, col_to):
    """Duration in minutes between two timestamp columns in a row dict."""
    def parse(v):
        if v is None:
            return None
        if isinstance(v, datetime):
            return v
        if isinstance(v, date):
            return datetime(v.year, v.month, v.day)
        s = str(v).strip()
        if not s:
            return None
        for fmt in ('%Y-%m-%d %H:%M:%S', '%Y-%m-%d %H:%M',
                    '%Y-%m-%dT%H:%M:%S', '%Y-%m-%dT%H:%M', '%Y-%m-%d'):
            try:
                return datetime.strptime(s, fmt)
            except ValueError:
                pass
        return None

    a, b = parse(row.get(col_from)), parse(row.get(col_to))
    if not a or not b:
        return None
    delta = (b - a).total_seconds() / 60
    return round(delta, 1) if delta >= 0 else None


def _post_lueu(rows):
    for r in rows:
        r['Diff Hrs'] = _calc_diff_hrs(r.pop('_from_time', ''), r.pop('_to_time', ''))
    return rows


def _post_mbc_tat(rows):
    out = []
    for r in rows:
        out.append({
            'Doc No':                        r.get('doc_num', ''),
            'MBC Name':                      r.get('mbc_name', ''),
            'Operation Type':                r.get('operation_type', ''),
            'Cargo':                         r.get('cargo_name', ''),
            'BL Quantity':                   r.get('bl_quantity', ''),
            'Status':                        r.get('doc_status', ''),
            'Created By':                    r.get('created_by', ''),
            'Doc Date':                      r.get('doc_date', ''),
            'Year':                          r.get('doc_date', '')[:4] if r.get('doc_date') else '',
            'Year-Month':                    r.get('doc_date', '')[:7] if r.get('doc_date') else '',
            'Cargo Type':                    r.get('cargo_type', ''),
            'Cargo Category':                r.get('cargo_category', ''),
            'Cargo Category 2':              r.get('cargo_category_2', ''),
            'Cargo Sub Category':            r.get('cargo_sub_category', ''),
            'Cargo Sub Category 2':          r.get('cargo_sub_category_2', ''),
            'Preberthing (min)':             _diff_mins(r, 'arrived_load_port',     'loading_commenced'),
            'Loading Time (min)':            _diff_mins(r, 'loading_commenced',     'loading_completed'),
            'Wait After Load (min)':         _diff_mins(r, 'loading_completed',     'cast_off_load_port'),
            'Total at Jaigad (min)':         _diff_mins(r, 'arrived_load_port',     'cast_off_load_port'),
            'Transit Jaigad-Gull (min)':     _diff_mins(r, 'cast_off_load_port',    'arrival_gull_island'),
            'Gull Waiting (min)':            _diff_mins(r, 'arrival_gull_island',   'departure_gull_island'),
            'Gull-Dharamtar (min)':          _diff_mins(r, 'departure_gull_island', 'vessel_arrival_port'),
            'Jaigad-Dharamtar (min)':        _diff_mins(r, 'cast_off_load_port',    'vessel_arrival_port'),
            'Preberthing Dharamtar (min)':   _diff_mins(r, 'vessel_arrival_port',   'unloading_commenced'),
            'Unloading Time (min)':          _diff_mins(r, 'unloading_commenced',   'unloading_completed'),
            'Wait After Unload (min)':       _diff_mins(r, 'unloading_completed',   'vessel_cast_off'),
            'Total at Dharamtar (min)':      _diff_mins(r, 'vessel_arrival_port',   'vessel_cast_off'),
            'Dharamtar-Jaigad (min)':        _diff_mins(r, 'vessel_cast_off',       'sailed_out_load_port'),
            'TAT (min)':                     _diff_mins(r, 'arrived_load_port',     'sailed_out_load_port'),
        })
    return out


# ─────────────────────────────────────────────────────────────────────────────
# SOURCE REGISTRY
#   key       → (sql, post_processor_or_None, adls_subfolder, file_prefix)
# ─────────────────────────────────────────────────────────────────────────────
SOURCES = {
    'mbc-ops':        (MBC_OPS_SQL,      None,          'mbc_ops',      'mbc_ops'),
    'vessel-ops':     (VESSEL_OPS_SQL,   None,          'vessel_ops',   'vessel_ops'),
    'vessel-barge':   (VESSEL_BARGE_SQL, None,          'vessel_barge', 'vessel_barge'),
    'lueu-equipment': (LUEU_SQL,         _post_lueu,    'lueu',         'lueu_equipment'),
    'mbc-tat':        (MBC_TAT_SQL,      _post_mbc_tat, 'mbc_tat',      'mbc_tat'),
}


# ─────────────────────────────────────────────────────────────────────────────
# DB / ADLS HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def fetch_source(sql: str, post=None) -> pd.DataFrame:
    conn = psycopg2.connect(DATABASE_URL)
    cur  = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        cur.execute(sql)
        rows = [dict(r) for r in cur.fetchall()]
    finally:
        cur.close()
        conn.close()

    if post is not None:
        rows = post(rows)

    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(rows)


def df_to_csv_bytes(df: pd.DataFrame) -> bytes:
    buf = BytesIO()
    df.to_csv(buf, index=False, encoding="utf-8")
    return buf.getvalue()


def build_adls_path(*parts: str) -> str:
    """Join ADLS path fragments without producing empty path segments."""
    normalized_parts = []
    for part in parts:
        segment = str(part).strip().replace("\\", "/").strip("/")
        if segment:
            normalized_parts.append(segment)

    if not normalized_parts:
        raise ValueError("ADLS path cannot be empty")

    return "/".join(normalized_parts)


def upload_to_adls(csv_bytes: bytes, blob_path: str, credential) -> None:
    blob_path = build_adls_path(blob_path)
    file_client = DataLakeFileClient(
        account_url=f"https://{ACCOUNT_NAME}.dfs.core.windows.net",
        file_system_name=CONTAINER,
        file_path=blob_path,
        credential=credential,
    )
    file_client.create_file()
    file_client.append_data(data=csv_bytes, offset=0, length=len(csv_bytes))
    file_client.flush_data(len(csv_bytes))


# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────

def main():
    start = datetime.now()
    timestamp = start.strftime("%Y%m%d_%H%M%S")

    log.info("=" * 60)
    log.info("RP01 → ADLS push started")
    log.info(f"Run time : {start.strftime('%Y-%m-%d %H:%M:%S')}")
    log.info(f"Sources  : {', '.join(SOURCES.keys())}")
    log.info("=" * 60)

    log.info("Authenticating with Azure...")
    credential = ClientSecretCredential(
        tenant_id=TENANT_ID,
        client_id=CLIENT_ID,
        client_secret=CLIENT_SECRET,
    )

    failures = []

    for key, (sql, post, subfolder, prefix) in SOURCES.items():
        log.info("-" * 60)
        log.info(f"[{key}] starting")
        try:
            df = fetch_source(sql, post)

            if df.empty:
                log.warning(f"[{key}] no rows returned — skipping upload")
                continue

            csv_bytes = df_to_csv_bytes(df)
            blob_path = build_adls_path(
                ADLS_BASE,
                subfolder,
                f"{prefix}_{timestamp}.csv",
            )

            log.info(f"[{key}] rows={len(df):,}  cols={len(df.columns)}  size={len(csv_bytes):,}B")
            log.info(f"[{key}] target: {blob_path}")

            upload_to_adls(csv_bytes, blob_path, credential)

            full_path = (
                f"abfss://{CONTAINER}@{ACCOUNT_NAME}.dfs.core.windows.net/{blob_path}"
            )
            log.info(f"[{key}] uploaded → {full_path}")

        except Exception:
            log.error(f"[{key}] FAILED")
            log.error(traceback.format_exc())
            failures.append(key)

    elapsed = (datetime.now() - start).total_seconds()
    log.info("=" * 60)
    if failures:
        log.error(f"COMPLETED in {elapsed:.1f}s with FAILURES: {', '.join(failures)}")
        log.info("=" * 60)
        sys.exit(1)
    else:
        log.info(f"COMPLETED in {elapsed:.1f}s — all sources uploaded")
        log.info("=" * 60)


if __name__ == "__main__":
    main()
