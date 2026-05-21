# Go-Live Cutover / Migration Tab — Design

**Date:** 2026-05-21
**Status:** Approved (design); pending implementation plan
**Module:** ADMIN (new tab) + small touches to FIN01 / FINV01

## Problem

At go-live, PORTMAN replaces a site billing system that is already in use. Two
cutover needs:

1. **Continue the serial numbers.** Invoicing (and bills) cannot start at 1 —
   they must continue from where the legacy system left off. Because we cannot
   trust that all legacy data was entered into PORTMAN correctly, the admin must
   set the **exact** next number to issue, not "legacy last + 1".
2. **Suppress already-billed work.** Some vessels were already billed in the
   legacy system. Their cargo/services must not appear in PORTMAN's billing
   worklist. This is a pure status flag — **no invoice and no SAP posting.**

Both are handled from a new **admin-only "Cutover / Migration" tab**, which can
be **locked after go-live** so nobody disturbs the sequences afterwards.

## Current behaviour (as-is)

- **Invoice number (FINV01):** `create_invoice` computes
  `next_seq = MAX(doc_series_seq) + 1` per `(doc_series, financial_year)` from
  `invoice_header`, then formats `{prefix}/{fy}/{seq}`. No "start number" exists.
  (`modules/FINV01/views.py`, ~line 288.)
- **Bill number (FIN01):** `get_next_bill_number()` =
  `MAX(CAST(SUBSTR(bill_number,5) AS INT)) + 1`, format `BILL%04d`, global
  (not per-FY). (`modules/FIN01/model.py`, ~line 79.)
- **Billing worklist** is driven by two "already-billed" markers:
  - Cargo declarations (`vcn_cargo_declaration`, `vcn_export_cargo_declaration`,
    `mbc_customer_details`) carry `is_billed` and `billed_quantity`; an item is
    billable while `is_billed = 0 OR billed_quantity < total`.
    (`modules/FIN01/views.py`, ~lines 670–743.)
  - Service records (`service_records`) carry `is_billed` (0/1); billing pulls
    only `is_billed = 0`. (`modules/SRV01/model.py`, ~line 211.)
- **ADMIN module** is a blueprint at `/admin`, gated by `admin_required`
  (`session['is_admin']`), rendering `templates/admin.html`. Config helpers
  `get_module_config` / `save_module_config` exist.
- **FDCN doc numbers** are now internal-only (a CN carries its parent invoice's
  reference to SAP). They are **out of scope** for cutover seeding and start
  fresh at `…0001`.

## Decisions (from brainstorming)

| Topic | Decision |
|---|---|
| Serials to seed | **Invoice (FINV01)** and **Bill (FIN01)** only. FDCN starts fresh. |
| Number model | Admin types the **exact next number**; FY-scoped one-time seed for the go-live FY. No "+1", no continuous-counter rework. |
| Mark billed granularity | **Per item** on a vessel (tick specific cargo lines / service records). |
| Cargo precision | **Whole line** (`billed_quantity = total`). No partial quantities. |
| Mark billed semantics | **Pure status flag** — no bill, no invoice, never posts to SAP. |
| Access | **Admin-only** + guardrails + audit log + **lock after go-live**. |
| Storage approach | **Approach A** — dedicated `cutover_seed` + `cutover_audit` tables; generators read the seed as a floor (no fabricated documents). |

## Architecture

A new **"Cutover / Migration"** section in `templates/admin.html`, with new
admin-only routes in `modules/ADMIN/views.py` (`/admin/...`, behind
`admin_required`). No new module.

### Data model

```sql
CREATE TABLE cutover_seed (
    id              SERIAL PRIMARY KEY,
    seed_type       TEXT NOT NULL CHECK (seed_type IN ('invoice','bill')),
    doc_series      TEXT NOT NULL DEFAULT '', -- invoice: e.g. 'DPPL'; bill: ''
    financial_year  TEXT NOT NULL DEFAULT '', -- invoice: e.g. '26-27'; bill: ''
    start_seq       INTEGER NOT NULL,         -- EXACT next number to issue
    created_by      TEXT,
    created_at      TIMESTAMP DEFAULT now(),
    updated_by      TEXT,
    updated_at      TIMESTAMP,
    UNIQUE (seed_type, doc_series, financial_year)
);
-- NOTE: doc_series/financial_year are NOT NULL DEFAULT '' (empty-string
-- sentinel for the single global bill row) on purpose. Postgres treats NULLs
-- as distinct in a UNIQUE constraint, so NULLable key columns would NOT prevent
-- duplicate bill seed rows. Empty strings make the UNIQUE constraint enforce a
-- single bill row and one row per (series, fy) for invoices.

CREATE TABLE cutover_audit (
    id            SERIAL PRIMARY KEY,
    action        TEXT NOT NULL,     -- 'set_invoice_seed' | 'set_bill_seed'
                                     -- | 'mark_billed' | 'unmark_billed'
                                     -- | 'lock' | 'unlock'
    details       JSONB,             -- e.g. {series, fy, old, new}
                                     -- or {vcn, cargo:[ids], services:[ids]}
    performed_by  TEXT,
    performed_at  TIMESTAMP DEFAULT now()
);
```

- **Lock flag** lives in `module_config` (`ADMIN` → `cutover_locked` = `'0'/'1'`);
  who/when of locking is recorded in `cutover_audit`.
- `start_seq` is the **exact** next sequence number. Per-`(series, fy)` rows for
  invoices; a single global row (`seed_type='bill'`, series/fy `''`) for bills.
- Created via an Alembic migration (single head; non-destructive `CREATE TABLE`).

### Number generation (the seed as a floor)

Pure, unit-testable core:

```
next_from_seed(existing_max, start_seq) = max(existing_max + 1, start_seq or 0)
```

- New `model.next_invoice_seq(cur, doc_series, fy)`: runs the existing
  `MAX(doc_series_seq)` query, looks up the matching `cutover_seed`, returns
  `next_from_seed(...)`. `FINV01.create_invoice` calls this instead of its inline
  `+ 1`.
- `FIN01.get_next_bill_number()` gains the same lookup for the `BILL` counter.

Behaviour: with `start_seq = 4568` and no PORTMAN invoices yet, the **first**
invoice is exactly `4568`, then 4569, 4570… `GREATEST` guarantees that once real
documents exist a stale seed can never cause a duplicate.

## Feature 1 — Document-number seeding

**UI:** "Document numbers" panel.
- Invoice: choose doc series (from `invoice_doc_series`), FY (default current),
  type the starting number. Show current max issued for that series+FY and a live
  preview of the first formatted number (e.g. `DPPL/26-27/4568`).
- Bill: type the starting `BILL` number; show current max + preview (`BILL4568`).
- Read-only list of seeds already configured.

**Save:** validate → upsert `cutover_seed` → write `cutover_audit`.

**Guardrails:**
- Reject `start_seq` not strictly greater than the current max for that
  series+FY (response names the current max), so a typo cannot silently no-op.
- All writes refused when `cutover_locked = 1`.
- Idempotent / re-runnable until locked.

## Feature 2 — Mark items billed

**UI:** "Mark items billed" panel.
- Pick customer type + customer; load billable items by **reusing**
  `get_customer_billables` so the tab mirrors the billing screen exactly:
  vessels (VCN) with cargo lines (import/export/MBC) and service records.
- Tick specific items (cargo whole-line). Confirm → submit.

**Server (pure flag — no bill, no invoice, no SAP):**
- Cargo: `UPDATE <decl table> SET is_billed=1, billed_quantity=<declared qty>
  WHERE id=<cargo_source_id>`, table chosen by `cargo_source_type`:
  `VCN_IMPORT → vcn_cargo_declaration`,
  `VCN_EXPORT → vcn_export_cargo_declaration`,
  `MBC → mbc_customer_details`.
- Service: `UPDATE service_records SET is_billed=1 WHERE id=<id>` (`bill_id`
  stays NULL — no bill exists).
- One transaction per submit; one `cutover_audit` row per submit.
- **Unmark** is a symmetric, audited, lock-guarded action
  (`is_billed=0, billed_quantity=0`) for correcting cutover mistakes. Only these
  two columns are ever touched.

**Guardrails:** refused when locked; only acts on currently-unbilled items
(re-marking is a no-op); transactional with rollback on error.

## Lock ("Cutover complete")

- Toggle sets `module_config` `cutover_locked=1` (audited).
- While locked: every write endpoint (seed / mark / unmark / re-seed) returns
  `403`; the tab renders read-only with a locked banner.
- Unlock requires an explicit admin confirmation (audited) — the override path.

## Error handling

- Non-admin → `admin_required` (redirect / 401).
- Locked → `403` with a clear message.
- `start_seq ≤ current max` → `400` naming the current max.
- Bad customer/item ids → `400` / `404`.
- Mark/unmark and seed upsert wrapped in a transaction; rollback + message on
  error.

## Testing

Same pattern as the recent SAP work — extract pure cores and TDD them:

- `next_from_seed(existing_max, start_seq)`:
  `(0,4568)=4568`, `(4568,4568)=4569`, `(5000,4568)=5001`, `(10,None)=11`.
- Seed-guardrail validator: rejects `start_seq ≤ max`.
- `cargo_source_type → (table, qty_column)` mapping helper.
- Lock gate: write endpoints return `403` when `cutover_locked=1`.
- Light integration checks for the `UPDATE` / upsert DB paths.

## Out of scope

- FDCN/CN/DN number seeding (internal-only; starts fresh).
- Partial cargo quantities when marking billed (whole-line only).
- Continuous (non-FY-resetting) invoice numbering.
- Importing legacy invoices/bills as real documents.

## Affected files (anticipated)

- `alembic/versions/<new>_cutover_tables.py` — create `cutover_seed`,
  `cutover_audit` (single head, non-destructive).
- `modules/ADMIN/views.py` — cutover routes + APIs (admin-only, lock-aware).
- `templates/admin.html` — Cutover / Migration tab UI.
- `modules/FIN01/model.py` — `next_from_seed`, `next_invoice_seq`, seed-aware
  `get_next_bill_number`.
- `modules/FINV01/views.py` — `create_invoice` uses `next_invoice_seq`.
- `test_cutover.py` — pure-core + guardrail + lock-gate tests.
```
