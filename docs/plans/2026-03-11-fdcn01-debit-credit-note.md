# FDCN01 — Rate Revision Debit/Credit Note Module

## Purpose

Handle mid-agreement rate revision billing. When customer agreement rates change partway through a term, this module calculates the difference against already-invoiced lines and generates a Debit Note (if new rate is higher) or Credit Note (if new rate is lower).

## Scenario

- Customer has an active agreement for 1+ years
- 6 months in, rates are revised
- Already-invoiced items need the difference billed (DN) or refunded (CN)
- User selects the original invoice, enters revised rates, system calculates difference

## Module Info

- **Code:** FDCN01
- **Name:** Debit / Credit Note
- **Approach:** Semi-automatic — user picks invoice, sees lines with original rates, enters revised rates, system calculates difference and GST

---

## Database Tables

### `fdcn_doc_series`

| Column | Type | Description |
|--------|------|-------------|
| id | SERIAL PK | |
| name | VARCHAR | Series name (e.g., "JSW Dharamtar DN") |
| prefix | VARCHAR | e.g., "DN", "CN" |
| type | VARCHAR | "DN" or "CN" |
| is_default | BOOLEAN | Default series for type |
| is_active | BOOLEAN | |

### `fdcn_header`

| Column | Type | Description |
|--------|------|-------------|
| id | SERIAL PK | |
| doc_number | VARCHAR UNIQUE | e.g., DN/25-26/0001 |
| doc_type | VARCHAR | "DN" or "CN" |
| doc_date | DATE | |
| doc_series | VARCHAR | Prefix used |
| doc_series_seq | INT | Sequence number |
| financial_year | VARCHAR | e.g., "25-26" |
| original_invoice_id | INT FK | → invoice_header.id |
| original_invoice_number | VARCHAR | |
| customer_id | INT | |
| customer_type | VARCHAR | "Customer" or "Agent" |
| customer_name | VARCHAR | |
| customer_gstin | VARCHAR | |
| customer_gst_state_code | VARCHAR | |
| customer_gl_code | VARCHAR | |
| subtotal | DECIMAL | |
| cgst_amount | DECIMAL | |
| sgst_amount | DECIMAL | |
| igst_amount | DECIMAL | |
| total_amount | DECIMAL | |
| doc_status | VARCHAR | Draft / Pending Approval / Approved / Posted to SAP / Posted to GST / Cancelled |
| rejection_reason | TEXT | |
| created_by | VARCHAR | |
| created_date | DATE | |
| approved_by | VARCHAR | |
| approved_date | DATE | |
| sap_document_number | VARCHAR | |
| sap_posting_date | DATE | |
| sap_fiscal_year | VARCHAR | |
| sap_company_code | VARCHAR | |
| gst_irn | VARCHAR | |
| gst_ack_number | VARCHAR | |
| gst_ack_date | DATE | |
| gst_qr_code | TEXT | |
| posted_by | VARCHAR | |
| posted_date | DATE | |
| remarks | TEXT | |

### `fdcn_lines`

| Column | Type | Description |
|--------|------|-------------|
| id | SERIAL PK | |
| fdcn_id | INT FK | → fdcn_header.id |
| invoice_line_id | INT FK | → invoice_lines.id |
| service_type_id | INT | |
| service_name | VARCHAR | |
| service_description | TEXT | |
| quantity | DECIMAL | |
| uom | VARCHAR | |
| original_rate | DECIMAL | Rate from original invoice line |
| revised_rate | DECIMAL | New rate entered by user |
| rate_difference | DECIMAL | revised_rate - original_rate |
| line_amount | DECIMAL | quantity * abs(rate_difference) |
| gst_rate_id | INT | |
| cgst_rate | DECIMAL | |
| sgst_rate | DECIMAL | |
| igst_rate | DECIMAL | |
| cgst_amount | DECIMAL | |
| sgst_amount | DECIMAL | |
| igst_amount | DECIMAL | |
| line_total | DECIMAL | line_amount + taxes |
| gl_code | VARCHAR | |
| sac_code | VARCHAR | |
| remarks | VARCHAR | |

---

## User Workflow

1. Open FDCN01 → list of all DN/CNs with status filters
2. Click "New" → select Customer Type → Customer/Agent → Original Invoice
3. System loads invoice lines with original rates pre-filled
4. User enters revised rate per line (skip lines not being revised)
5. System calculates: `rate_difference = revised_rate - original_rate`
   - Positive difference → Debit Note
   - Negative difference → Credit Note
   - All selected lines must be same direction (no mixing DN and CN lines)
6. GST calculated per line using service type's GST rate
7. Save → approval workflow → once approved → post to SAP & generate IRN

## Approval Workflow

Config-driven via `module_config` (approver_id, approval_add):

```
Draft → Submit → Pending Approval → Approve → Approved
                                   → Reject → Draft (with reason)
```

Approver/admin can auto-approve on save.

---

## Routes

### Page Routes

| Route | Method | Purpose |
|-------|--------|---------|
| `/module/FDCN01/` | GET | Redirect to list |
| `/module/FDCN01/list` | GET | List all DN/CNs |
| `/module/FDCN01/entry` | GET | Create/edit form |
| `/module/FDCN01/doc-series` | GET | Doc series master |
| `/module/FDCN01/print/<id>` | GET | Print view |

### API Routes

| Route | Method | Purpose |
|-------|--------|---------|
| `/api/module/FDCN01/data` | GET | Paginated list data |
| `/api/module/FDCN01/save` | POST | Save header + lines |
| `/api/module/FDCN01/submit` | POST | Submit for approval |
| `/api/module/FDCN01/approve` | POST | Approve |
| `/api/module/FDCN01/reject` | POST | Reject |
| `/api/module/FDCN01/delete` | POST | Delete draft |
| `/api/module/FDCN01/invoices/<type>/<id>` | GET | Invoices for customer |
| `/api/module/FDCN01/invoice-lines/<id>` | GET | Lines for invoice |
| `/api/module/FDCN01/post-sap` | POST | Post to SAP |
| `/api/module/FDCN01/generate-irn` | POST | Generate IRN |
| `/api/module/FDCN01/cancel-irn` | POST | Cancel IRN |
| `/api/module/FDCN01/doc-series/data` | GET | List doc series |
| `/api/module/FDCN01/doc-series/save` | POST | Save doc series |
| `/api/module/FDCN01/doc-series/delete` | POST | Delete doc series |

---

## Integration

### SAP
- Add `build_fdcn_payload()` / `build_fdcn_credit_payload()` to sap_builder
- Post via `sap_client.post_invoice_to_sap()` with DN/CN document type
- Log to integration_logs (FLOG01)

### GST e-Invoice
- Add `build_einvoice_from_fdcn()` to einvoice_builder
- DN: document type `DBN`, CN: document type `CRN`
- Original invoice number/date as reference per GST schema v1.1
- Post via `gsp_client.generate_irn()`
- Log to integration_logs (FLOG01)

### FSAP01 Audit Trail
- Query fdcn_header for SAP/GST posting audit alongside invoices and credit notes

---

## File Structure

```
modules/FDCN01/
  __init__.py              — Blueprint + MODULE_INFO
  views.py                 — All routes & API endpoints
  model.py                 — Database CRUD operations
  fdcn01_list.html         — List page with filters, pagination, actions
  fdcn01_entry.html        — Create/edit form
  fdcn01_print.html        — Print layout (Original + Duplicate)
  fdcn01_doc_series.html   — Doc series master
```

## Registration

**app.py:**
```python
from modules.FDCN01 import bp as fdcn01_bp, MODULE_INFO as fdcn01_info
register_module(fdcn01_info['code'], fdcn01_info['name'], fdcn01_bp)
```

**base.html sidebar:**
- Add "DN/CN Doc Series" under the Doc Series accordion section
