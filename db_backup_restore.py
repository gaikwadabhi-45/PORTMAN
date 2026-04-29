"""
Database backup and restore utility for PORTMAN.

Usage:
  python db_backup_restore.py backup             # dump all tables to JSON
  python db_backup_restore.py restore <file>     # restore from a backup file
"""

import sys
import json
import datetime
import decimal
from pathlib import Path

from sqlalchemy import create_engine, text, inspect

sys.path.insert(0, str(Path(__file__).parent))
from config import DATABASE_URL


def _serialise(value):
    """Convert non-JSON-native types to strings."""
    if isinstance(value, (datetime.datetime, datetime.date, datetime.time)):
        return value.isoformat()
    if isinstance(value, decimal.Decimal):
        return float(value)
    if isinstance(value, bytes):
        return value.hex()
    return value


def _row_to_dict(row, columns):
    return {col: _serialise(val) for col, val in zip(columns, row)}


# ---------------------------------------------------------------------------
# BACKUP
# ---------------------------------------------------------------------------

def backup():
    engine = create_engine(DATABASE_URL)
    inspector = inspect(engine)
    tables = inspector.get_table_names()

    dump = {
        "created_at": datetime.datetime.now().isoformat(),
        "database_url_hint": DATABASE_URL.split("@")[-1],  # host/db only, no creds
        "tables": {}
    }

    with engine.connect() as conn:
        for table in sorted(tables):
            result = conn.execute(text(f'SELECT * FROM "{table}"'))
            columns = list(result.keys())
            rows = [_row_to_dict(row, columns) for row in result.fetchall()]
            dump["tables"][table] = {"columns": columns, "rows": rows}
            print(f"  {table:50s} {len(rows):>6} rows")

    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = Path(__file__).parent / f"backup_{timestamp}.json"
    out_path.write_text(json.dumps(dump, indent=2), encoding="utf-8")
    print(f"\nBackup saved to: {out_path}")
    return out_path


# ---------------------------------------------------------------------------
# RESTORE
# ---------------------------------------------------------------------------

def restore(backup_file: str):
    path = Path(backup_file)
    if not path.exists():
        print(f"ERROR: File not found: {path}")
        sys.exit(1)

    dump = json.loads(path.read_text(encoding="utf-8"))
    engine = create_engine(DATABASE_URL)
    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names())

    print(f"Restoring from: {path}")
    print(f"Backup created: {dump['created_at']}\n")

    confirm = input("This will TRUNCATE and reload all tables. Type YES to continue: ")
    if confirm.strip() != "YES":
        print("Aborted.")
        sys.exit(0)

    with engine.begin() as conn:
        # Disable FK checks during restore
        conn.execute(text("SET session_replication_role = 'replica'"))

        for table, data in dump["tables"].items():
            if table not in existing_tables:
                print(f"  SKIP (table does not exist): {table}")
                continue

            columns = data["columns"]
            rows = data["rows"]

            conn.execute(text(f'TRUNCATE TABLE "{table}" RESTART IDENTITY CASCADE'))

            if rows:
                col_list = ", ".join(f'"{c}"' for c in columns)
                placeholders = ", ".join(f":{c}" for c in columns)
                insert_sql = text(
                    f'INSERT INTO "{table}" ({col_list}) VALUES ({placeholders})'
                )
                # Restore bytes columns from hex
                typed_rows = []
                for row in rows:
                    typed_row = {}
                    for col, val in row.items():
                        # Re-encode hex strings back to bytes for BYTEA columns
                        col_info = next(
                            (c for c in inspector.get_columns(table) if c["name"] == col),
                            None
                        )
                        if col_info and "BYTEA" in str(col_info.get("type", "")).upper() and val is not None:
                            typed_row[col] = bytes.fromhex(val)
                        else:
                            typed_row[col] = val
                    typed_rows.append(typed_row)

                conn.execute(insert_sql, typed_rows)

            print(f"  {table:50s} {len(rows):>6} rows restored")

        conn.execute(text("SET session_replication_role = 'origin'"))

    print("\nRestore complete.")


# ---------------------------------------------------------------------------
# ENTRY POINT
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    if len(sys.argv) < 2 or sys.argv[1] not in ("backup", "restore"):
        print(__doc__)
        sys.exit(1)

    if sys.argv[1] == "backup":
        backup()

    elif sys.argv[1] == "restore":
        if len(sys.argv) < 3:
            print("Usage: python db_backup_restore.py restore <backup_file.json>")
            sys.exit(1)
        restore(sys.argv[2])
