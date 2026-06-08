# Cutover tab UI refresh + partial cutover billing — Design

**Date:** 2026-06-08
**Author:** Shubham Shinde (with Claude)
**Builds on:** [2026-05-21-go-live-cutover-migration-tab-design.md](2026-05-21-go-live-cutover-migration-tab-design.md)

## Problem

The Admin → Cutover tab lets an admin mark legacy (pre-go-live) cargo and service
items as already billed, so the new system will not re-bill them. Today this is
**all-or-nothing**: marking a cargo line sets `billed_quantity = full BL quantity`
([`modules/ADMIN/cutover.py:142`](../../../modules/ADMIN/cutover.py)).

In reality a customer was often **partially billed** in the legacy system before
cutover (e.g. 50 MT of a 100 MT BL). We need to record only the already-billed
portion and leave the **balance** open so it can be billed normally in FIN01 —
exactly like FIN01's existing quantity partial billing
([`modules/FIN01/model.py:46` `_mark_cargo_source_billed`](../../../modules/FIN01/model.py)).

The tab UI is also a cramped list of inline-styled checkboxes and needs a proper,
readable table layout.

## Goals

1. Allow **partial** cutover marking: enter a Bill Qty per cargo line; the balance
   stays billable in FIN01.
2. Refresh the Cutover tab UI into a clean, readable, dark-theme-aware layout with
   a proper cargo line-items table.
3. Treat cutover items as **manual bills only — no SAP push, no GST computation,
   no bill document, no bill number.** The existing cutover storage mechanism does
   not change shape.

## Non-goals (out of scope)

- SAP integration, GST computation, creating `bill_header`/`bill_lines` records, or
  assigning bill numbers to cutover marks.
- Persisting Rate / Amount (grid-only helpers — see below).
- Partial **unmark** (unmark remains a full reset, unchanged).
- Changes to the document-number seed logic or the lock mechanism.

## Key decisions (from brainstorming)

| Decision | Choice |
|----------|--------|
| What Bill Qty / Rate / Amount do | Manual bill capture, **no SAP, no GST** |
| Where partial data is stored | **Existing storage unchanged** — `is_billed` + `billed_quantity` on the declaration rows |
| Remaining balance after partial mark | **Stays billable in FIN01** |
| Rate & Amount columns | **Display/helper only, not saved** |

## Storage (unchanged)

Cutover state continues to live on the three cargo declaration tables — no schema
change:

- `vcn_cargo_declaration` (qty column `bl_quantity`)
- `vcn_export_cargo_declaration` (qty column `bl_quantity`)
- `mbc_customer_details` (qty column `quantity`)

Each has `is_billed` and `billed_quantity`. Services use `service_records.is_billed`
(pure flag, no quantity concept).

## Backend design — `modules/ADMIN/cutover.py`

Extend `_apply_billed` / `mark_items_billed` so each cargo item may carry an optional
`bill_quantity`. The payload becomes `{source_type, id, bill_quantity}` (the
`bill_quantity` is the amount to mark billed *now*).

**Mark (`billed=True`)** for each cargo item:

1. Look up the row's total qty (`qty_col`) and current `billed_quantity`.
   `qty_col`/`table` come only from the trusted `CARGO_SOURCES` constant (never user
   input), preserving the existing safe-interpolation contract.
2. `balance = total − already_billed`.
3. `bill_qty = item.bill_quantity`; if missing or `<= 0`, default to `balance`
   (preserves the existing "mark whole line" behavior — full back-compat for any
   caller that does not send a quantity).
4. Cap: `bill_qty = min(bill_qty, balance)` (never over-bill).
5. `new_billed = already_billed + bill_qty`.
6. `is_billed = 1 if new_billed >= total else 0`.
7. `UPDATE {table} SET is_billed=<flag>, billed_quantity=<new_billed> WHERE id=%s`.
   (`bill_id` stays NULL — cutover has no bill record.)

This mirrors FIN01's `_mark_cargo_source_billed` exactly, minus the `bill_id`.
Because `billed_quantity < bl_quantity` keeps a row in FIN01's billables query
([`modules/FIN01/views.py:677`](../../../modules/FIN01/views.py)) with
`billable_quantity = total − billed`, the balance automatically remains billable.

**Unmark (`billed=False`):** unchanged — full reset to `is_billed=0, billed_quantity=0`.

**Validation / lock:** unchanged. The lock check and audit write
(`write_audit('mark_billed', …)`) stay; the audit `details` now include the per-item
`bill_quantity` for traceability.

Pure-helper extraction: the partial-quantity math (steps 2–6) is factored into a
small DB-free function (e.g. `compute_partial_billed(total, already, bill_qty)`
returning `(new_billed, is_billed_flag)`) so it is unit-testable without a DB, matching
the module's existing "pure helpers have no DB dependency" docstring.

## Frontend design — Cutover tab in `templates/admin.html`

### UI polish
Restructure the tab (currently [`templates/admin.html:597`](../../../templates/admin.html))
into three clearly separated cards, each using the existing `.admin-section` style:

1. **Document Numbers** — invoice seed + bill seed (existing controls, tidied with
   labels/help text and aligned inputs).
2. **Mark Items Billed (partial)** — customer/agent picker + the new cargo table +
   services list + action buttons.
3. **Lock** — existing lock toggle.

All new styles reuse existing classes (`.admin-section`, `.admin-table`) and add
dark-theme rules consistent with the surrounding `body.dark-theme` block.

### Cargo line-items table
`cutLoadBillables` renders cargo as a table (replacing the checkbox list) with these
columns, populated from `/api/module/FIN01/customer-billables/<type>/<id>`:

| Column | Source field | Notes |
|--------|--------------|-------|
| ☑ select | — | row checkbox + "select all" |
| Source | `doc_label` | with an Import / Export / MBC tag derived from `cargo_source_type` |
| Status | `doc_status` | colored chip; billable vs blocked from `is_billable` |
| Service | `service_name` | e.g. CHGU01 / CHGL01 |
| Cargo | `cargo_name` | |
| Proof Docs | lazy fetch | "📎 View" link → existing [`/api/module/FIN01/proof_docs/by_source/<module>/<id>`](../../../modules/FIN01/views.py); VCN → LDUD via `ldud_id`, MBC → `source_id`. Shows count, opens file links. Renders "—" when no source id. |
| BL Date | `bl_date` | read-only |
| BL Qty | `total_quantity` | read-only |
| Billed | `billed_quantity` | read-only (already billed) |
| Balance | `billable_quantity` | read-only (= total − billed) |
| Bill Qty | input | number, defaults to Balance, `min=0`, `max=Balance` |
| Rate | input | number, helper only |
| Amount | computed | `Bill Qty × Rate`, read-only, helper only |

`Amount` recomputes on Bill Qty / Rate change. Bill Qty is clamped to `[0, Balance]`
in the UI as well as the backend.

### Services
Services remain a simple flag list/table below the cargo table (Service · Source ·
select) — they have no quantity concept, so no Bill Qty/Rate/Amount.

### Submit
`cutMarkBilled(true)` collects checked cargo rows as
`{source_type, id, bill_quantity}` (reading each row's Bill Qty input) plus checked
`service_ids`, and POSTs to the unchanged
[`/admin/api/cutover/mark-billed`](../../../modules/ADMIN/views.py) route.
`cutMarkBilled(false)` (unmark) sends rows without `bill_quantity` (full reset).

## Testing

**Unit (pure helper `compute_partial_billed`):**
- partial `<` balance → `is_billed=0`, `new_billed = already + bill_qty`
- partial `==` balance (or full line) → `is_billed=1`
- `bill_qty` over balance → capped to balance, `is_billed=1`
- `bill_qty` missing / ≤ 0 → defaults to full balance (back-compat)

**Manual:**
- Load a customer with cargo, mark a partial Bill Qty, save, then open FIN01
  generate-bill for the same customer → the remaining balance still appears as
  billable with the reduced `billable_quantity`.
- Confirm lock blocks the action; confirm unmark fully resets.

## Files touched

- `modules/ADMIN/cutover.py` — partial mark logic + pure helper.
- `modules/ADMIN/views.py` — **no change needed**: the
  [`/api/cutover/mark-billed`](../../../modules/ADMIN/views.py) route already forwards
  `cargo_items` verbatim to `mark_items_billed`, so the extra `bill_quantity` key
  passes straight through.
- `templates/admin.html` — Cutover tab markup, styles, and the
  `cutLoadBillables` / `cutMarkBilled` JS.
- `test_cutover.py` (project root) — add `compute_partial_billed` cases alongside the
  existing pure-helper tests (DB-free, matching the `test_sap_builder.py` pattern).
