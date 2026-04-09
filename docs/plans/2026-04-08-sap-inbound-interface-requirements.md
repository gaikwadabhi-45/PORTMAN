# SAP Inbound Interface Requirements
## Portbird → SAP FI: Customer Invoice, Credit Memo & Reversal

**Document Date:** 2026-04-08
**Prepared By:** Portbird Team
**Version:** 1.0
**Status:** Draft — Shared for SAP BASIS/ABAP Team Review

---

## 1. Purpose

This document defines the requirements for the **inbound interface** from the Portbird Port Management System (PMS) to SAP FI. The interface enables Portbird to post the following financial documents into SAP:

- Customer Invoices
- Debit Notes (DN)
- Credit Notes / Credit Memos (CN)
- Reversals of Invoices
- Reversals of Credit Notes

IRN (e-Invoice) generation is handled by SAP after posting via Cygnet/NIC. The outbound interface (SAP → Portbird) for delivering IRN, Acknowledgement Number, and QR Code is to be scoped and agreed separately.

---

## 2. Integration Architecture

```
Portbird PMS
    │
    │  HTTPS POST (JSON)
    │  Bearer Token (OAuth2)
    ▼
SAP Process Integration (PI/PO)
    │
    │  Inbound Proxy
    ▼
SAP Staging Table
    │
    │  ABAP Posting Program
    ▼
SAP FI Document
    │
    │  Cygnet / NIC
    ▼
IRN + QR Code (written back to staging table)
```

**Network:** Telnet connectivity from Portbird to SAP is confirmed operational.

---

## 3. Transport & Authentication

| Parameter | Value |
|---|---|
| Protocol | HTTPS REST |
| HTTP Method | `POST` |
| Content-Type | `application/json` |
| Endpoint | `{SAP_PI_BASE_URL}/{InboundServiceName}` |
| Authentication | OAuth2 — Client Credentials grant |
| Token URL | `{SAP_PI_BASE_URL}/oauth2/api/v1/generateToken` |
| Token Grant | `grant_type=client_credentials` with `client_id` + `client_secret` |

> **Note:** The inbound service name replaces the previous `DynaportInvoice` endpoint. SAP team to confirm the new endpoint name.

**Token behaviour:** Portbird caches the Bearer token and refreshes it on expiry (`expires_in` − 60s buffer).

---

## 4. Request Payload Structure

```json
{
  "Record": {
    "Invoice_Type": "I",
    "Company_Code": "5171",
    "Invoice_Date": "08.04.2026",
    "Posting_Date": "08.04.2026",
    "Reference_Text": "INV/25-26/0001",
    "Document_Type": "DR",
    "Cancellation_Flag": "",
    "Nature_of_transaction": "B2B",
    "Service_Sale": "S",
    "Customer_Code": "CUST001",
    "Invoice_Amount": "118000.00",
    "Currency": "INR",
    "Business_Place": "5171",
    "Section_Code": "5171",
    "Payment_Term": "Z030",
    "Baseline_Date": "08.04.2026",
    "Header_Text": "INV/25-26/0001",
    "Item": [
      {
        "GL_Account": "4000100",
        "GL_Amount": "100000.00",
        "Plant": "5171",
        "Profit_Center": "PC001",
        "Text_Description": "Cargo Handling Charges",
        "Tax_Code": "G1",
        "IGST_GL": "",
        "IGST_Amount": "",
        "SGST_GL": "2400200",
        "SGST_Amount": "9000.00",
        "CGST_GL": "2400100",
        "CGST_Amount": "9000.00",
        "HSN_or_SAC_code": "996731",
        "UOM": "MT",
        "Unit_Price": "100.00",
        "Quantity": "1000",
        "TDS_GL": "",
        "TDS_Amount": "",
        "TCS_GL": "",
        "TCS_Amount": "",
        "Round_off_GL": "",
        "Round_off_Value": ""
      }
    ]
  }
}
```

---

## 5. Document Type Matrix

| Scenario | Invoice_Type | Document_Type | Cancellation_Flag | Item Array |
|---|---|---|---|---|
| Customer Invoice | `I` | `DR` | *(blank)* | Required |
| Debit Note (DN) | `I` | `DR` | *(blank)* | Required |
| Credit Note / Credit Memo | `C` | `DG` | *(blank)* | Required |
| Invoice Reversal | `I` | `DR` | `X` | **Not sent** |
| Credit Note Reversal | `C` | `DG` | `X` | **Not sent** |

> **Reversal rule:** For reversals, Portbird sends only the header fields. No `Item` array is included. SAP locates the original document via `Reference_Text` (= original SAP `Document_Number`) and reverses it entirely.

---

## 6. Header Field Specification

| Field | Description | Format / Max Length | Source / Rules |
|---|---|---|---|
| `Invoice_Type` | Document category | `I` or `C` / 3 char | `I` = Invoice, DN, Reversal of Invoice; `C` = Credit Note, Reversal of CN |
| `Company_Code` | SAP company code | String | From SAP config; overridden per customer for inter-company |
| `Invoice_Date` | Document date | `DD.MM.YYYY` | Invoice / DN / CN document date from PMS |
| `Posting_Date` | SAP posting date | `DD.MM.YYYY` | Same as Invoice_Date |
| `Reference_Text` | Unique document identifier | Max 16 char | PMS document number (Invoice / DN / CN). For reversals: original SAP Document_Number |
| `Document_Type` | SAP document type | String | `DR` for Invoice/DN; `DG` for Credit Note |
| `Cancellation_Flag` | Reversal marker | `X` or blank / 3 char | `X` for reversals only; blank for all other documents |
| `Nature_of_transaction` | GST transaction type | `B2B` or `B2C` / 5 char | `B2B` if customer has a valid GSTIN; `B2C` otherwise |
| `Service_Sale` | Revenue category | `S` or `A` / 3 char | Derived from the first line item's service master flag: `S` = Service, `A` = Sale (Asset/Goods) |
| `Customer_Code` | SAP customer GL code | Max 10 char | From customer master in PMS |
| `Invoice_Amount` | Net document value | Positive numeric / 13 char | Taxable amount + GST − TDS + TCS. Always positive; SAP derives credit/debit treatment from Invoice_Type |
| `Currency` | Transaction currency | 3 char | `INR` (default) |
| `Business_Place` | SAP business place | String | From SAP config |
| `Section_Code` | SAP section code | String | From SAP config |
| `Payment_Term` | SAP payment term code | Max 4 char | From SAP config |
| `Baseline_Date` | Payment due base date | `DD.MM.YYYY` | Same as Invoice_Date |
| `Header_Text` | SAP document header text | Max 25 char | PMS document number |

---

## 7. Line Item Field Specification

> Sent for Invoice, DN, and CN only. **Not included for reversals.**

| Field | Description | Format / Max Length | Source / Rules |
|---|---|---|---|
| `GL_Account` | SAP GL account for the service | Max 10 char | From `finance_service_types.sap_gl_account` in PMS service master |
| `GL_Amount` | Taxable line amount | Positive numeric / 13 char | Net taxable amount before GST. Always positive |
| `Plant` | SAP plant code | String | From SAP config |
| `Profit_Center` | SAP profit center | String | From service master; falls back to SAP config default |
| `Text_Description` | Line description | Max 25 char | Service name |
| `Tax_Code` | SAP tax code | String | From service master; falls back to SAP config default |
| `IGST_GL` | IGST GL account | Max 10 char | From service master. **Blank if amount is zero** (inter-state transactions only) |
| `IGST_Amount` | IGST amount | Positive numeric | Blank if zero. Applicable only for inter-state (customer GSTIN state ≠ port state) |
| `SGST_GL` | SGST GL account | Max 10 char | From service master. **Blank if amount is zero** (intra-state transactions only) |
| `SGST_Amount` | SGST amount | Positive numeric | Blank if zero. Applicable only for intra-state |
| `CGST_GL` | CGST GL account | Max 10 char | From service master. **Blank if amount is zero** (intra-state transactions only) |
| `CGST_Amount` | CGST amount | Positive numeric | Blank if zero. Applicable only for intra-state |
| `HSN_or_SAC_code` | HSN / SAC code for GST | Max 16 char | From service master |
| `UOM` | Unit of measure | String | From service master |
| `Unit_Price` | Rate per unit | Positive numeric | Blank if not applicable |
| `Quantity` | Quantity billed | Numeric | Blank if not applicable |
| `TDS_GL` | TDS GL account | String | From service master; falls back to SAP config default. Blank if no TDS |
| `TDS_Amount` | TDS deducted | Positive numeric | Blank if zero |
| `TCS_GL` | TCS GL account | String | From service master; falls back to SAP config default. Blank if no TCS |
| `TCS_Amount` | TCS collected | Positive numeric | Blank if zero |
| `Round_off_GL` | Rounding adjustment GL | String | From SAP config. Blank if no rounding |
| `Round_off_Value` | Rounding adjustment value | **±** numeric / 13 char | Can be positive or negative. Blank if zero. This is the **only field with a sign** |

---

## 8. GST Determination Rules (applied by Portbird before sending)

| Condition | CGST | SGST | IGST |
|---|---|---|---|
| Customer GSTIN state = Port GSTIN state (intra-state) | Populated | Populated | Blank |
| Customer GSTIN state ≠ Port GSTIN state (inter-state) | Blank | Blank | Populated |
| Customer has no GSTIN (`B2C`) | As per applicable rule | As per applicable rule | As per applicable rule |

- GST rates are sourced from the **GST Rate Master** in PMS (`finance_service_types` → `gst_rates`)
- Port GSTIN is configured in PMS under GSTCFG module

---

## 9. Amount Sign Convention

All amounts are sent as **positive values**. SAP derives the sign (credit/debit) from:
- `Invoice_Type` = `I` → debit to customer
- `Invoice_Type` = `C` → credit to customer
- `Cancellation_Flag` = `X` → full reversal of original document

**Exception:** `Round_off_Value` carries a `+` or `−` sign as required.

---

## 10. SAP Auto-Populated Fields (Staging Table — SAP fills these)

Portbird does **not** send these fields. They are populated by SAP after the ABAP posting program runs. Portbird will read them back once the outbound interface is agreed and built.

| Field | Description | Format / Max Length | Populated When |
|---|---|---|---|
| `Processing_Status` | Staging record state | `N`/`Y`/`E`/`R` | Auto: N=New on insert, Y=Posted, E=Error, R=Reversed |
| `Fiscal_Year` | SAP fiscal year | 4 digit | Derived from Posting_Date |
| `Fiscal_Period` | SAP fiscal period | 2 digit | Derived from Posting_Date |
| `Push_Date` | Date ABAP program ran | Date | Auto on program execution |
| `Push_Time` | Time ABAP program ran | Time | Auto on program execution |
| `Document_Number` | SAP FI document number | String | After successful posting |
| `Message` | SAP success / error message | String | After program execution |
| `IRN_Number` | e-Invoice IRN (Cygnet/NIC) | Max 64 char | After IRN generation in SAP |
| `Acknowledgement_Number` | GST acknowledgement number | Max 20 char | After IRN generation |
| `IRN_Date` | IRN generation date | `DD.MM.YYYY` / 10 char | After IRN generation |
| `QR_Code` | e-Invoice QR code data | Max 256 char | After IRN generation |

---

## 11. Synchronous HTTP Response (SAP PI → Portbird)

After Portbird POSTs the payload, SAP PI should return a synchronous HTTP response:

**Success (HTTP 200):**
```json
{
  "status": "success",
  "message": "Record accepted",
  "reference": "INV/25-26/0001"
}
```

**Error (HTTP 4xx / 5xx):**
```json
{
  "status": "error",
  "message": "<description of error>",
  "reference": "INV/25-26/0001"
}
```

> **Note:** The `Document_Number` and IRN details are **not** expected in this synchronous response. They will be delivered via the outbound proxy (to be scoped separately) once the ABAP program has processed the staging record and IRN has been generated.

---

## 12. Error Handling

| Scenario | Expected Behaviour |
|---|---|
| Duplicate `Reference_Text` | SAP PI should reject with HTTP 400 and a clear error message |
| Invalid `Customer_Code` | SAP returns error; Portbird marks invoice status as `SAP Failed` and logs the response |
| Authentication failure | Portbird refreshes token and retries once; if retry fails, logs error and alerts |
| Network timeout | Portbird logs the failure; manual retry available from FINV01 module |
| ABAP posting error (`Processing_Status = E`) | SAP populates `Message` field with error detail; delivered via outbound |

---

## 13. Outbound Interface (To Be Scoped)

The outbound interface (SAP → Portbird) will deliver the following back to PMS after the ABAP program runs and IRN is generated:

- `Document_Number`
- `Processing_Status`
- `Message`
- `IRN_Number`
- `Acknowledgement_Number`
- `IRN_Date`
- `QR_Code`

**Agreed approach:** SAP will call a Portbird REST endpoint (to be defined). Portbird will build a receiving API to ingest and store these values against the corresponding invoice / CN record.

Full specification for the outbound interface to be agreed in a separate session.

---

## 14. Open Items for SAP Team Confirmation

| # | Item | Owner |
|---|---|---|
| 1 | Confirm new inbound service endpoint name (replacing `DynaportInvoice`) | SAP BASIS |
| 2 | Confirm `Document_Type` codes: `DR` for Invoice/DN, `DG` for Credit Note | SAP ABAP |
| 3 | Confirm whether `Posting_Date` should always equal `Invoice_Date` or be independent | SAP FI |
| 4 | Confirm SAP PI base URL and OAuth2 credentials for UAT environment | SAP BASIS |
| 5 | Confirm `Tax_Code` values per service type (e.g. `G1` for 18% GST) | SAP FI / Tax |
| 6 | Confirm `Business_Place` and `Section_Code` default values | SAP FI |
| 7 | Confirm outbound endpoint timing — will SAP push IRN synchronously or async after Cygnet processes? | SAP ABAP |
| 8 | Confirm `Round_off_GL` account number | SAP FI |

---

*End of Document*
