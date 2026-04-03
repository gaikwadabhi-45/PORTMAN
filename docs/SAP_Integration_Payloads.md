# SAP Integration - Payload Reference

## Overview

Portbird communicates with SAP ECC via a SAP PI/PO REST adapter. All supported documents are posted as JSON to a single endpoint, and PI middleware transforms them into the appropriate SAP document type.

**Endpoint:** `{base_url}/RESTAdapter/Portbirdinvoice`  
**Method:** `POST`  
**Auth:** OAuth2 client credentials (`/oauth2/api/v1/generateToken`)  
**IRN Fetch:** `GET {base_url}/RESTAdapter/Portbirdinvoice/IRN?Reference={doc_number}`

All payloads share the same outer envelope:

```json
{
  "Record": { "...": "..." }
}
```

---

## SAP Document Types

| SAP Type | Used For |
|----------|----------|
| `DR` | Invoice (FINV01), Invoice Reversal, and Debit Note (FDCN01) |
| `DG` | Credit Note (FDCN01) |

Debit Note uses `DR` — same as a regular invoice. Credit Note uses `DG`. Invoice reversals use `DR` with `Cancellation_Flag = "X"`.

---

## Staging Table Field Reference

### Record (Header) Fields

| Field | Max | Format | Notes |
|-------|-----|--------|-------|
| `Invoice_Type` | 3 | `"I"` / `"C"` | `I` = Invoice / Debit Note; `C` = Credit Note |
| `Company_Code` | | `"5130"` | From SAP config; overridden by customer's `company_code` if set |
| `Document_Date` | | `DD.MM.YYYY` | Invoice / document date |
| `Posting_Date` | | `DD.MM.YYYY` | Same as `Document_Date` |
| `Reference_Text` | 16 | | Invoice or DN/CN number — **unique primary field** |
| `Document_Type` | | `"DR"` / `"DG"` | `DR`=Invoice/Debit Note/Reversal, `DG`=Credit Note — **unique primary field** |
| `Cancellation_Flag` | 3 | `""` / `"X"` | `"X"` only for invoice reversals — **unique primary field** |
| `Customer_Code` | 10 | | Customer SAP GL code from `customer_gl_code` |
| `Invoice_Amount` | 13 | `"118000.00"` | Total incl. GST, minus TDS, plus TCS |
| `Currency` | | `"INR"` | Always INR |
| `Business_Place` | | | From SAP config `business_place`; overridden by customer company code |
| `Section_Code` | | | From SAP config `section_code`; overridden by customer company code |
| `Payment_Term` | 4 | | From active SAP config |
| `Baseline_Date` | | `DD.MM.YYYY` | Same as `Document_Date` |
| `Doc_Header_Text` | 25 | | Same as `Reference_Text`; reversal prefixes with `"REV "` |
| `SERVICE_SALE` | | `"S"` (Serv) / `"A"` (Sale) | From service master `is_sale` flag on the first line. `S` = Service, `A` = Sale |
| `IRN_No` | 64 | | e-Invoice IRN — empty until fetched from SAP (auto) |
| `Ack_No` | 20 | | GST acknowledgement number (auto) |
| `IRN_Date` | 10 | `DD.MM.YYYY` | IRN acknowledgement date (auto) |
| `Nature_of_transaction` | | `"B2B"` / `"B2C"` | `B2B` if customer has GSTIN, else `B2C` |
| `Original_Invoice_No` | | | Present on FDCN01 debit and credit notes |
| `TDS_Amount` | 13 | `"500.00"` / `""` | Header-level total TDS; empty string if zero |
| `TCS_Amount` | 13 | `"100.00"` / `""` | Header-level total TCS; empty string if zero |
| `Item` | | array | One entry per service line |

> **Auto fields** (SAP fills on response): `Processing_Status`, `Fiscal_Year`, `Fiscal_Period`,
> `Push_Date`, `Push_Time`, `Document_Number`, `Message`, `IRN_No`, `Ack_No`, `IRN_Date`.

### Item (Line) Fields

| Field | Max | Notes |
|-------|-----|-------|
| `Service_Code` | 10 | GL Account — `service_code` or `gl_code` from the line |
| `Amount` | 13 | Taxable base amount ± sign; always populated (`"0.00"` if zero) |
| `Plant` | | Line → SAP config `plant_code` → company code |
| `Text` | 25 | Service name, truncated to 25 chars |
| `IGST_GL` | 10 | IGST GL account from service master `sap_igst_gl` |
| `IGST_AMT` | | IGST amount; empty if zero |
| `CGST_GL` | 10 | CGST GL account from service master `sap_cgst_gl` |
| `CGST_AMT` | | CGST amount; empty if zero |
| `SGST_GL` | 10 | SGST GL account from service master `sap_sgst_gl` |
| `SGST_AMT` | | SGST amount; empty if zero |
| `UOM` | | From EU01 line item `quantity_uom` → bill line → invoice line |
| `Unit_Price` | | From customer agreement rate, stored in `bill_lines.rate` → invoice line |
| `Quantity` | | From EU01 line item `quantity` (billed qty) → bill line → invoice line |
| `HSN_SAC` | 16 | SAC / HSN code from line |
| `Tax_Amount` | 13 | Total GST per line (CGST + SGST + IGST); to be confirmed total or bifurcated |
| `TDS_GL` | | TDS GL account — service master `sap_tds_gl` → SAP config `tds_gl` |
| `TDS_Amount` | 13 | Line-level TDS ± sign; empty if zero |
| `TCS_GL` | | TCS GL account — service master `sap_tcs_gl` → SAP config `tcs_gl` |
| `TCS_Amount` | 13 | Line-level TCS ± sign; empty if zero |
| `Round_off_GL` | | Round-off GL account from SAP config `round_off_gl` |
| `Rounding_off` | 13 | Rounding adjustment ± sign; empty if zero |
| `Business_Place` | | From line → SAP config `business_place` → company code |
| `Section_Code` | | From line → SAP config `section_code` → company code |
| `Tax_Code` | | Line `sap_tax_code` → SAP config `tax_code` |
| `Profit_Center` | | Line `profit_center` → SAP config `profit_center` |

### GL Account Precedence Summary

| GL Field | Level 1 (highest) | Level 2 | Level 3 (fallback) |
|---|---|---|---|
| `IGST_GL` | line override | service master `sap_igst_gl` | — |
| `CGST_GL` | line override | service master `sap_cgst_gl` | — |
| `SGST_GL` | line override | service master `sap_sgst_gl` | — |
| `TDS_GL` | line override | service master `sap_tds_gl` | SAP config `tds_gl` |
| `TCS_GL` | line override | service master `sap_tcs_gl` | SAP config `tcs_gl` |
| `Round_off_GL` | — | — | SAP config `round_off_gl` |
| `Plant` | line | SAP config `plant_code` | company code |
| `Business_Place` | line | SAP config `business_place` | company code |
| `Section_Code` | line | SAP config `section_code` | company code |

### Invoice_Amount Calculation

```text
Invoice_Amount = total_amount (from header)
               - TDS
               + TCS
```

If `total_amount` is zero or null, it is computed by summing `line_amount + cgst + sgst + igst` across all lines before applying TDS/TCS.

---

## Scenario 1 — Invoice (FINV01)

**Trigger:** User clicks "Post to SAP" on an approved invoice.  
**Builder:** `sap_builder.build_invoice_payload(invoice_header, invoice_lines)`  
**`Invoice_Type`:** `I`  **`Document_Type`:** `DR`

```json
{
  "Record": {
    "Invoice_Type": "I",
    "Company_Code": "5130",
    "Document_Date": "24.04.2025",
    "Posting_Date": "24.04.2025",
    "Document_Type": "DR",
    "Reference_Text": "dppl/25-26/0001",
    "Cancellation_Flag": "",
    "Customer_Code": "5100001",
    "Invoice_Amount": "9212850.00",
    "Currency": "INR",
    "Business_Place": "5130",
    "Section_Code": "5130",
    "Payment_Term": "Z030",
    "Baseline_Date": "24.04.2025",
    "Doc_Header_Text": "dppl/25-26/0001",
    "SERVICE_SALE": "S",
    "IRN_No": "",
    "Ack_No": "",
    "IRN_Date": "",
    "Nature_of_transaction": "B2B",
    "TDS_Amount": "",
    "TCS_Amount": "",
    "Item": [
      {
        "Service_Code": "OT0051",
        "Amount": "7807500.00",
        "Plant": "5130",
        "Text": "CARGO HANDLING CHARGES",
        "IGST_GL": "",
        "IGST_AMT": "",
        "CGST_GL": "2400101000",
        "CGST_AMT": "702675.00",
        "SGST_GL": "2400102000",
        "SGST_AMT": "702675.00",
        "UOM": "MT",
        "Unit_Price": "173.50",
        "Quantity": "45000",
        "HSN_SAC": "996719",
        "Tax_Amount": "1405350.00",
        "TDS_GL": "2400001000",
        "TDS_Amount": "",
        "TCS_GL": "",
        "TCS_Amount": "",
        "Round_off_GL": "4200000001",
        "Rounding_off": "",
        "Business_Place": "5130",
        "Section_Code": "5130",
        "Tax_Code": "50",
        "Profit_Center": "5130000000"
      }
    ]
  }
}
```

**Status after success:** `invoice_status = 'Posted to SAP'`, `sap_document_number` saved.

---

## Scenario 2 — Invoice Reversal (FINV01 cancellation)

**Trigger:** User cancels an already SAP-posted invoice.  
**Builder:** `sap_builder.build_invoice_reversal_payload(invoice_header, invoice_lines)`  
**`Invoice_Type`:** `I`  **`Cancellation_Flag`:** `"X"`

| Field | Invoice | Reversal |
|-------|---------|----------|
| `Invoice_Type` | `I` | `I` |
| `Document_Type` | `DR` | `DR` |
| `Reference_Text` | `dppl/25-26/0001` | SAP document number or invoice number |
| `Doc_Header_Text` | `dppl/25-26/0001` | `REV 1800000123` |
| `Cancellation_Flag` | `""` | `"X"` |

SAP processes `Cancellation_Flag = X` and posts a reversal entry.

---

## Scenario 3 — Debit Note (FDCN01)

**Trigger:** User clicks "Post to SAP" on an approved debit note.  
**Builder:** `sap_builder.build_fdcn_payload(fdcn_header, fdcn_lines)`  
**`doc_type`:** `DN` → **`Invoice_Type`:** `I`  **`Document_Type`:** `DR`

**Creation types that produce a DN:** `rate_revision`, `manual`

```json
{
  "Record": {
    "Invoice_Type": "I",
    "Company_Code": "5130",
    "Document_Date": "30.03.2026",
    "Posting_Date": "30.03.2026",
    "Document_Type": "DR",
    "Reference_Text": "DN/25-26/0001",
    "Cancellation_Flag": "",
    "Customer_Code": "5100001",
    "Invoice_Amount": "11800.00",
    "Currency": "INR",
    "Business_Place": "5130",
    "Section_Code": "5130",
    "Payment_Term": "Z030",
    "Baseline_Date": "30.03.2026",
    "Doc_Header_Text": "DN/25-26/0001",
    "IRN_No": "",
    "Ack_No": "",
    "IRN_Date": "",
    "Nature_of_transaction": "B2B",
    "Original_Invoice_No": "dppl/25-26/0001",
    "TDS_Amount": "",
    "TCS_Amount": "",
    "Item": [{ "...": "same structure as Invoice item" }]
  }
}
```

---

## Scenario 4 — Credit Note (FDCN01)

**Trigger:** User clicks "Post to SAP" on an approved credit note.  
**Builder:** `sap_builder.build_fdcn_payload(fdcn_header, fdcn_lines)`  
**`doc_type`:** `CN` → **`Invoice_Type`:** `C`  **`Document_Type`:** `DG`

**Creation types that produce a CN:**

| `creation_type` | Meaning |
|---|---|
| `rate_revision` | Revised rate is lower than original |
| `manual` | User manually created the credit note |
| `eu_deletion` | Auto-created after invoiced EU line deletion |

```json
{
  "Record": {
    "Invoice_Type": "C",
    "Company_Code": "5130",
    "Document_Type": "DG",
    "Reference_Text": "CN/25-26/0001",
    "Cancellation_Flag": "",
    "Original_Invoice_No": "dppl/25-26/0001",
    "Item": [{ "...": "same structure as Invoice item" }]
  }
}
```

---

## Scenario Matrix

| Document | Module | `doc_type` | `creation_type` | `Invoice_Type` | SAP `Document_Type` | `Cancellation_Flag` | `Original_Invoice_No` |
|----------|--------|------------|-----------------|----------------|---------------------|---------------------|-----------------------|
| Invoice | FINV01 | — | — | `I` | `DR` | `""` | not sent |
| Invoice Reversal | FINV01 | — | — | `I` | `DR` | `"X"` | not sent |
| Debit Note | FDCN01 | `DN` | `rate_revision` | `I` | `DR` | `""` | yes |
| Debit Note | FDCN01 | `DN` | `manual` | `I` | `DR` | `""` | yes, optional |
| Credit Note | FDCN01 | `CN` | `rate_revision` | `C` | `DG` | `""` | yes |
| Credit Note | FDCN01 | `CN` | `manual` | `C` | `DG` | `""` | yes, optional |
| Credit Note | FDCN01 | `CN` | `eu_deletion` | `C` | `DG` | `""` | yes |

---

## Company Code Logic

```text
If customer has company_code set (inter-company):
    Company_Code     = customer.company_code
    Plant            = customer.company_code
    Business_Place   = customer.company_code
    Section_Code     = customer.company_code

Else:
    Company_Code     = sap_api_config.company_code
    Plant / Business_Place / Section_Code:
        1. Line-level value (if set)
        2. SAP config default (plant_code / business_place / section_code)
        3. Company code fallback
```

Customer types checked: `Customer`, `Agent`, `ImporterExporter`.

---

## GST Logic

| Scenario | CGST_GL | CGST_AMT | SGST_GL | SGST_AMT | IGST_GL | IGST_AMT |
|----------|---------|----------|---------|----------|---------|----------|
| Intra-state | populated | populated | populated | populated | `""` | `""` |
| Inter-state | `""` | `""` | `""` | `""` | populated | populated |

SAP does not calculate GST. Portbird sends pre-calculated tax values and their corresponding GL accounts from the service master.

GST GL accounts are configured per service in **FSTM01**:
- `sap_cgst_gl` — CGST liability GL
- `sap_sgst_gl` — SGST liability GL
- `sap_igst_gl` — IGST liability GL

---

## TDS / TCS Logic

TDS and TCS GL accounts follow a 3-level precedence because different services fall under different Income Tax sections (194C, 194J, 194I, etc.), each with its own GL account.

| TDS Section | Nature | Example GL |
|---|---|---|
| 194C | Contractor / Work contracts | `2400001000` |
| 194J | Professional / Technical fees | `2400002000` |
| 194I | Rent | `2400003000` |

**Precedence:**
1. Line-level override (if set directly on the invoice line)
2. Service master `sap_tds_gl` / `sap_tcs_gl` ← primary configuration point
3. SAP config `tds_gl` / `tcs_gl` ← fallback default

---

## SERVICE_SALE Flag

Sent at **header level** (not per item). Controls how SAP classifies the document for accounting.

| Value | Label | Meaning | Set when |
|---|---|---|---|
| `"S"` | **Serv** | Service transaction | `Is Sale = No` on service master (default) |
| `"A"` | **Sale** | Sale/goods transaction | `Is Sale = Yes` on service master |

> Note: `"S"` stands for **Serv** (Service), not "Sale". Use `"A"` for Sale to avoid ambiguity.

Derived from the first line's service master `service_sale_flag`. Configured per service in **FSTM01** via the **Is Sale** checkbox.

---

## IRN / e-Invoice Flow

```text
1. Post to SAP
2. SAP PI forwards to Cygnet for IRN generation
3. IRN is not returned in the initial POST response
4. Finance clicks Fetch IRN later
5. Portbird calls GET /RESTAdapter/DynaportInvoice/IRN?Reference={doc_number}
6. SAP returns IRN_No, Ack_No, IRN_Date
7. Portbird saves gst_irn, gst_ack_number, gst_ack_date
8. Re-posted payloads include IRN fields if already available
```

---

## Error Handling

| Condition | Behavior |
|-----------|----------|
| No active SAP config | Return error and log to `integration_logs` |
| OAuth token failure | Return error and log |
| HTTP 4xx/5xx from SAP | Log full request and response; set document to SAP failed |
| Network timeout | Return error and log |
| Successful post | Save `sap_document_number` and update document status |

All attempts are written to `integration_logs` with:
- `integration_type`: `SAP` or `SAP_IRN_FETCH`
- `source_type`: `Invoice`, `CreditNote`, `DebitNote`
- `source_id`, `source_reference`, `request_body`, `response_body`, `status`, `created_by`

---

## Source Field Mapping

### FINV01 Invoice (`invoice_header`)

| Payload Field | DB Column |
|---|---|
| `Reference_Text` | `invoice_number` |
| `Document_Date` | `invoice_date` |
| `Customer_Code` | `customer_gl_code` |
| `Invoice_Amount` | `total_amount` |
| `IRN_No` | `irn` |
| `Ack_No` | `ack_number` |
| `IRN_Date` | `irn_date` |
| `SERVICE_SALE` | service master `service_sale_flag` of first line → `"S"` (Serv) or `"A"` (Sale) |

### FDCN01 DN / CN (`fdcn_header`)

| Payload Field | DB Column |
|---|---|
| `Reference_Text` | `doc_number` |
| `Document_Date` | `doc_date` |
| `Customer_Code` | `customer_gl_code` |
| `Invoice_Amount` | `total_amount` |
| `Original_Invoice_No` | `original_invoice_number` |
| `IRN_No` | `gst_irn` |
| `Ack_No` | `gst_ack_number` |
| `IRN_Date` | `gst_ack_date` |
| `SERVICE_SALE` | service master `service_sale_flag` of first line → `"S"` (Serv) or `"A"` (Sale) |

### Service Line (`invoice_lines` / `fdcn_lines`)

| Payload Field | DB Column / Source |
|---|---|
| `Service_Code` | `service_code` or `gl_code` |
| `Amount` | `line_amount` |
| `CGST_AMT` | `cgst_amount` |
| `SGST_AMT` | `sgst_amount` |
| `IGST_AMT` | `igst_amount` |
| `Tax_Amount` | `cgst_amount + sgst_amount + igst_amount` |
| `Text` | `service_name` (max 25 chars) |
| `UOM` | `invoice_lines.uom` ← `bill_lines.uom` ← `lueu_lines.quantity_uom` (EU01 line item) |
| `Unit_Price` | `invoice_lines.rate` ← `bill_lines.rate` ← customer agreement rate (fetched in FIN01 billing) |
| `Quantity` | `invoice_lines.quantity` ← `bill_lines.quantity` ← `lueu_lines.quantity` (EU01 line item) |
| `HSN_SAC` | `invoice_lines.sac_code` |
| `IGST_GL` | service master `sap_igst_gl` |
| `CGST_GL` | service master `sap_cgst_gl` |
| `SGST_GL` | service master `sap_sgst_gl` |
| `TDS_GL` | service master `sap_tds_gl` → config `tds_gl` |
| `TCS_GL` | service master `sap_tcs_gl` → config `tcs_gl` |
| `Round_off_GL` | config `round_off_gl` |
| `TDS_Amount` | `tds_amount` |
| `TCS_Amount` | `tcs_amount` |
| `Rounding_off` | `rounding_off` |
| `Tax_Code` | `sap_tax_code` → config `tax_code` |
| `Profit_Center` | `profit_center` → config `profit_center` |
