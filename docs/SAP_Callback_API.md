# PORTBIRD DPPL API document

## API – SAP Callback (Inbound)

| | |
|---|---|
| **Method Name** | `/api/sap/callback` |
| **Method Type** | POST |
| **URL** | `https://dpplportbird.jsw.in/api/sap/callback` |
| **Content-Type** | application/json |
| **Authorization** | `Bearer <token>` (issued by PORTBIRD DPPL admin)  |
| **Remarks** | One call may contain multiple records. Per-record status returned in `Record[]`. |

### Input

```json
{
  "Record": [
    {
      "Reference": "DPPL/26-27/1",
      "Document_Number": "5100001234",
      "Posting_Date": "06.05.2026",
      "Company_Code": "5130",
      "Message": "Posted successfully",
      "IRN_No": "a1b2c3d4e5f6...64chars",
      "Ack_No": "112226053100001",
      "IRN_Date": "2026-05-06",
      "QR_Code": "<long string>"
    },
    {
      "Reference": "CN/26-27/0001",
      "Document_Number": "5100002345",
      "Posting_Date": "",
      "Company_Code": "",
      "Message": "",
      "IRN_No": "",
      "Ack_No": "",
      "IRN_Date": "",
      "QR_Code": ""
    }
  ]
}
```

### Field reference

| Field | Length | Notes |
|---|---|---|
| `Reference` | ≤ 50 | PMS doc number echoed from original push (`invoice_number` for invoices/reversals, `doc_number` for DN/CN). |
| `Document_Number` | ≤ 50 | SAP-assigned doc number after posting. |
| `Posting_Date` | 10 (`DD.MM.YYYY` or `YYYY-MM-DD`) | SAP posting date. Stored on the row. |
| `Company_Code` | ≤ 10 | SAP company code (e.g. `5130`). |
| `Message` | ≤ 500 | Free text. Containing `Error` / `Invalid` / `Fail` marks the record as a SAP-side failure. |
| `IRN_No` | ≤ 64 | IRN from IRP. |
| `Ack_No` | ≤ 20 | Acknowledgement number. |
| `IRN_Date` | 10 (`YYYY-MM-DD`) | Ack / IRN date. |
| `QR_Code` | TEXT | QR string. |

### Output

```json
{
  "Record": [
    { "Reference": "DPPL/26-27/1", "Status": "S", "Message": "Updated" },
    { "Reference": "CN/26-27/0001", "Status": "E", "Message": "Reference not found" }
  ]
}
```

| Status | Meaning |
|---|---|
| `S` | Record applied successfully (or accepted as no-op on duplicate). |
| `E` | Record rejected — see `Message`. |

### Auth failure

```json
{ "error": "Unauthorized" }
```
HTTP 401 — token missing, invalid, or revoked.
