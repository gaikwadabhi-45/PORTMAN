# Daily-Ops Cutoff Remodel: FY × Cargo-Type Snapshot

**Date:** 2026-06-08
**Module:** RP01 / daily-ops
**Status:** Design — pending user review

## Problem

The RP01 daily-ops report has a configurable "cutoff" that freezes month-to-date
(MTD) values before the system go-live, so historical numbers don't shift as live
data is edited. Today the cutoff stores two grids in
`daily_ops_cutoff.cutoff_values` (JSON):

- `mbc_cargo` — MBC cargo handling MTD
- `cargo_handled` — cargo handled by route MTD

We are replacing those grids with a **Financial-Year × cargo-type throughput
matrix** (the multi-year tonnage view). The cutoff date stays; what it stores
changes.

## Goals

- Store, per cutoff, a snapshot of total quantity per **(financial year, cargo
  type)** from FY 2012-2013 through the FY that contains the cutoff date.
- The latest (cutoff) FY is partial: it spans that FY's April 1 up to the cutoff
  date only.
- Cargo types are the distinct `cargo_type` values from the VCG01
  `vessel_cargo` master (IBRM, Fluxes, CBRM, Clinker, Slag/GGBS, HRC/TMT,
  Other…). They are dynamic — adding a cargo type must not require a schema
  change.
- One cutoff setting, set by an admin. Cutoff settings are restricted to the
  admin role on the frontend.
- Values are **auto-computed** from existing data when the admin saves the
  cutoff — not hand-typed.

## Non-Goals

- Building the multi-FY display report itself. The reference image
  (FY rows × cargo-type columns) shows the target shape, but this work only
  remodels what the cutoff **stores** and computes. Rendering the matrix report
  is separate/future.
- Preserving the retired `mbc_cargo` / `cargo_handled` cutoff behavior.

## Decisions (from brainstorming)

| Decision | Choice |
|---|---|
| Storage shape | JSON in the existing `daily_ops_cutoff` table (no new table) |
| Old grids | Replaced entirely by the FY matrix |
| Value source | Auto-computed snapshot; admin sets only the cutoff date |
| Access | Cutoff settings restricted to admin (`session['is_admin']`) |
| FY convention | April-start: month ≥ 4 → FY `YYYY-(YYYY+1)`, else `(YYYY-1)-YYYY` |
| FY range | 2012-2013 → cutoff FY (last FY truncated at the cutoff date) |

## Data Model

`daily_ops_cutoff` stays a single-row table; `cutoff_values` (TEXT) holds:

```json
{
  "fy_throughput": {
    "2012-2013": { "IBRM": 2255677, "Fluxes": 360690, "CBRM": 604953, "Clinker": 0, ... },
    "2024-2025": { "IBRM": 1481822, "Fluxes": 330242, "CBRM": 6354617, ... },
    "2026-2027": { "IBRM": 442916,  "Fluxes": 95803,  "CBRM": 239855, ... }
  }
}
```

- Keys = FY label strings; values = `{cargo_type: quantity}`.
- Cargo types present per FY are only those with data; consumers treat missing as 0.
- Column totals and grand totals are derived by the consumer, not stored.

## Components

### 1. Alembic migration

New revision chaining from the current migration head (verify with
`alembic heads` at implementation; latest seen is
`f1a2b3c4d5e6_rp01_historical_lueu`).

- **No DDL** — `cutoff_values` remains `TEXT`.
- **upgrade():** data migration. For any existing `daily_ops_cutoff` row, reset
  `cutoff_values` to `{"fy_throughput": {}}` while preserving `cutoff_date`.
  This drops the now-incompatible `mbc_cargo` / `cargo_handled` payload so the
  new code reads cleanly. The admin re-saves the cutoff after deploy to populate
  the snapshot.
- **downgrade():** reset `cutoff_values` to `{"mbc_cargo": {}, "cargo_handled": {}}`.
  Comment that the pre-migration payload is not recoverable.

### 2. Backend — `modules/RP01/RP01/daily_ops/views.py`

**Snapshot computation** (new helper, e.g. `_compute_fy_throughput(cutoff_date)`):

- Union `rp01_historical_lueu` and `lueu_lines` (live rows: `is_deleted = false`).
- Left-join `vessel_cargo` on `UPPER(TRIM(cargo_name))` to get
  `COALESCE(vc.cargo_type,'OTHERS')` — same join already used by
  `_fetch_cargo_type_throughput`.
- Filter `entry_date <= cutoff_date`.
- Derive FY label in SQL (April-start) and `GROUP BY fy, cargo_type`,
  `SUM(quantity)`.
- Return a nested dict `{fy: {cargo_type: qty}}`.

**`POST /api/module/RP01/daily-ops/cutoff`** (`daily_ops_cutoff_save`):

- Guard: `if not session.get('is_admin'): return 403`.
- Require `cutoff_date`.
- Compute snapshot via the helper, store
  `cutoff_values = {"fy_throughput": <snapshot>}` (single-row upsert as today:
  DELETE then INSERT, recording `created_by`).

**`GET /api/module/RP01/daily-ops/cutoff`** (`daily_ops_cutoff_get`):

- Return `{id, cutoff_date, cutoff_values: {"fy_throughput": {...}}}`.
- Empty default: `{"fy_throughput": {}}`.

**`_fetch_cargo_handled()`** (`views.py:1091`):

- Remove the cutoff-merge branch that reads `cutoff_vals.get('cargo_handled')`
  and `_load_cutoff()`. The route-cutoff feature is retired with the old grid;
  the function returns live day/month route data only.

### 3. Frontend — `modules/RP01/RP01/daily_ops/daily_ops.html`

- Replace the two cutoff grids (MBC Cargo Handling, Cargo Handled by Routes) with
  a read-only FY × cargo-type preview of the computed snapshot. Flow: admin picks
  a cutoff date → Save → backend computes → modal shows the resulting matrix.
- Hide the "Cutoff Settings" button when the user is not admin. The daily-ops
  index route passes `is_admin = session.get('is_admin')` into the template.

## Data Flow

```
Admin opens Cutoff Settings (admin-only button)
  -> picks cutoff_date
  -> POST /cutoff
       -> _compute_fy_throughput(cutoff_date)
            union(rp01_historical_lueu, lueu_lines)
            join vessel_cargo -> cargo_type
            where entry_date <= cutoff_date
            group by fy, cargo_type
       -> store {"fy_throughput": {...}} (single row, created_by)
  -> GET /cutoff returns snapshot -> modal renders read-only matrix
```

## Error Handling

- Non-admin POST → 403.
- Missing `cutoff_date` → 400 (unchanged).
- `cargo_name` with no `vessel_cargo` match → bucketed as `OTHERS`.
- Unparseable/empty `cutoff_values` on GET → empty default.

## Testing

- **Migration:** seed a row with old `{mbc_cargo, cargo_handled}` payload; run
  upgrade; assert `cutoff_values == {"fy_throughput": {}}` and `cutoff_date`
  preserved. Run downgrade; assert old empty shape.
- **Snapshot helper:** seed `rp01_historical_lueu` + `lueu_lines` across FY
  boundaries and the cutoff date; assert FY bucketing (April-start), cargo-type
  mapping via `vessel_cargo`, `OTHERS` fallback, and that the cutoff FY is
  truncated at the cutoff date.
- **Admin guard:** POST without `is_admin` → 403; with `is_admin` → snapshot
  stored.
- **`_fetch_cargo_handled`:** returns live values with no cutoff dependency.

## Open / Confirmed

- FY label uses standard April-start convention; the reference image is
  illustrative only (confirmed by user).
