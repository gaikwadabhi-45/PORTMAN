"""
LUEU Equipment Data → Azure Data Lake Storage (ADLS) push utility.

Pulls ALL lueu_lines data (same columns as the custom report designer
'lueu-equipment' source), converts to CSV, and uploads to ADLS.

Designed to be scheduled via Windows Task Scheduler.
Logs are written to D:\\DHRISTI\\logs\\lueu_push_YYYYMMDD.log
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

ADLS_FOLDER   = "landing/iportman/lueu"

# ─────────────────────────────────────────────────────────────────────────────
# LOGGING  (file + console)
# ─────────────────────────────────────────────────────────────────────────────
LOG_DIR = Path(r"D:\DHRISTI\logs")
LOG_DIR.mkdir(parents=True, exist_ok=True)

log_file = LOG_DIR / f"lueu_push_{datetime.now().strftime('%Y%m%d')}.log"

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
# SQL  — same as custom report designer 'lueu-equipment', no date filter,
#        no LIMIT, _from_time / _to_time kept for Diff Hrs computation
# ─────────────────────────────────────────────────────────────────────────────
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


# ─────────────────────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def _calc_diff_hrs(from_t: str, to_t: str):
    """Compute duration in hours between two HH:MM strings (wraps midnight)."""
    try:
        fh, fm = int(from_t[:2]), int(from_t[3:5])
        th, tm = int(to_t[:2]),   int(to_t[3:5])
        from_mins = fh * 60 + fm
        to_mins   = th * 60 + tm
        diff = to_mins - from_mins if to_mins >= from_mins else 1440 - from_mins + to_mins
        return round(diff / 60, 2)
    except Exception:
        return None


def fetch_lueu() -> pd.DataFrame:
    log.info("Connecting to database...")
    conn = psycopg2.connect(DATABASE_URL)
    cur  = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        log.info("Executing LUEU query (full table, no date filter)...")
        cur.execute(LUEU_SQL)
        rows = cur.fetchall()
        log.info(f"Fetched {len(rows)} rows from lueu_lines")
    finally:
        cur.close()
        conn.close()

    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame([dict(r) for r in rows])

    # Compute Diff Hrs from _from_time / _to_time, then drop the raw cols
    df["Diff Hrs"] = df.apply(
        lambda r: _calc_diff_hrs(r.get("_from_time", ""), r.get("_to_time", "")),
        axis=1
    )
    df.drop(columns=["_from_time", "_to_time"], inplace=True)

    return df


def df_to_csv_bytes(df: pd.DataFrame) -> bytes:
    buf = BytesIO()
    df.to_csv(buf, index=False, encoding="utf-8")
    return buf.getvalue()


def upload_to_adls(csv_bytes: bytes, blob_path: str) -> None:
    log.info("Authenticating with Azure...")
    credential = ClientSecretCredential(
        tenant_id=TENANT_ID,
        client_id=CLIENT_ID,
        client_secret=CLIENT_SECRET,
    )

    file_client = DataLakeFileClient(
        account_url=f"https://{ACCOUNT_NAME}.dfs.core.windows.net",
        file_system_name=CONTAINER,
        file_path=blob_path,
        credential=credential,
    )

    log.info(f"Creating file at: {blob_path}")
    file_client.create_file()

    log.info(f"Uploading {len(csv_bytes):,} bytes...")
    file_client.append_data(data=csv_bytes, offset=0, length=len(csv_bytes))
    file_client.flush_data(len(csv_bytes))

    full_path = (
        f"abfss://{CONTAINER}@{ACCOUNT_NAME}.dfs.core.windows.net/{blob_path}"
    )
    log.info(f"Upload successful → {full_path}")


# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────

def main():
    start = datetime.now()
    log.info("=" * 60)
    log.info("LUEU → ADLS push started")
    log.info(f"Run time : {start.strftime('%Y-%m-%d %H:%M:%S')}")
    log.info("=" * 60)

    try:
        df = fetch_lueu()

        if df.empty:
            log.warning("No data found in lueu_lines — nothing to upload.")
            return

        csv_bytes = df_to_csv_bytes(df)

        timestamp = start.strftime("%Y%m%d_%H%M%S")
        blob_path = f"{ADLS_FOLDER}/lueu_equipment_{timestamp}.csv"

        log.info(f"Rows    : {len(df):,}")
        log.info(f"Columns : {len(df.columns)}")
        log.info(f"Size    : {len(csv_bytes):,} bytes")
        log.info(f"Target  : {blob_path}")

        upload_to_adls(csv_bytes, blob_path)

        elapsed = (datetime.now() - start).total_seconds()
        log.info("=" * 60)
        log.info(f"COMPLETED in {elapsed:.1f}s")
        log.info("=" * 60)

    except Exception:
        log.error("=" * 60)
        log.error("PUSH FAILED")
        log.error(traceback.format_exc())
        log.error("=" * 60)
        sys.exit(1)


if __name__ == "__main__":
    main()
