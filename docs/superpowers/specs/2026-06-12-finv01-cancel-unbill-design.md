# FINV01 Cancel Button — Relaxed Gate + Full Unbill to Cargo Level

**Date:** 2026-06-12
**Status:** Approved

## Problem

The SAP push is a staging push: `sap_document_number` and `sap_posting_date` are only
populated later by `/api/sap/callback`. The FINV01 invoice list gates the Cancel button
on `sap_document_number`, so until SAP responds there is no Cancel (or Create CN) button
at all. Additionally, cancelling an invoice today leaves its bills stuck in `'Invoiced'`
and the cargo declarations marked billed, so the vessel's cargo can never be re-invoiced.

## Decisions (user-confirmed)

1. **Unbill depth:** Full unbill to cargo level. On cancellation, linked bills are set to
   `'Cancelled'` (kept for audit, not deleted), cargo declarations get `billed_quantity`
   decremented / `is_billed` reverted via the existing `_unmark_cargo_source_billed`,
   and `service_records` are unmarked (`is_billed=0, bill_id=NULL`).
2. **FB08 window:** Keep the 24h split. Within window → Cancel (FB08 reversal payload);
   past window → Create CN. The window anchor is relaxed: `sap_posting_date` when SAP
   has confirmed, otherwise fall back to `posted_date` (our push time, set by
   `_auto_post_to_sap`).
3. **Scope:** Only SAP-pushed invoices (`invoice_status` in `('Posted to SAP',
   'Posted to GST')`). Invoices in `Generated` / `SAP Failed` keep current behavior.
4. **Invoice numbers never repeat:** already guaranteed — numbering takes
   `MAX(invoice_number)+1` over all rows including Cancelled ones. Re-invoicing after a
   cancellation produces a fresh number.

## Changes

### `modules/FIN01/model.py`
New helper `unbill_invoice_sources(cur, invoice_id)`:
- Looks up bills via `invoice_bill_mapping`.
- Per bill: reverses cargo declaration tracking (`_unmark_cargo_source_billed` per
  `bill_lines` row), unmarks `service_records`, sets `bill_status='Cancelled'`.
- Runs inside the caller's transaction (takes `cur`, does not commit).
- Returns the affected bill numbers for the remarks note.

### `modules/FINV01/views.py`
- `invoices()`: `within_cancel_window` anchor = `sap_posting_date` → fallback `posted_date`.
- `cancel_invoice_sap()`:
  - Guard on `invoice_status` in `('Posted to SAP', 'Posted to GST')` instead of
    requiring `sap_document_number`.
  - Window anchor with the same fallback; missing both → error.
  - Reversal payload unchanged (`build_invoice_reversal_payload`; Reference = invoice
    number, which SAP staging matches on even without an SAP doc number).
  - On SAP success: mark invoice `Cancelled` **and** call `unbill_invoice_sources` in
    the same transaction.
- `create_cancellation_cn()`: same status-based guard relaxation and the same
  `unbill_invoice_sources` call when marking the invoice Cancelled, so cancellation by
  CN also frees the cargo.

### `modules/FINV01/finv01_invoices.html`
- Show Cancel / Create CN when `invoice_status` in `('Posted to SAP', 'Posted to GST')`
  (drop the `sap_document_number` requirement).
- Update confirm dialogs to state that linked bills will be cancelled and cargo
  declarations returned to unbilled.

## Out of Scope
- Local cancel for never-pushed invoices.
- Changes to CN payload, FDCN records, or SAP callback handling.
