# PORTMAN <-> SAP Data Flow and Payload Guide

Last updated: 2026-03-30

## 1. Scope

This document explains how finance/accounting data flows between PORTMAN (PMS) and SAP, including:

- Direction of flow (PMS to SAP, SAP to PMS)
- Business process mapping with SAP T-codes
- API endpoints used in PORTMAN
- Payload formats (internal API payloads and SAP DynaportInvoice payloads)
- Current implementation status

## 2. Process Mapping (Direction + T-Code)

| Process | SAP T-Code | Direction | PORTMAN Module | Current Status |
| --- | --- | --- | --- | --- |
| Customer Invoice Posting | FB70 | PMS -> SAP | FINV01 | Implemented (API + payload + logging) |
| Customer Invoice Cancellation/Reversal (within 24h) | FB08 | PMS -> SAP | FINV01 | Implemented (24h rule enforced) |
| Customer Debit Note Posting | FB70 | PMS -> SAP | FDCN01 | Implemented (API + payload + logging) |
| Customer Credit Memo Posting | FB75 | PMS -> SAP | FDCN01 | Implemented (API + payload + logging) |
| Advance from Customer | F-29 | SAP -> PMS | FSAP01 | Manual capture in PMS (no SAP webhook/pull yet) |
| Customer Incoming Payment | F-28 | SAP -> PMS | FSAP01 | Manual capture in PMS (no SAP webhook/pull yet) |
| GL to GL JV | FB50 | SAP -> PMS | FSAP01 | Manual capture in PMS (no SAP webhook/pull yet) |
| Reversal JV | FB08 | SAP -> PMS | FSAP01 | Manual capture in PMS (no SAP webhook/pull yet) |

Note: For invoice cancellation beyond 24 hours, PORTMAN blocks FB08 reversal and requires creating a new Debit Note/Credit Note process.

## 3. SAP Configuration and Connectivity

### 3.1 Configuration Module

SAP credentials and base URL are managed in `SAPCFG`:

- UI: `/module/SAPCFG/`
- Save config: `POST /api/module/SAPCFG/save`
- Set active environment: `POST /api/module/SAPCFG/set-active`
- Test connection: `POST /api/module/SAPCFG/test-connection`

Active config is read from table `sap_api_config` (where `is_active = 1`).

### 3.2 OAuth Token Request (to SAP)

PORTMAN requests bearer token before posting to SAP:

```http
POST {base_url}/oauth2/api/v1/generateToken
Content-Type: application/x-www-form-urlencoded
```

```text
grant_type=client_credentials
client_id=<client_id>
client_secret=<client_secret>
```

### 3.3 SAP Posting Endpoint

All outbound postings use:

```http
POST {base_url}/RESTAdapter/DynaportInvoice
Authorization: Bearer <token>
Content-Type: application/json
```

## 4. PMS -> SAP Flow (Outbound)

## 4.1 Invoice Posting (FINV01, FB70)

### Trigger

- UI action: "Post SAP" in FINV01 invoice list
- Internal API:

```http
POST /api/module/FINV01/invoice/post-sap
Content-Type: application/json
```

```json
{
  "invoice_id": 123
}
```

### Processing Steps

1. Read `invoice_header` + `invoice_lines`.
2. Build SAP payload using `sap_builder.build_invoice_payload()`.
3. Post to SAP via `sap_client.post_invoice_to_sap()`.
4. On success, update `invoice_header`:
   `sap_document_number`, `sap_posting_date`, `posted_by`, `posted_date`, `invoice_status='Posted to SAP'`.
5. Write request/response log into `integration_logs`.

### SAP Payload Example (Invoice)

```json
{
  "Record_Header": [
    {
      "Invoice_Credit": "I",
      "Document_type": "INV",
      "Company_code": "5171",
      "Business_place": "5171",
      "Section_code": "5171",
      "Credit_Control_Area": "5171",
      "Plant": "5171",
      "Customer_code": "I510785",
      "Payment_term": "51",
      "Document_date": "20260226",
      "Posting_date": "20260226",
      "Base_date": "20260226",
      "Header_text": "INV2026-0001",
      "Reference_no": "INV2026-0001",
      "ITEM": [
        {
          "GL_account": "4101076030",
          "Tax_code": "50",
          "Profit_center": "5171000000",
          "Cost_center": "",
          "Amount": "100000.00",
          "Item_text": "Stevedoring Charges"
        },
        {
          "GL_account": "4101076030",
          "Tax_code": "50",
          "Profit_center": "5171000000",
          "Cost_center": "",
          "Amount": "9000.00",
          "Item_text": "CGST @ 9.0%"
        },
        {
          "GL_account": "4101076030",
          "Tax_code": "50",
          "Profit_center": "5171000000",
          "Cost_center": "",
          "Amount": "9000.00",
          "Item_text": "SGST @ 9.0%"
        }
      ]
    }
  ]
}
```

### Success Response (internal API example)

```json
{
  "success": true,
  "sap_document_number": "1900001234",
  "message": "Posted to SAP successfully",
  "log_id": 890
}
```

## 4.2 Invoice Cancellation/Reversal (FINV01, FB08, within 24h)

### Trigger

```http
POST /api/module/FINV01/invoice/cancel-sap
Content-Type: application/json
```

```json
{
  "invoice_id": 123
}
```

### Validation Rules

1. Invoice must exist.
2. Invoice must already have `sap_document_number`.
3. Invoice must not already be `Cancelled`.
4. Cancellation allowed only within 24 hours from `sap_posting_date` (fallback: `posted_date`, then `created_date`).
5. If >24 hours: API returns error and user must create DN/CN workflow.

### SAP Payload Example (Reversal)

PORTMAN builds reversal as a reverse posting payload:

- `Invoice_Credit = "C"`
- `Document_type = "CRN"`
- `Header_text` and `Reference_no` carry original SAP document/invoice reference

```json
{
  "Record_Header": [
    {
      "Invoice_Credit": "C",
      "Document_type": "CRN",
      "Header_text": "REV 1900001234",
      "Reference_no": "1900001234",
      "ITEM": [
        {
          "GL_account": "4101076030",
          "Tax_code": "50",
          "Amount": "100000.00",
          "Item_text": "Stevedoring Charges"
        }
      ]
    }
  ]
}
```

### On Success

- `invoice_status` set to `Cancelled`
- reversal info appended in `remarks`
- integration log row saved

## 4.3 Debit / Credit Note Posting (FDCN01, FB70 / FB75)

### Trigger

```http
POST /api/module/FDCN01/post-sap
Content-Type: application/json
```

```json
{
  "id": 45
}
```

### Behavior

- Reads `fdcn_header` + `fdcn_lines`
- Builds payload using `sap_builder.build_fdcn_payload()`
- Uses `Document_Type="Y1"` for `doc_type='DN'`
- Uses `Document_Type="Y2"` for `doc_type='CN'`
- Posts through same SAP REST endpoint (`DynaportInvoice`)
- On success updates `fdcn_header.sap_document_number` and marks `doc_status='Posted to SAP'`
- On failure updates `doc_status='SAP Failed'`
- Logs request/response to `integration_logs`

## 5. SAP -> PMS Flow (Inbound)

Current implementation is **manual capture in PORTMAN**, not automated webhook/polling from SAP.

Capture module: `FSAP01`

- UI: `/module/FSAP01/`
- Advance receipts APIs: `/api/module/FSAP01/advance-receipts*`
- Incoming payments APIs: `/api/module/FSAP01/incoming-payments*`
- GL JV APIs: `/api/module/FSAP01/gl-jvs*`

## 5.1 Advance from Customer (F-29)

### Save API Payload (FSAP01)

```http
POST /api/module/FSAP01/advance-receipts/save
Content-Type: application/json
```

```json
{
  "id": null,
  "receipt_number": "AR-2026-0001",
  "receipt_date": "2026-02-26",
  "party_type": "Customer",
  "party_id": 12,
  "party_name": "ABC Logistics Pvt Ltd",
  "amount": 500000.0,
  "currency": "INR",
  "payment_method": "NEFT",
  "bank_reference": "UTR123456789",
  "sap_document_number": "1500004567",
  "sap_fiscal_year": "2026",
  "status": "Synced",
  "remarks": "Imported from SAP F-29"
}
```

## 5.2 Customer Incoming Payment (F-28)

### Save API Payload (FSAP01)

```http
POST /api/module/FSAP01/incoming-payments/save
Content-Type: application/json
```

```json
{
  "id": null,
  "payment_number": "IP-2026-0010",
  "payment_date": "2026-02-26",
  "party_type": "Customer",
  "party_id": 12,
  "party_name": "ABC Logistics Pvt Ltd",
  "invoice_id": 123,
  "amount": 118000.0,
  "currency": "INR",
  "payment_method": "RTGS",
  "bank_reference": "UTR99887766",
  "sap_document_number": "1400003321",
  "sap_fiscal_year": "2026",
  "status": "Synced",
  "remarks": "Imported from SAP F-28"
}
```

## 5.3 GL to GL JV and Reversal JV (FB50 / FB08)

### Save API Payload (FSAP01)

```http
POST /api/module/FSAP01/gl-jvs/save
Content-Type: application/json
```

```json
{
  "id": null,
  "jv_number": "JV-2026-0042",
  "jv_date": "2026-02-26",
  "description": "Port expense reclassification",
  "total_debit": 25000.0,
  "total_credit": 25000.0,
  "sap_document_number": "1900007890",
  "sap_fiscal_year": "2026",
  "status": "Synced"
}
```

## 6. Field Mapping Used in SAP Invoice Payload

| PORTMAN Source | SAP Field |
| --- | --- |
| `invoice_header.customer_gl_code` | `Customer_code` |
| `invoice_header.invoice_date` | `Document_date`, `Posting_date`, `Base_date` |
| `invoice_header.invoice_number` | `Header_text`, `Reference_no` |
| `invoice_lines.gl_code` | `ITEM[].GL_account` |
| `invoice_lines.sap_tax_code` | `ITEM[].Tax_code` |
| `invoice_lines.profit_center` | `ITEM[].Profit_center` |
| `invoice_lines.cost_center` | `ITEM[].Cost_center` |
| `invoice_lines.line_amount` | `ITEM[].Amount` |
| GST line amounts (`cgst_amount`, `sgst_amount`, `igst_amount`) | Additional `ITEM[]` rows |

Company code resolution:

1. If customer has `company_code`, use it.
2. Else use active `sap_api_config.company_code`.

## 7. Logging and Audit Trail

Outbound SAP calls are logged in `integration_logs` with:

- `integration_type` (`SAP`)
- `source_type` (`Invoice`, `InvoiceReversal`, `CreditNote`, `DebitNote`)
- `source_id`, `source_reference`
- `request_body`, `response_body`
- `status` (`Success` or `Error`)
- `error_message`
- `created_by`, `created_date`

View logs in module `FLOG01`:

- UI: `/module/FLOG01/`
- List API: `GET /api/module/FLOG01/data`
- Detail API: `GET /api/module/FLOG01/detail/<log_id>`

## 8. Current Gaps / Next Step

1. SAP -> PMS is currently manual entry in FSAP01. No webhook/poll integration yet.
2. There is no separate SAP reversal endpoint for FDCN01 documents; corrections are handled by posting the appropriate DN/CN document.
3. Optional enhancement: add a dedicated SAP inbound endpoint to auto-ingest F-29/F-28/FB50/FB08 extracts and mark records as `Synced` automatically.
