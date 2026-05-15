# SAP → PORTBIRD Callback API

This is the endpoint SAP calls **back** to PORTBIRD after it processes an
invoice / debit-note / credit-note that we previously pushed into SAP's
staging table. Think of it as SAP saying:

> "Here's what happened with the document you sent me — here's its SAP doc
> number, the IRN from the IRP, and whether it posted cleanly."

PORTBIRD uses that confirmation to:

- Stamp the SAP document number on our invoice / DN / CN row
- Mark the invoice as Posted to GST (if IRN came along)
- **Start the 24-hour cancellation window** (the Cancel button only appears
  after this callback arrives)

---

## 1. Authorization (do this first)

Every call must carry a bearer token in the `Authorization` header.

```http
POST /api/sap/callback
Authorization: Bearer <your-token-here>
Content-Type: application/json
```

### How to get a token

1. A PORTBIRD admin generates it from the Admin → SAP Inbound Tokens screen.
2. The full token is shown **only once at creation time** — copy it then.
3. Tokens can be revoked or reactivated by admins. Revoked tokens stop
   working immediately.

### What happens if auth is wrong

```json
{ "error": "Unauthorized" }
```

HTTP **401** is returned when:

- The `Authorization` header is missing
- The scheme isn't `Bearer`
- The token doesn't match any active row
- The token was revoked

Nothing in the request body is processed — fix the token and retry.

---

## 2. Endpoint

| | |
|---|---|
| **URL** | `https://dpplportbird.jsw.in/api/sap/callback` |
| **Method** | `POST` |
| **Content-Type** | `application/json` |
| **Auth** | `Bearer <token>` (see above) |

One call can carry **many records** in a single payload. Each record is
applied independently — failures on one don't stop the others.

---

## 3. Request structure

The body is a JSON object with a single `Record` array. Each item in
the array is one document SAP is reporting back on.

```json
{
  "Record": [
    {
      "Reference":       "DPPL/26-27/1",
      "Document_Number": "5100001234",
      "Posting_Date":    "06.05.2026",
      "Company_Code":    "5130",
      "Message":         "Posted successfully",
      "IRN_No":          "a1b2c3d4e5f6...64chars",
      "Ack_No":          "112226053100001",
      "IRN_Date":        "2026-05-06",
      "QR_Code":         "<long string>"
    },
    {
      "Reference":       "CN/26-27/0001",
      "Document_Number": "5100002345",
      "Posting_Date":    "",
      "Company_Code":    "",
      "Message":         "",
      "IRN_No":          "",
      "Ack_No":          "",
      "IRN_Date":        "",
      "QR_Code":         ""
    }
  ]
}
```

### Field reference

| Field | Required? | Length | What it means |
|---|---|---|---|
| `Reference` | **Yes** | ≤ 50 | The PMS document number we originally sent in the `Reference` field of the outbound payload. PORTBIRD uses this to find the row to update. For invoices/reversals it's the `invoice_number`; for DN/CN it's the `doc_number`. |
| `Document_Number` | Yes (on success) | ≤ 50 | The SAP-assigned document number after posting. Stored on the row. |
| `Posting_Date` | Optional | 10 chars | SAP posting date. Accepts `DD.MM.YYYY` or `YYYY-MM-DD`. **This is what anchors the 24h cancel window** — if blank, server time at callback receipt is used. |
| `Company_Code` | Optional | ≤ 10 | SAP company code (e.g. `5130`). Stored on the row. |
| `Message` | Optional | ≤ 500 | Free text. If it contains `Error`, `Invalid`, or `Fail` (case-insensitive), the record is treated as a **SAP-side failure** and the invoice is marked `SAP Failed` instead of posted. |
| `IRN_No` | Optional | ≤ 64 | IRN from the GST IRP. Presence flips the invoice status to `Posted to GST`. |
| `Ack_No` | Optional | ≤ 20 | IRP acknowledgement number. |
| `IRN_Date` | Optional | 10 chars | IRN / acknowledgement date in `YYYY-MM-DD`. |
| `QR_Code` | Optional | TEXT | QR string from the IRP. |

### Sending only what changed

Empty strings are treated as "no change" — the existing value on the row
is kept. So you can send a follow-up callback that only carries IRN info
without re-sending `Document_Number` or `Posting_Date`.

---

## 4. Response structure

PORTBIRD returns HTTP **200** with a per-record result array. The order
matches the input order.

```json
{
  "Record": [
    { "Reference": "DPPL/26-27/1",  "Status": "S", "Message": "Updated" },
    { "Reference": "CN/26-27/0001", "Status": "E", "Message": "Reference not found" }
  ]
}
```

### Status codes

| `Status` | Meaning |
|---|---|
| `S` | Record applied successfully (or accepted as a no-op duplicate). |
| `E` | Record rejected — see `Message` for the reason. |

### Common `Message` values

| Message | What it means |
|---|---|
| `Updated` | Row updated successfully. |
| `Updated (IRN truncated to 64)` | Same, but the IRN was longer than 64 chars and we trimmed it. |
| `Updated (Ack truncated to 20)` | Same, with Ack number trimmed. |
| `Already recorded` | A row with this Reference already has this IRN — no-op, safe to retry. |
| `Reversal recorded` | Invoice was already posted; the new `Document_Number` differs and no IRN was supplied, so we treated this as a reversal and marked the invoice Cancelled. |
| `Stored SAP error: ...` | `Message` field contained Error/Invalid/Fail — we logged it and marked the row `SAP Failed`. |
| `Reference not found` | No invoice / DN / CN in PORTBIRD matches this `Reference`. |
| `IRN mismatch with existing record` | An IRN is already on this row and yours differs — refusing to overwrite. Contact PORTBIRD admin. |
| `Missing Reference` | The `Reference` field was blank. |

---

## 5. Behaviour rules SAP teams should know

1. **Idempotent on IRN.** If you re-send a callback with the same `Reference`
   and the same `IRN_No`, you'll get `"Already recorded"` and no changes
   are made. Safe to retry on network blips.
2. **IRN is locked once set.** If a row already has an IRN and a new call
   tries to set a different one, the call is rejected with
   `"IRN mismatch with existing record"`.
3. **Reversal heuristic.** If an invoice already has a SAP doc number and a
   callback arrives with a *different* `Document_Number` and **no** IRN, it's
   interpreted as a reversal — the invoice is marked Cancelled.
4. **Error keywords.** Any `Message` containing `Error`, `Invalid`, or
   `Fail` (case-insensitive) flips the row to `SAP Failed` and the rest of
   the success fields are not applied. The full message is saved on the row.
5. **All calls are logged.** Every authenticated request is written to
   `integration_logs` (request body, response body, IP, token label,
   timestamp). Auth failures are not logged.

---

## 6. Quick test

```bash
curl -X POST https://dpplportbird.jsw.in/api/sap/callback \
  -H "Authorization: Bearer <your-token>" \
  -H "Content-Type: application/json" \
  -d '{
    "Record": [{
      "Reference": "DPPL/26-27/1",
      "Document_Number": "5100001234",
      "Posting_Date": "06.05.2026",
      "Company_Code": "5130",
      "Message": "Posted successfully"
    }]
  }'
```

Expected response:

```json
{ "Record": [{ "Reference": "DPPL/26-27/1", "Status": "S", "Message": "Updated" }] }
```

If you see `401 Unauthorized`, the token is the problem — not the body.
If you see `Status: "E"` with `"Reference not found"`, double-check the
PMS document number matches what PORTBIRD originally sent in the outbound
`Reference` field.
