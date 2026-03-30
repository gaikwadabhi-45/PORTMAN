# PORTMAN — Accounts / Finance Module Map

> **Generated:** 2026-02-28
> **Source:** Code audit of `d:\PORTMAN\modules\FIN01`, `FINV01`, `FCN01`, `FDCN01`, `FCAM01`, `FGRM01`, `FCRM01`, `FSTM01`, `FSAP01`, `FLOG01`, `SAPCFG`, `GSTCFG`, `SRV01`, and related master modules.

---

## 1. Section Overview

The Finance / Accounts section handles all revenue-side accounting for the port management system. Its responsibilities are:

- **Rate management** — maintaining the tariff structure (service types, GST rates, currencies, exchange rates, customer-specific agreed rates)
- **Service recording** — capturing billable service events against vessel calls (VCN) or MBC (berth calls)
- **Bill generation** — aggregating EU lines and service records into a bill per customer per source
- **Invoice generation** — consolidating approved bills into tax invoices with CGST/SGST/IGST
- **Credit note management** — issuing credit memos against specific invoices / invoice lines
- **SAP FI integration** — posting invoices and credit notes to SAP via the JSW DynaportInvoice REST API (OAuth2)
- **GST e-invoice / IRN** — submitting invoices to the IRP (Invoice Registration Portal) via a GSP to obtain IRN and QR code
- **Payment tracking** — recording advance receipts, incoming payments against invoices, and GL journal vouchers
- **Configuration** — admin-only setup for SAP API credentials (SAPCFG) and GST GSP credentials (GSTCFG)
- **Audit trail** — an integration log (FLOG01) capturing every SAP and GST API call and its result

The section is implemented as a set of Flask Blueprints registered in `d:\PORTMAN\app.py`, each in its own `modules/<CODE>/` directory.

---

## 2. Module Inventory

| Module | Code | Type | Purpose | DB Tables | Status |
|--------|------|------|---------|-----------|--------|
| Billing (Bills) | FIN01 | Transaction | Generate bills from EU lines or service records; approval workflow; roll up into invoices | `bill_header`, `bill_lines` | Active |
| Invoice List & Generation | FINV01 | Transaction | Consolidate approved bills into tax invoices; print; GSTR-1 B2B export; post to SAP; generate IRN | `invoice_header`, `invoice_lines`, `invoice_bill_mapping` | Active |
| Credit Note Management | FCN01 | Transaction | Issue credit memos against existing invoices; generate IRN for credit notes | `credit_note_header`, `credit_note_lines` | Active |
| Debit / Credit Note Workflow | FDCN01 | Transaction | Manage approved debit notes and SAP-postable credit notes linked to original invoices; post to SAP; fetch IRN from SAP | `fdcn_header`, `fdcn_lines`, `fdcn_doc_series` | Active |
| Customer Agreement Master | FCAM01 | Master | Rate cards per customer / agent with validity periods and per-service-type rates; approval workflow | `customer_agreements`, `customer_agreement_lines` | Active |
| Service Type Master | FSTM01 | Master | Define billable service types with SAC codes, GST rates, GL codes, SAP GL accounts; configure custom EAV fields | `finance_service_types`, `service_field_definitions` | Active |
| GST Rate Master | FGRM01 | Master | Maintain GST rate slabs (CGST/SGST/IGST); linked to service types | `gst_rates` | Active |
| Currency Master | FCRM01 | Master | Currency definitions and exchange rate history | `currency_master`, `currency_exchange_rates` | Active |
| Service Recording | SRV01 | Transaction | Record billable service events against a VCN or MBC source; EAV field values; approval status | `service_records`, `service_record_values` | Active |
| Payments & Advances | FSAP01 | Transaction | Record advance receipts from parties; incoming payments against invoices; manual GL journal vouchers | `advance_receipts`, `customer_incoming_payments`, `gl_journal_vouchers`, `gl_jv_lines` | Active (UI stub) |
| Integration Logs | FLOG01 | Reporting | Read-only view of all SAP and GST API call logs with filter by type, status, date range | `integration_logs` | Active |
| SAP Config | SAPCFG | Config | Admin-only: manage SAP API credentials per environment (Dev/QAS/Prod); test connection | `sap_api_config` | Active |
| GST Config | GSTCFG | Config | Admin-only: manage GST GSP API credentials per environment; IRP public key path | `gst_api_config` | Active |

### Modules that Finance depends on (master data — not Finance modules themselves)

| Module | Code | Table | Fields used by Finance |
|--------|------|-------|----------------------|
| Vessel Agent Master | VAM01 | `vessel_agents` | `name`, `sap_customer_code`, `gl_code`, `gstin`, `gst_state_code`, `pan`, `billing_address`, `city`, `pincode`, `contact_phone`, `contact_email`, `company_code`, `default_currency` |
| Vessel Customer Master | VCUM01 | `vessel_customers` | `name`, `sap_customer_code`, `gl_code`, `gstin`, `gst_state_code`, `pan`, `billing_address`, `city`, `pincode`, `contact_phone`, `contact_email`, `company_code`, `default_currency` |
| Importer / Exporter Master | VIEM01 | `vessel_importer_exporters` | `name`, `gstin`, `gst_state_code` (used by FCAM01 agreement entry) |
| Vessel Call Notification | VCN01 | `vcn_header`, `vcn_anchorage` | `vcn_doc_num`, `vessel_name`, `anchorage_arrival` (source reference for bills) |
| Marine Berth Call | MBC01 | `mbc_header` | `doc_num`, `mbc_name`, `doc_date` (source reference for bills) |
| EU Lines (Loading/Unloading) | LUEU01 | `lueu_lines` | `service_type_id`, `source_type`, `source_id`, `is_billed`, `bill_id` (FIN01 reads unbilled, marks billed) |

---

## 3. Master Data Dependencies

The table below shows which master data each finance module reads and what specific fields it consumes.

| Finance Module | Reads From | Fields Consumed | Purpose |
|----------------|-----------|-----------------|---------|
| FIN01 (Bill Gen) | `vessel_customers` / `vessel_agents` | `id`, `name`, `gstin`, `gst_state_code`, `gl_code`, `pan`, `billing_address`, `city`, `pincode`, `contact_phone`, `contact_email` | Populate bill / invoice customer details |
| FIN01 (Bill Gen) | `vcn_header`, `vcn_anchorage` | `id`, `vcn_doc_num`, `vessel_name`, `anchorage_arrival` | Source selector dropdown |
| FIN01 (Bill Gen) | `mbc_header` | `id`, `doc_num`, `mbc_name`, `doc_date` | Source selector dropdown |
| FIN01 (Bill Gen) | `lueu_lines` | `id`, `service_type_id`, `source_type`, `source_id`, `is_billed`, `bill_id` | Pull unbilled EU lines for billing |
| FIN01 (Bill Gen) | `finance_service_types` | `id`, `service_name`, `service_code`, `sac_code`, `uom`, `gl_code`, `gst_rate_id` | Service line details and SAC codes |
| FIN01 (Bill Gen) | `gst_rates` | `id`, `cgst_rate`, `sgst_rate`, `igst_rate` | GST calculation per service line |
| FIN01 (Bill Gen) | `customer_agreements` + `customer_agreement_lines` | `rate`, `uom`, `currency_code` | Auto-fill rate from agreed tariff |
| FIN01 (Bill Gen) | `service_records` + `service_record_values` | `id`, `service_type_id`, `billable_quantity`, `field_values` | Alternative to EU lines: approved service events |
| FINV01 (Invoice) | `invoice_header`, `bill_header`, `bill_lines` | All columns | Consolidate bills into invoice |
| FINV01 (Invoice) | `sap_api_config` (via `sap_builder`) | `base_url`, `token_url`, `client_id`, `client_secret`, `company_code`, `payment_term` | SAP API credentials and defaults |
| FINV01 (Invoice) | `gst_api_config` (via `gsp_client`) | `api_base_url`, `asp_id`, `asp_secret`, `gstin`, `public_key_path` | GST GSP credentials for IRN |
| FCN01 (Credit Note) | `invoice_header` | `id`, `invoice_number`, `customer_name`, `grand_total` | Dropdown to link credit note to invoice |
| FCN01 (Credit Note) | `invoice_lines` | `id`, `service_name`, `gl_code`, `sac_code` | Pre-fill credit note lines from original invoice lines |
| FCAM01 (Agreements) | `vessel_customers` (via VCUM01 model) | `id`, `name` | Customer selector for agreement |
| FCAM01 (Agreements) | `vessel_importer_exporters` (via VIEM01 model) | `id`, `name` | Importer/exporter selector for agreement |
| FCAM01 (Agreements) | `finance_service_types` (via FSTM01 model) | `id`, `service_name`, `service_category`, `uom` | Service type selector for agreement lines |
| FCAM01 (Agreements) | `currency_master` (via FCRM01 model) | `currency_code`, `currency_name` | Currency selector for agreement |
| FSTM01 (Service Types) | `gst_rates` (via FGRM01 model) | `id`, `rate_name`, `cgst_rate`, `sgst_rate`, `igst_rate` | Assign default GST rate to service type |
| FSAP01 (Payments) | `invoice_header` | `id` | Link incoming payment to invoice (`invoice_id` FK) |

---

## 4. Data Flow Diagram

```
MASTER DATA LAYER
=================
vessel_customers  ──┐
vessel_agents     ──┤   (name, gstin, gst_state_code,
vessel_importer_   ─┤    gl_code, sap_customer_code,
  exporters          │    billing_address, pan, ...)
                    │
gst_rates       ────┤
finance_service_    │
  types         ────┤
customer_agr-       │
  eements       ────┤
currency_master ────┤
                    │
                    ▼
TRANSACTION LAYER — SOURCE EVENTS
==================================
[VCN01]  vcn_header                [MBC01]  mbc_header
    │                                   │
    └──────────────┬────────────────────┘
                   │ (source_type / source_id)
                   ▼
         [LUEU01] lueu_lines             [SRV01] service_records
          (EU loading/unloading)          (custom EAV service events)
          is_billed = 0 (unbilled)        doc_status = 'Approved'
                   │                              │
                   └──────────────┬───────────────┘
                                  │
                                  ▼
BILLING LAYER
=============
[FIN01] bill_header + bill_lines
   • Created from unbilled EU lines and/or approved service records
   • GST computed per line using gst_rates
   • Rate auto-filled from customer_agreements if available
   • Approval workflow: Draft → Pending Approval → Approved
   • On save: lueu_lines.is_billed = 1, bill_id = <bill_id>
             service_records.is_billed = 1, bill_id = <bill_id>
                   │
                   │ (one or more approved bills → one invoice)
                   ▼
INVOICE LAYER
=============
[FINV01] invoice_header + invoice_lines + invoice_bill_mapping
   • Bill lines copied to invoice_lines (with profit_center, cost_center)
   • Invoice number: {series}{YYYY}-{NNNN}  e.g. INV2026-0042
   • Financial year computed from invoice_date (Apr-Mar)
   • On creation: bill_header.bill_status = 'Invoiced'
                   │
          ┌────────┴────────┐
          │                 │
          ▼                 ▼
[FCN01]                [FINV01 / FSAP01]
Credit notes           Payments
  credit_note_header     advance_receipts
  credit_note_lines      customer_incoming_payments
  (linked to             gl_journal_vouchers
   invoice_header)
          │                 │
          └────────┬────────┘
                   │
                   ▼
EXTERNAL INTEGRATION LAYER
===========================
      ┌──────────────────────────────────────┐
      │  SAP FI (JSW DynaportInvoice REST)   │
      │  sap_api_config (SAPCFG)             │
      │  sap_builder.py → sap_client.py      │
      │  → invoice_header.sap_document_number│
      │  → credit_note_header.sap_doc_number │
      └──────────────────────────────────────┘
      ┌──────────────────────────────────────┐
      │  GST IRP (e-Invoice via GSP)         │
      │  gst_api_config (GSTCFG)            │
      │  einvoice_builder.py → gsp_client.py │
      │  → invoice_header.gst_irn            │
      │  → invoice_header.gst_ack_number     │
      │  → credit_note_header.gst_irn        │
      └──────────────────────────────────────┘
                   │
                   ▼
AUDIT LOG
=========
[FLOG01] integration_logs
   • Every SAP and GST API call logged with request/response
   • Filterable by integration_type, status, date range
```

---

## 5. Key DB Tables

| Table | Module Owner | Purpose |
|-------|-------------|---------|
| `bill_header` | FIN01 | One row per bill; links to customer, source (VCN/MBC), contains totals and approval status |
| `bill_lines` | FIN01 | Line items per bill; can reference `lueu_lines.id` or `service_records.id`; carries GST breakdown |
| `invoice_header` | FIN01 / FINV01 | Tax invoice header; carries `invoice_number`, `financial_year`, SAP doc number, IRN, QR code |
| `invoice_lines` | FIN01 / FINV01 | Invoice line items copied from bill lines; carries `profit_center`, `cost_center`, `sac_code` |
| `invoice_bill_mapping` | FIN01 / FINV01 | Junction table: which bills were rolled into which invoice |
| `credit_note_header` | FCN01 | Credit note (CN) header; FK to `invoice_header.id`; status, SAP doc number, IRN |
| `credit_note_lines` | FCN01 | Credit note line items; optionally FK to `invoice_lines.id` (original line being reversed) |
| `customer_agreements` | FCAM01 | Rate card header per customer; validity dates, approval status, currency |
| `customer_agreement_lines` | FCAM01 | Per-service-type rate within an agreement; `rate`, `uom`, `min_charge`, `max_charge` |
| `finance_service_types` | FSTM01 | Service type definitions: `service_code`, `sac_code`, `gl_code`, `sap_gl_account`, `sap_tax_code`, `sap_profit_center`, `sap_cost_center`, `gst_rate_id`, `has_custom_fields` |
| `service_field_definitions` | FSTM01 | EAV schema: custom fields per service type (type, label, formula, display order) |
| `gst_rates` | FGRM01 | GST rate slabs: `rate_name`, `cgst_rate`, `sgst_rate`, `igst_rate`, `effective_from/to` |
| `currency_master` | FCRM01 | Currency codes, symbols, base currency flag |
| `currency_exchange_rates` | FCRM01 | Exchange rate history per currency pair with effective date |
| `service_records` | SRV01 | Billable service event header: `record_number`, `service_type_id`, `source_type/id`, `billable_quantity`, `doc_status`, `is_billed` |
| `service_record_values` | SRV01 | EAV values for a service record: FK to `service_field_definitions` |
| `advance_receipts` | FSAP01 | Advance payment receipts from a party; `party_type`, `amount`, SAP doc number |
| `customer_incoming_payments` | FSAP01 | Incoming payment against a specific invoice; FK to `invoice_header` |
| `gl_journal_vouchers` | FSAP01 | Manual GL journal vouchers (header only — lines in `gl_jv_lines`) |
| `gl_jv_lines` | FSAP01 | Journal voucher lines (debit/credit legs) |
| `integration_logs` | FLOG01 | Audit log of all SAP and GST API calls: `integration_type`, `status`, request payload, response |
| `sap_api_config` | SAPCFG | SAP REST API credentials per environment: `base_url`, `token_url`, `client_id`, `client_secret`, `company_code`, `payment_term`, `is_active` |
| `gst_api_config` | GSTCFG | GST GSP credentials per environment: `api_base_url`, `asp_id`, `asp_secret`, `gstin`, `public_key_path`, `is_active` |
| `vessel_agents` | VAM01 | Agent master: `name`, `sap_customer_code`, `gl_code`, `gstin`, `gst_state_code`, `pan`, billing address fields, `default_currency`, `is_active` |
| `vessel_customers` | VCUM01 | Customer/consignee master: same billing fields as agents plus `company_code` (for inter-company SAP postings) |
| `vessel_importer_exporters` | VIEM01 | Importer/exporter master: used by FCAM01 agreement entry |
| `lueu_lines` | LUEU01 | EU (loading/unloading) lines: `service_type_id`, `is_billed`, `bill_id` updated by FIN01 on billing |

---

## 6. Inter-Module Relationships

### FIN01 → FINV01

FIN01's `model.py` contains **both** bill and invoice functions. FINV01 is a pure view/API layer that **imports** FIN01's model (`from modules.FIN01 import model`). FINV01 adds the UI for invoice list, generation, printing, GSTR-1 export, SAP posting, and IRN generation. FIN01's legacy invoice routes redirect to FINV01.

```
FIN01.bills  ──(approval)──►  FIN01.generate_bill  ──►  FINV01.generate_invoice
                                (bill_header)                (invoice_header)
```

### FINV01 → FCN01

FCN01 reads `invoice_header` directly to populate the invoice dropdown in the credit note entry form. A credit note header carries `invoice_id` as a foreign key. Credit note lines optionally reference `invoice_lines.id` so the system knows which original line is being credited.

```
FINV01.invoice_header  ──(FK: invoice_id)──►  FCN01.credit_note_header
FINV01.invoice_lines   ──(FK: invoice_line_id)──►  FCN01.credit_note_lines
```

### FSTM01 → FGRM01

FSTM01 views load GST rates via `gst_model.get_all_gst_rates()` (FGRM01 model) so each service type can be assigned a GST rate slab.

```
FGRM01.gst_rates  ──(FK: gst_rate_id)──►  FSTM01.finance_service_types
```

### FCAM01 → FSTM01 + FCRM01 + VIEM01 + VCUM01

The agreement entry form loads all four master sources:
- Service types from FSTM01 (for agreement line rate card)
- Currencies from FCRM01 (agreement currency)
- Importers from VIEM01 (agreement party)
- Customers from VCUM01 (agreement party)

### FIN01 → FCAM01 (rate lookup)

During bill generation, FIN01 queries `customer_agreements` and `customer_agreement_lines` by `customer_id` and `service_type_id` to auto-fill the agreed rate for a line. Only `Approved`, `is_active=1`, date-valid agreements are used.

### FIN01 → SRV01

FIN01's `get_service_records` API endpoint calls `SRV01.model.get_unbilled_records_for_source()` to pull approved, unbilled service records as an alternative source for bill lines. On saving a bill line with a `service_record_id`, SRV01's `service_records.is_billed` is set to 1.

### FINV01 / FCN01 → SAPCFG + GSTCFG

FINV01 and FDCN01 use `sap_builder.py` + `sap_client.py` for SAP posting and SAP IRN fetch. FINV01 and FCN01 use `einvoice_builder.py` + `gsp_client.py` for direct GST IRN generation. These utilities read from `sap_api_config` and `gst_api_config`, administered through SAPCFG and GSTCFG.

### All integrations → FLOG01

Every call through `sap_client.py` and `gsp_client.py` writes a row to `integration_logs`. FLOG01 presents a read-only filterable log grid.

---

## 7. Known Issues / TODOs

The following gaps were identified during the code audit. Several match the issues documented in `docs/plans/2026-02-26-accounts-redesign.md`.

### 7.1 CRM01 is not a finance consignee master

The `modules/CRM01/model.py` reads from `conveyor_routes`, **not** a consignee/receiver table. The CRM01 module appears to be a Conveyor Route Master, not related to finance. Any reference to "CRM01 as consignee master" in planning docs appears to be a naming collision. The actual consignee data used by finance lives in `vessel_customers` (VCUM01).

### 7.2 VAM01 agent table expansion — partially done

The `vessel_agents` table now has billing fields (`sap_customer_code`, `gstin`, `gst_state_code`, etc.) in the model, but FIN01's `get_customers_for_billing` endpoint only queries `vessel_customers` and `vessel_importer_exporters`. Agents cannot yet be selected as the billing party in bill generation. The API endpoint would need to be extended to include `vessel_agents` as `customer_type='Agent'`.

### 7.3 FCN01 model queries wrong status field on invoice lookup

In `FCN01/model.py`, `get_invoices_for_dropdown()` filters with `WHERE status != 'Cancelled'` but the `invoice_header` table uses the column `invoice_status`, not `status`. This query will silently return all invoices including cancelled ones. The correct filter is `WHERE invoice_status != 'Cancelled'`.

### 7.4 FSAP01 is a structural stub

The FSAP01 module has correct model and API layers (advance receipts, incoming payments, GL JVs) but `fsap01.html` renders a single page template with `permissions` passed. There is no table/Tabulator grid or SAP posting integration wired for payments — the payments data layer is ready but the UI is likely incomplete.

### 7.5 No virtual accounts per customer

The redesign plan calls for a `customer_virtual_accounts` sub-table under `vessel_customers` so the finance team can record multiple virtual bank accounts per customer. This table does not exist in any of the audited model files.

### 7.6 EU line splitting not implemented

The redesign plan identifies the need to split a single `lueu_lines` row into two partial quantities for billing (e.g., two customers sharing a berth). No split logic exists in FIN01 or LUEU01.

### 7.7 GSTR-1 export is client-side only — no server-side filing

FINV01 exports GSTR-1 B2B JSON via the API endpoint (`/api/module/FINV01/export/gstr1-b2b`), but there is no filing status tracked on `invoice_header` and no reconciliation mechanism. If filing happens outside the system, the invoices will have no audit trail of the filing.

### 7.8 SAP cancellation window is hard-coded to 24 hours

`FINV01/views.py` enforces a 24-hour window for SAP cancellation (FB08 reversal). This is hard-coded and is not configurable via SAPCFG. If the SAP policy changes, a code change is required.

### 7.9 FIN01 module config stores port GST details redundantly

FIN01's `get_port_config` endpoint reads `port_gstin`, `seller_gstin`, `seller_legal_name`, etc. from the module config table (not from `sap_api_config` or `gst_api_config`). This means the port's GST identity is stored in three places. Consolidation into GSTCFG would reduce the chance of inconsistencies.

### 7.10 Legacy FCN01 SAP posting removed

Debit note and SAP-postable credit note handling now lives in FDCN01. FCN01 remains a legacy credit note workflow and no longer owns SAP posting.

### 7.11 `sap_document_number` field missing from `advance_receipts` but included in model SQL

The `FSAP01` model's `save_advance_receipt` includes `sap_document_number` and `sap_fiscal_year` in the UPDATE statement. Whether the DB table was migrated to include these columns is not confirmed from code alone; this should be verified against the Alembic migration history.

### 7.12 Exchange rate is stored on invoice but not validated against FCRM01

`invoice_header` carries an `exchange_rate` field but the system does not automatically look it up from `currency_exchange_rates`. The value is passed in from the frontend. This could lead to stale rates being used if the user does not manually check.

---

## 8. Approval Workflows

Two modules have configurable approval workflows driven by the `module_config` table:

| Module | Config Key | Behaviour |
|--------|-----------|-----------|
| FIN01 (Bills) | `approver_id`, `approval_add` | If `approval_add` is set, new bills start as "Pending Approval"; only the designated approver or admin can approve/reject |
| FCAM01 (Agreements) | `approver_id`, `approval_add` | Same pattern; agreements start as "Pending" if approval required |

Invoices (FINV01) do not have a separate approval step — they are created from already-approved bills.

---

## 9. SAP Integration Summary

| Document Type | Trigger | API Endpoint | SAP Document Type | Reversal |
|--------------|---------|-------------|------------------|---------|
| Invoice | FINV01 "Post to SAP" button | `POST /api/module/FINV01/invoice/post-sap` | `Invoice_Credit: "I"`, `Document_type: "INV"` | FINV01 cancel-SAP (FB08, within 24h) |
| Debit Note | FDCN01 "Post SAP" button | `POST /api/module/FDCN01/post-sap` | `Document_Type: "Y1"` | Post correcting DN/CN document as needed |
| Credit Note | FDCN01 "Post SAP" button | `POST /api/module/FDCN01/post-sap` | `Document_Type: "Y2"` | Post correcting DN/CN document as needed |

**SAP field source summary:**

| SAP Field | Source |
|-----------|--------|
| `Company_code` | `vessel_customers.company_code` if set (inter-company), else `sap_api_config.company_code` |
| `Customer_Code` | `vessel_customers.sap_customer_code` |
| `GL_account` | `finance_service_types.sap_gl_account` |
| `Tax_Code` | `finance_service_types.sap_tax_code` |
| `HSN_SAC` | `invoice_lines.sac_code` (sourced from `finance_service_types.sac_code`) |
| `Profit_Center` | `finance_service_types.sap_profit_center` |
| `Cost_Center` | `finance_service_types.sap_cost_center` |
| `Payment_Term` | `sap_api_config.payment_term` |

---

## 10. GST / e-Invoice Summary

| Action | Trigger | Stores |
|--------|---------|--------|
| Generate IRN (Invoice) | FINV01 "Generate IRN" button | `invoice_header.gst_irn`, `invoice_header.gst_ack_number` |
| Cancel IRN (Invoice) | FINV01 "Cancel IRN" button | Clears `gst_irn`, `gst_ack_number`, `gst_ack_date`, `gst_qr_code` |
| Generate IRN (Credit Note) | FCN01 "Generate IRN" button | `credit_note_header.gst_irn`, `credit_note_header.gst_ack_number` |
| GSTR-1 B2B Export | FINV01 multi-select export | JSON download only — no filing status stored |

The GSP client uses AES-256/RSA encryption (`pycryptodome`) and reads the IRP public key path from `gst_api_config.public_key_path`.

---

*End of document.*
