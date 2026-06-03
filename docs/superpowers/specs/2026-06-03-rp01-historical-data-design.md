# RP01 Historical Data — Design

**Date:** 2026-06-03
**Status:** Approved (pending spec review)
**Module:** RP01 (Reports & Dashboards)

## 1. Background & Goal

Older LUEU operational data (equipment time-slices: unloading quantities and
delays) exists only in legacy Excel logsheets and was never entered into LUEU01.
Management decided **not** to migrate that data into the live `lueu_lines` /
VCN / MBC / LDUD records. Instead, RP01 needs a way to load this backdated data
as **hardcoded reference data** so it can be included in reports and dashboard
widgets that analyse the LUEU dataset.

**Goal:** Add a **Historical Data** feature in RP01 where an admin uploads a
CSV/Excel (filled from an exported blank template) that is stored in a dedicated
table mirroring the LUEU structure, and is exposed as a **separate, opt-in data
source** in the custom report designer and dashboard widgets.

### Non-goals
- No writes to `lueu_lines`; no creation of VCN / MBC / LDUD documents.
- No doc-number requirement on the uploaded data.
- No per-date / partial replace — each upload is a **full replace**.
- No in-app row editing — the uploaded Excel master file is the source of truth.
- No change to the existing live `lueu-equipment` source or existing saved reports.
- No changes to live operational dashboards beyond offering the new source.

## 2. Data Model

New table `rp01_historical_lueu`, mirroring the **base (input) columns** of
`lueu_lines` (enrichment columns are derived at query time, never stored):

```
id              SERIAL PRIMARY KEY
entry_date      DATE         NOT NULL
shift           VARCHAR
equipment_name  VARCHAR      NOT NULL
from_time       VARCHAR(5)   -- 'HH:MM' (24-hr)
to_time         VARCHAR(5)   -- 'HH:MM' (24-hr)
source_display  VARCHAR      -- vessel name (vessel ops) / 'MBC' label text as-is
barge_name      VARCHAR      -- barge name (vessel ops) OR MBC name (MBC ops)
cargo_name      VARCHAR
delay_name      VARCHAR
system_name     VARCHAR
route_name      VARCHAR
berth_name      VARCHAR
shift_incharge  VARCHAR
operator_name   VARCHAR
quantity        NUMERIC
quantity_uom    VARCHAR
remarks         TEXT
uploaded_by     INTEGER
uploaded_at     TIMESTAMP DEFAULT NOW()
```

Created via an Alembic migration (down-revision = current head;
`CREATE TABLE IF NOT EXISTS` + `DROP TABLE` on downgrade).

## 3. New RP01 Sub-Package: `historical_data`

Follows the existing RP01 sub-feature pattern:
- New package `modules/RP01/RP01/historical_data/` with `__init__.py` +
  `views.py` registering routes on the shared `bp`.
- Imported in `modules/RP01/RP01/views.py` (alongside the other
  `from .<feature> import views as _..._views` lines).
- A page template `historical_data/historical_data.html` rendered at
  `GET /module/RP01/historical-data/`.
- A **card/button on `rp01.html`** linking to the page — rendered **only when
  the session user is an admin** (`session.get('is_admin')`).

The page shows:
- Current row count + last upload info (`uploaded_by`, `uploaded_at`).
- **Download Template** button.
- **Upload** file picker (`.csv`, `.xlsx`, `.xls`).
- A **reconciliation preview** panel (results of Phase 1, see §5).
- An **Apply (replace all)** button, enabled after a clean preview.

## 4. Template Export

`GET /api/module/RP01/historical/template` → an `.xlsx` workbook (admin-only):

- **Sheet 1 "Data"** — the fill-in sheet:
  - Header row = the base columns (§2, excluding `id`/`uploaded_by`/`uploaded_at`).
  - A frozen instructions banner documenting formats:
    - `entry_date` → `YYYY-MM-DD` (**required**)
    - `from_time` / `to_time` → `HH:MM` (24-hr)
    - `quantity` → number
    - `equipment_name` → **required**
    - `source_display` = vessel name for vessel ops; `barge_name` = barge name
      (vessel ops) or MBC name (MBC ops); everything else free text.
- **Sheet 2 "Masters"** — one column per master, populated **live at export
  time** from the DB (see master map in §5): Equipment, Cargo, Delay, Route,
  System, Berth, Operator, Shift Incharge, Barge, MBC, Vessel.
- **Dropdowns**: each master-backed column on the Data sheet gets an Excel
  **list data-validation** referencing the matching Masters column range
  (e.g. `Masters!$A$2:$A$1000`). The validation is created with
  `showErrorMessage=False` (openpyxl) so it shows the dropdown but does **not**
  reject typed-in values — free text still allowed, consistent with "unknown
  values are warnings, not blockers".

## 5. Upload Flow — Two-Phase, Full Replace

### Phase 1 — Preview & Reconcile
`POST /api/module/RP01/historical/preview` (multipart, admin-only):
1. Parse CSV/Excel from the "Data" sheet (openpyxl for xlsx, csv for csv).
2. **Format validation** per row: `entry_date` parses as a date; `from_time`/
   `to_time` parse as `HH:MM`; `quantity` parses as a number when present;
   `equipment_name` non-empty. Collect row-level errors (row number + message).
3. **Master reconciliation**: for each master-backed column, take the file's
   distinct non-empty values and split into *recognized* vs *unknown* against
   the master. For each **unknown** value, attach the closest master
   suggestions ("did you mean") via fuzzy matching (`difflib.get_close_matches`,
   case-insensitive, up to 3 suggestions).

   Master map:

   | Column | Master table / column |
   |---|---|
   | equipment_name | `equipment.name` |
   | cargo_name | `vessel_cargo.cargo_name` |
   | delay_name | `port_delay_types.name` |
   | route_name | `conveyor_routes.route_name` (active) |
   | system_name | `port_systems.name` |
   | berth_name | `port_berth_master.berth_name` |
   | operator_name | `port_shift_operators.name` |
   | shift_incharge | `port_shift_incharge.name` (PSMM01) |
   | barge_name | `barges.barge_name` **or** `mbc_master.mbc_name` |
   | source_display | `vessels.vessel_name` |

   `barge_name` counts as recognized if it matches **either** the barge master
   **or** the MBC master (since the column carries both).

4. Response: `{ total_rows, format_errors: [...],
   reconciliation: { <column>: { recognized: [...],
   unknown: [{ value, count, suggestions: [...] }] } } }`.

Nothing is written in Phase 1. **Unknown master values are warnings, not
blockers** — only hard format errors (bad date/time/number, missing required)
block the apply. The user fixes spellings (helped by the suggestions) and
re-uploads, or proceeds.

### Phase 2 — Apply
`POST /api/module/RP01/historical/apply` (multipart, admin-only):
1. Re-parse and re-run **format validation** server-side (never trust a stale
   preview); if hard errors exist, return them and write nothing.
2. In **one transaction**: `TRUNCATE rp01_historical_lueu` → bulk `INSERT` all
   parsed rows (`uploaded_by`, `uploaded_at` set) → commit.
3. Return `{ inserted: <count> }`.

## 6. New Pivot Data Source: `lueu-historical`

Label **"LUEU (incl. historical)"**. Added to:
- `VALID_SOURCES`, `DATE_COL_FILTERS` (`entry_date`), `DATE_COL_DEFAULTS`
  in `custom_report/views.py`.
- The source `<select>` dropdowns in `custom_report.html` **and**
  `dashboard.html` (both consume `/api/module/RP01/pivot/data/<source>`).

Query = `UNION ALL` of:
- the **existing** `lueu-equipment` projection over `lueu_lines` (live), and
- the same projection over `rp01_historical_lueu`,

both wrapped by the identical `vessel_cargo` and `port_delay_types` lateral
joins and the same `Diff Hrs` post-processing in `pivot_data`, so the output
columns are byte-for-byte identical to `lueu-equipment`. Implemented by
factoring the inner per-table SELECT so live and historical share it, differing
only in the base table/columns. The historical leg has no `source_type`/
`source_id`; `source_display` and `barge_name` come straight from the table.

The original `lueu-equipment` source remains **live-only and unchanged**;
existing saved reports are unaffected. Reports/widgets opt in by choosing
"LUEU (incl. historical)".

## 7. Permissions

- Template download, preview, apply endpoints and the rp01.html button are
  **admin-only** (`session.get('is_admin')`); non-admins get 403 / no button.
- Selecting and viewing the `lueu-historical` source in reports/widgets is
  available to any RP01 user (it's just another read source).

## 8. Components Summary

| Unit | Responsibility | Depends on |
|---|---|---|
| Alembic migration | create/drop `rp01_historical_lueu` | — |
| `historical_data/views.py` | page route + template/preview/apply endpoints | `rp01_historical_lueu`, masters |
| `historical_data/model.py` | parse, validate, reconcile, full-replace insert, template build | DB, openpyxl, difflib |
| `historical_data.html` | upload UI + reconciliation preview | preview/apply endpoints |
| `rp01.html` | admin-only card | — |
| `custom_report/views.py` | `lueu-historical` source (UNION query) | `lueu_lines`, `rp01_historical_lueu` |
| `custom_report.html`, `dashboard.html` | new source in dropdowns | — |

## 9. Testing

- **Unit (pure, no DB):** date/time/number format validators; fuzzy-suggestion
  helper (returns closest master values for a misspelling); CSV/Excel row parser
  (maps headers → fields, blank-row skipping).
- **DB-backed smoke:** template export opens and contains Masters sheet with
  live values + dropdowns; preview classifies recognized vs unknown correctly;
  apply does full replace (count before/after) within a transaction; the
  `lueu-historical` pivot query returns the same column set as `lueu-equipment`
  and includes historical rows for an out-of-range historical date.
- **Manual:** admin sees the button, non-admin does not; build a report/widget
  on "LUEU (incl. historical)" and confirm historical dates appear.

## 10. Open Items / To Confirm During Implementation
- Exact header labels in the template (use base column names vs. friendly
  labels) — default to friendly labels matching the LUEU export where they exist.
- Masters dropdown range size (default 1000 rows) — widen if any master exceeds it.
