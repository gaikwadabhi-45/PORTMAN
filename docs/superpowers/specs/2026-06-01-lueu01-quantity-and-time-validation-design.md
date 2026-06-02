# LUEU01 — Quantity & Time-Overlap Validation

**Date:** 2026-06-01
**Module:** LUEU01 (Load Unload Equipment Utilization)
**Status:** Approved design

## Problem

The LUEU01 data-entry grid lets users save invalid operational data:

1. **BL/trip quantity over-entry** — The grid *displays* exceed status (the `OVR`
   badge and the BL-progress popup via `get_bl_progress`), but nothing *blocks* it.
   `save_line` (`modules/LUEU01/model.py`) inserts whatever quantity is posted, and
   the client `saveAll` never compares against any remaining limit. A user can handle
   more than the barge trip's / MBC's quantity.

2. **Time overlap** — There is no overlap check anywhere. The same equipment can have
   two rows on the same date whose `from_time`–`to_time` ranges intersect, which is
   physically impossible (one machine cannot do two operations at once). The only
   client time logic is `calcDiffHrs`, which just computes duration.

## Decisions (from brainstorming)

- **Enforcement style:** Hard block, but *save the rest of the row* — reject only the
  offending field(s), leave them blank, and show a popup. The user re-enters a valid
  value.
- **Quantity limit basis:** **Per-barge / per-MBC trip quantity** (not the per-cargo
  declaration BL). The entered quantity must fit within the remaining trip quantity.
- **Overlap action:** Reject `from_time` + `to_time` (blank both), save the rest, popup
  naming the conflicting entry.
- **Overlap scope:** Same `equipment_name` + same `entry_date`. Overnight rows
  (`to_time <= from_time`) are treated as crossing into the next day.
- **Validation location:** Server-authoritative (Option A). All checks run inside the
  save endpoint; the client reacts to a structured response. Single source of truth,
  un-bypassable, reuses queries already present in the module.

## Architecture

### 1. Quantity block — per-barge / per-MBC trip qty

Computed server-side, before writing the row.

**VCN source:**
- `expected` = the barge trip's `discharge_quantity`, aggregated from
  `ldud_barge_lines` by `barge_name` / `trip_number` — the same aggregation
  `get_vcn_barges` already performs. Match the `lueu_lines.barge_name` value (stored as
  the `"barge / trip"` display string) against that aggregation.
- `handled_excluding_self` = `SUM(quantity)` of `lueu_lines` for the same `source_id`
  + `barge_name`, with `is_deleted IS NOT TRUE` and `id != current_id`.

**MBC source:**
- `expected` = MBC quantity (sum of `mbc_customer_details.quantity` if rows exist, else
  `mbc_header.bl_quantity`) — the same logic as `get_mbc_options`.
- `handled_excluding_self` = `SUM(quantity)` for `source_type='MBC'` + `source_id`,
  `is_deleted IS NOT TRUE`, `id != current_id`.

**Rule:** `remaining = expected - handled_excluding_self`. If
`incoming_qty > remaining` (and `expected > 0`), the row is saved with
`quantity = NULL` and the response records the rejection.

Edge handling:
- When editing an existing row, the row's own current quantity must be excluded from
  `handled` (via `id != current_id`) so re-saving an unchanged row does not falsely trip.
- If no expected/trip quantity can be resolved (`expected <= 0`), do not block (no basis
  to compare against).

### 2. Time-overlap block — same equipment + same date

Only checked when **both** `from_time` and `to_time` are present and parseable as `HH:MM`.

- Convert `HH:MM` to minutes. If `to <= from`, treat the interval as crossing midnight:
  `to += 1440`. Each row's interval lives in a `0..2880` minute space.
- Candidate set: existing `lueu_lines` with the same `equipment_name` and `entry_date`,
  `is_deleted IS NOT TRUE`, `id != current_id`, both times present.
- Two intervals A and B overlap when `startA < endB AND startB < endA`.
- On overlap, the row is saved with `from_time = NULL` and `to_time = NULL`. The client
  recomputes `diff_hrs` (becomes blank). The response records the conflicting entry's
  `from_time`, `to_time`, and `source_display` for the popup message.

### 3. Response contract + client reaction

- `model.save_line` returns `{'id': line_id, 'rejections': [...]}` instead of a bare id.
  Each rejection is a small dict, e.g.:
  - `{'field': 'quantity', 'reason': 'exceeds_trip_qty', 'limit': <remaining>, 'attempted': <qty>, 'label': <barge/mbc>}`
  - `{'field': 'time', 'reason': 'overlap', 'conflict': {'from_time': ..., 'to_time': ..., 'source_display': ...}}`
- `views.save_data` passes the dict straight through `jsonify`.
- In `saveAll` (`modules/LUEU01/lueu01.html`): after each row's response, read
  `result.id` as today, plus `result.rejections`. If present, `row.update()` the blanked
  field(s) (`quantity: null`, or `from_time: null, to_time: null` + recompute `diff_hrs`),
  and accumulate a human-readable message per affected row. After the save loop, show a
  **single summary popup** listing every rejected row and why (reusing existing
  modal/popup styling in the template).

## Historical over-quantity flag (added)

New validation only blocks *new* over-entries. Rows saved before it existed may already
exceed the barge/MBC trip quantity. To let staff find and correct them, `get_all_lines`
annotates each returned row with a group-level over flag:

- **Group key:** VCN → `(source_id, barge_name)`; MBC → `(source_id)`.
- **Over test:** the group's *total* non-deleted handled quantity exceeds its trip
  quantity (the same per-barge/MBC basis as `_resolve_trip_quantity`, `exclude_id=None`
  so nothing is excluded). Flag **every** row in an over group (not just the offending
  one — which row "tipped it over" is order-dependent and ambiguous).
- **Row fields added:** `_qty_over_group` (bool) and `_qty_over_by` (float, the overage).
- **UI:** the Quantity-cell formatter shows a distinct red `⚠ OVER` badge (separate from
  the existing per-cargo `OVR` badge) with a tooltip naming the overage. Read-time only;
  no schema change.

## Out of scope (deliberately)

- **Split** (`split_line`) is *not* re-validated — it only redistributes existing
  quantity between parent and child, so total handled never increases.
- Soft-deleted rows are excluded from both the quantity sums and the overlap candidate
  set, consistent with the rest of the module.
- **Known limitation of same-date scope:** an operation logged at e.g. `02:00` whose
  neighbor is an overnight `22:00–06:00` row from the *previous* calendar date will not
  be flagged, because overlap candidates are restricted to the same `entry_date`. This
  is an accepted trade-off of the chosen scope.

## Testing

- **Quantity, VCN:** handled at/over a barge trip's `discharge_quantity` → quantity
  rejected and blanked; under → saved normally; editing an existing row down/equal does
  not falsely trip.
- **Quantity, MBC:** same against MBC bl/customer-details quantity; MBC with no
  customer-detail rows falls back to header `bl_quantity`.
- **No basis:** source with no resolvable trip/BL quantity → not blocked.
- **Overlap:** two same-equipment, same-date rows with intersecting ranges → second's
  times rejected; non-overlapping ranges → both saved; overnight wrap (`23:00–02:00`)
  correctly overlaps `01:00–03:00`; a row missing one of from/to is skipped.
- **Response/UI:** rejected fields come back blanked in the grid; a single popup lists
  all rejections; un-rejected fields on the same row persist.
