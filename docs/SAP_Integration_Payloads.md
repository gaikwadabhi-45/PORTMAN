# SAP Integration - Payload Reference

## Overview

portbird proposed communication with SAP ECC via a SAP PI/PO REST adapter. All supported documents are posted as JSON to a single endpoint, and PI middleware transforms them into the appropriate SAP document type.

**Endpoint:** `{base_url}/RESTAdapter/DynaportInvoice`  
**Method:** `POST`  
**Auth:** OAuth2 client credentials (`/oauth2/api/v1/generateToken`)  
**IRN Fetch:** `GET {base_url}/RESTAdapter/DynaportInvoice/IRN?Reference={doc_number}`

All payloads share the same outer envelope:

```json
{
  "Record": { "...": "..." }
}
```

---

## SAP Document Types

| SAP Type | Interface Message | Used For |
|----------|-------------------|----------|
| `Y1` | `MT_INV_portbirdtoECC_Req` | Invoice, Debit Note, Invoice Reversal |
| `Y2` | `MT_CN_portbirdtoECC_Req` | Credit Note (FDCN01 module) |

`Debit Note` uses `Y1`, the same document type as invoices, because it increases the customer's payable. Credit notes use `Y2`.

---

## Common Field Reference

### Record (Header) Fields

| Field | Type | Format | Notes |
|-------|------|--------|-------|
| `Company_Code` | string | `"5130"` | From SAP config; overridden by customer's `company_code` if set |
| `Document_Date` | string | `DD.MM.YYYY` | Date of the document |
| `Posting_Date` | string | `DD.MM.YYYY` | Same as `Document_Date` |
| `Document_Type` | string | `"Y1"` / `"Y2"` | See document type table above |
| `Reference_Text` | string | max 16 chars | Invoice or DN/CN number |
| `Doc_Header_Text` | string | max 25 chars | Same as `Reference_Text`; reversal prefixes with `"REV "` |
| `Currency` | string | `"INR"` | Always INR |
| `Customer_Code` | string | GL code | Customer SAP GL code from `customer_gl_code` |
| `Payment_Term` | string | | From active SAP config |
| `Baseline_Date` | string | `DD.MM.YYYY` | Same as `Document_Date` |
| `Invoice_Amount` | string | `"118000.00"` | Total including GST, minus TDS, plus TCS |
| `IRN_No` | string | | e-Invoice IRN, empty until fetched from SAP |
| `Ack_No` | string | | GST acknowledgement number |
| `IRN_Date` | string | `DD.MM.YYYY` | IRN acknowledgement date |
| `Nature_of_transaction` | string | `"B2B"` / `"B2C"` | `B2B` if customer has GSTIN, else `B2C` |
| `Cancellation_Flag` | string | `""` / `"X"` | `"X"` only for invoice reversals |
| `Original_Invoice_No` | string | | Present on FDCN01 debit and credit notes |
| `TDS_Amount` | string | `"500.00"` / `""` | Empty string if zero |
| `TCS_Amount` | string | `"100.00"` / `""` | Empty string if zero |
| `Item` | array | | One entry per service line |

### Item Fields

| Field | Type | Notes |
|-------|------|-------|
| `Service_Code` | string | `service_code` or `gl_code` from the line |
| `Amount` | string | Taxable base amount, always populated; `"0.00"` if zero |
| `CGST_AMT` | string | Empty string if zero |
| `SGST_AMT` | string | Empty string if zero |
| `IGST_AMT` | string | Empty string if zero |
| `Text` | string | Service name, max 50 chars |
| `Plant` | string | From line -> SAP config `plant_code` -> company code; default plant code for our setup is `"5130"` |
| `Business_Place` | string | From line -> SAP config `business_place` -> company code |
| `Section_Code` | string | From line -> SAP config `section_code` -> company code |
| `Tax_Code` | string | From line `sap_tax_code` -> SAP config `tax_code` |
| `Profit_Center` | string | From line `profit_center` -> SAP config `profit_center` |
| `HSN_SAC` | string | SAC/HSN code |
| `TDS_Amount` | string | Line-level TDS, empty if zero |
| `TCS_Amount` | string | Line-level TCS, empty if zero |
| `Rounding_off` | string | Rounding adjustment, empty if zero |

### Invoice_Amount Calculation

```text
Invoice_Amount = total_amount (from header)
               - TDS
               + TCS
```

If `total_amount` is zero or null, it is computed by summing `line_amount + cgst + sgst + igst` across all lines before applying TDS/TCS.

---

## Scenario 1 - Invoice (FINV01)

**Trigger:** User clicks "Post to SAP" on an approved invoice.  
**Builder:** `sap_builder.build_invoice_payload(invoice_header, invoice_lines)`  
**Source fields:** `invoice_header` + `invoice_lines`

```json
{
  "Record": {
    "Company_Code": "5130",
    "Document_Date": "24.04.2025",
    "Posting_Date": "24.04.2025",
    "Document_Type": "Y1",
    "Reference_Text": "dppl/25-26/0001",
    "Doc_Header_Text": "dppl/25-26/0001",
    "Currency": "INR",
    "Customer_Code": "5100001",
    "Payment_Term": "Z030",
    "Baseline_Date": "24.04.2025",
    "Invoice_Amount": "118000.00",
    "IRN_No": "",
    "Ack_No": "",
    "IRN_Date": "",
    "Nature_of_transaction": "B2B",
    "Cancellation_Flag": "",
    "TDS_Amount": "",
    "TCS_Amount": "",
    "Item": [
      {
        "Service_Code": "OT0051",
        "CGST_AMT": "9000.00",
        "SGST_AMT": "9000.00",
        "IGST_AMT": "",
        "Amount": "100000.00",
        "Text": "MOORING CHARGES",
        "Plant": "5130",
        "Business_Place": "5130",
        "Section_Code": "5130",
        "Tax_Code": "50",
        "Profit_Center": "500000",
        "HSN_SAC": "996759",
        "TDS_Amount": "",
        "TCS_Amount": "",
        "Rounding_off": ""
      }
    ]
  }
}
```

**Status after success:** `invoice_status = 'Posted to SAP'`, `sap_document_number` saved.

---

## Scenario 2 - Invoice Reversal (FINV01 cancellation)

**Trigger:** User cancels an already SAP-posted invoice.  
**Builder:** `sap_builder.build_invoice_reversal_payload(invoice_header, invoice_lines)`

| Field | Invoice | Reversal |
|-------|---------|----------|
| `Document_Type` | `Y1` | `Y1` |
| `Reference_Text` | `dppl/25-26/0001` | SAP document number or invoice number |
| `Doc_Header_Text` | `dppl/25-26/0001` | `REV 1800000123` |
| `Cancellation_Flag` | `""` | `"X"` |

```json
{
  "Record": {
    "Document_Type": "Y1",
    "Reference_Text": "1800000123",
    "Doc_Header_Text": "REV 1800000123",
    "Cancellation_Flag": "X",
    "Invoice_Amount": "118000.00"
  }
}
```

SAP processes `Cancellation_Flag = X` and posts a reversal entry.

---

## Scenario 3 - Debit Note (FDCN01)

**Trigger:** User clicks "Post to SAP" on an approved debit note from FDCN01.  
**Builder:** `sap_builder.build_fdcn_payload(fdcn_header, fdcn_lines)`  
**Source fields:** `fdcn_header` + `fdcn_lines`  
**doc_type:** `DN`

Debit notes use `Y1`.

**Creation types that produce a DN:**
- `rate_revision`
- `manual`

```json
{
  "Record": {
    "Company_Code": "5130",
    "Document_Date": "30.03.2026",
    "Posting_Date": "30.03.2026",
    "Document_Type": "Y1",
    "Reference_Text": "DN/25-26/0001",
    "Doc_Header_Text": "DN/25-26/0001",
    "Currency": "INR",
    "Customer_Code": "5100001",
    "Payment_Term": "Z030",
    "Baseline_Date": "30.03.2026",
    "Invoice_Amount": "11800.00",
    "IRN_No": "",
    "Ack_No": "",
    "IRN_Date": "",
    "Nature_of_transaction": "B2B",
    "Cancellation_Flag": "",
    "Original_Invoice_No": "dppl/25-26/0001",
    "TDS_Amount": "",
    "TCS_Amount": "",
    "Item": [
      {
        "Service_Code": "OT0051",
        "CGST_AMT": "450.00",
        "SGST_AMT": "450.00",
        "IGST_AMT": "",
        "Amount": "5000.00",
        "Text": "MOORING CHARGES",
        "Plant": "5130",
        "Business_Place": "5130",
        "Section_Code": "5130",
        "Tax_Code": "50",
        "Profit_Center": "500000",
        "HSN_SAC": "996759",
        "TDS_Amount": "",
        "TCS_Amount": "",
        "Rounding_off": ""
      }
    ]
  }
}
```

---

## Scenario 4 - Credit Note (FDCN01)

**Trigger:** User clicks "Post to SAP" on an approved credit note from FDCN01.  
**Builder:** `sap_builder.build_fdcn_payload(fdcn_header, fdcn_lines)`  
**Source fields:** `fdcn_header` + `fdcn_lines`  
**doc_type:** `CN`

Credit notes use `Y2`.

**Creation types that produce a CN:**

| `creation_type` | Meaning |
|---|---|
| `rate_revision` | Revised rate is lower than original rate |
| `manual` | User manually created the credit note |
| `eu_deletion` | Auto-created after invoiced EU line deletion |

```json
{
  "Record": {
    "Company_Code": "5130",
    "Document_Date": "30.03.2026",
    "Posting_Date": "30.03.2026",
    "Document_Type": "Y2",
    "Reference_Text": "CN/25-26/0001",
    "Doc_Header_Text": "CN/25-26/0001",
    "Currency": "INR",
    "Customer_Code": "5100001",
    "Payment_Term": "Z030",
    "Baseline_Date": "30.03.2026",
    "Invoice_Amount": "11800.00",
    "IRN_No": "",
    "Ack_No": "",
    "IRN_Date": "",
    "Nature_of_transaction": "B2B",
    "Cancellation_Flag": "",
    "Original_Invoice_No": "dppl/25-26/0001",
    "TDS_Amount": "",
    "TCS_Amount": "",
    "Item": [
      {
        "Service_Code": "OT0051",
        "CGST_AMT": "450.00",
        "SGST_AMT": "450.00",
        "IGST_AMT": "",
        "Amount": "5000.00",
        "Text": "MOORING CHARGES",
        "Plant": "5130",
        "Business_Place": "5130",
        "Section_Code": "5130",
        "Tax_Code": "50",
        "Profit_Center": "500000",
        "HSN_SAC": "996759",
        "TDS_Amount": "",
        "TCS_Amount": "",
        "Rounding_off": ""
      }
    ]
  }
}
```

---

## Scenario Matrix

| Document | Module | `doc_type` | `creation_type` | SAP `Document_Type` | `Cancellation_Flag` | `Original_Invoice_No` |
|----------|--------|-----------|-----------------|---------------------|---------------------|-----------------------|
| Invoice | FINV01 | - | - | `Y1` | `""` | not sent |
| Invoice Reversal | FINV01 | - | - | `Y1` | `"X"` | not sent |
| Debit Note | FDCN01 | `DN` | `rate_revision` | `Y1` | `""` | yes |
| Debit Note | FDCN01 | `DN` | `manual` | `Y1` | `""` | yes, optional |
| Credit Note | FDCN01 | `CN` | `rate_revision` | `Y2` | `""` | yes |
| Credit Note | FDCN01 | `CN` | `manual` | `Y2` | `""` | yes, optional |
| Credit Note | FDCN01 | `CN` | `eu_deletion` | `Y2` | `""` | yes |

---

## Company Code Logic

```text
If customer has company_code set:
    Company_Code = customer.company_code
    Plant = customer.company_code
    Business_Place = customer.company_code
    Section_Code = customer.company_code

Else:
    Company_Code = sap_api_config.company_code
    Plant / Business_Place / Section_Code:
        1. Line-level value
        2. SAP config default
        3. Company code fallback
```

Customer types checked: `Customer`, `Agent`, `ImporterExporter`.

---

## GST Logic

| Scenario | CGST_AMT | SGST_AMT | IGST_AMT |
|----------|----------|----------|----------|
| Intra-state | populated | populated | `""` |
| Inter-state | `""` | `""` | populated |

SAP does not calculate GST. portbird sends the pre-calculated tax values from the line rows.

---

## IRN / e-Invoice Flow

```text
1. Post to SAP
2. SAP PI forwards to Cygnet for IRN generation
3. IRN is not returned in the initial POST response
4. Finance clicks Fetch IRN later
5. portbird calls GET /RESTAdapter/DynaportInvoice/IRN?Reference={doc_number}
6. SAP returns IRN_No, Ack_No, IRN_Date
7. portbird saves gst_irn, gst_ack_number, gst_ack_date
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
- `source_id`
- `source_reference`
- `request_body`
- `response_body`
- `status`
- `created_by`

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
