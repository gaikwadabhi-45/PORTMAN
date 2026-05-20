# SAP Inbound Callback API + Admin Playground — Design

> **Date:** 2026-05-05
> **Status:** Approved, ready for implementation
> **Touches:** `app.py`, new `sap_inbound.py`, `modules/ADMIN/views.py`, `templates/admin.html`, optional spot-check on print templates

---

## 1. Goal

After PORTMAN pushes a Y1 staging payload to SAP (existing `sap_client.post_invoice_to_sap`), the SAP team processes the document asynchronously and posts the result back. This design adds:

1. **Inbound REST endpoint** SAP hits to write back: `Document_Number`, `Message`, `IRN_No` (≤64), `Ack_No` (≤20), `IRN_Date` (10), `QR_Code` (TEXT).
2. **Bearer-token auth** for SAP, managed via a new admin tab.
3. **Admin Playground** with outbound test (re-send existing invoice payloads) and inbound viewer (received-callback log).
4. Status transitions on the invoice / FDCN row when callback arrives.

Out of scope: mail notifications on callback, retry queue, GSP direct integration.

---

## 2. Data Flow

```
SAP processes Y1 push
        ↓
SAP POSTs /api/sap/callback (Bearer token)
        ↓
1. Verify token → sap_inbound_tokens (active, non-revoked)
2. Validate JSON body
3. For each record in body.Record[]:
     a. Match Reference → invoice_header.invoice_number (UPDATE)
     b. else match → fdcn_header.doc_number (UPDATE)
     c. else → per-record error
   Truncate IRN(64), Ack(20). QR is TEXT.
   Idempotent: if existing IRN == incoming IRN, skip.
4. Insert integration_logs row (integration_type='SAP_INBOUND')
5. Return ack { Record:[{Reference, Status:S|E, Message}, ...] }
```

URL: `POST /api/sap/callback` (registered directly in `app.py`, no module — SAP-facing, not user UI).

---

## 3. Schema

### New table — `sap_inbound_tokens`

```sql
CREATE TABLE sap_inbound_tokens (
    id              SERIAL PRIMARY KEY,
    label           VARCHAR(100) NOT NULL,
    token           VARCHAR(64)  NOT NULL UNIQUE,
    is_active       SMALLINT     DEFAULT 1,
    created_by      VARCHAR(100),
    created_date    TIMESTAMP,
    last_used_at    TIMESTAMP,
    last_used_ip    VARCHAR(45),
    revoked_at      TIMESTAMP,
    revoked_by      VARCHAR(100)
);
```

Token = `secrets.token_hex(32)` (256-bit, 64-char hex).

### Existing columns reused (no migration)

`invoice_header` / `fdcn_header` already have:
`sap_document_number`, `sap_posting_date`, `gst_irn`, `gst_ack_number`, `gst_ack_date`, `gst_qr_code`, plus status / error columns.

`integration_logs` already exists (used by outbound). Inbound rows will be `integration_type='SAP_INBOUND'`.

### Status transitions

| Before | Callback | After |
|--------|----------|-------|
| `Posted to SAP` | success + IRN populated | `Posted to GST` |
| `Posted to SAP` | Message indicates SAP error | `SAP Failed` (with error stored) |
| `Posted to GST` (already populated) | duplicate w/ same IRN | unchanged (idempotent) |
| any (with sap_document_number set) | reversal callback (different doc no.) | `Cancelled`, reversal SAP doc# appended to remarks |

---

## 4. Auth

- Header: `Authorization: Bearer <token>`
- 401 returned for missing / invalid / revoked tokens. Body **not** logged for unauthenticated requests (avoid storing hostile payloads).
- On success, update `last_used_at` + `last_used_ip` on the token row.

---

## 5. Admin UI

### Tab A — "SAP Tokens"

Manage callback bearer tokens. Admin-only.

| Field | Notes |
|-------|-------|
| Label | "SAP Prod", "SAP QAS" |
| Token | masked `abc1...xyz9` + Copy button |
| Active | toggle |
| Last Used | timestamp |
| Last IP | string |
| Created By/Date | audit |
| Actions | Revoke / Reactivate |

**Generate flow**: prompt for label → server creates `secrets.token_hex(32)` → returns full token **once** in a "Copy now — won't be shown again" modal.

**Revoke** = soft (sets `revoked_at`, `is_active=0`). No hard delete.

#### APIs (admin-only)
- `GET  /api/sap-tokens` — list (masked)
- `POST /api/sap-tokens/generate` — `{label}` → `{token: <full>}`
- `POST /api/sap-tokens/revoke` — `{id}`
- `POST /api/sap-tokens/reactivate` — `{id}`

### Tab B — "SAP Playground"

#### B1 — Outbound (push test)
- Dropdown: pick existing invoice / FDCN → loads via existing builders (`build_invoice_payload`, `build_fdcn_payload`, `build_invoice_reversal_payload`).
- Pre-filled JSON in editable `<textarea>` + Format button.
- Buttons: `Send to SAP` (uses real active config), `Build Reversal Payload`, `Reset to Default`.
- Result panel: HTTP status + response JSON + log_id link.

#### B2 — Inbound viewer
- Read-only Tabulator table from `integration_logs WHERE integration_type='SAP_INBOUND'`.
- Columns: Logged At | Reference (from request body) | Status (S/E) | HTTP | Token Label | Source IP | Message.
- Click row → modal with full request + response.
- Filters: date range, reference, status.

---

## 6. Endpoint contract

### Request (SAP → us)
```json
POST /api/sap/callback
Authorization: Bearer <token>
Content-Type: application/json

{
  "Record": [
    {
      "Reference": "DPPL/26-27/1",
      "Document_Number": "5100001234",
      "Message": "Posted successfully",
      "IRN_No": "abc...64chars",
      "Ack_No": "112226053100001",
      "IRN_Date": "2026-05-06",
      "QR_Code": "<long-base64-or-jwt-string>"
    }
  ]
}
```

### Response (us → SAP)
```json
{
  "Record": [
    {"Reference":"DPPL/26-27/1", "Status":"S", "Message":"Updated"}
  ]
}
```

HTTP 200 always when authenticated, with per-record S/E status inside.
HTTP 401 only for auth failure.
HTTP 400 only for malformed JSON body.

---

## 7. Edge cases

| Case | Behaviour |
|------|-----------|
| Reference not found | per-record `Status:'E', Message:'Reference not found'` |
| Same IRN already on row | per-record `Status:'S', Message:'Already recorded'` (no DB write) |
| Different IRN for same Reference | per-record `Status:'E', Message:'IRN mismatch with existing record'` (no DB write) |
| Reversal: doc_number != existing sap_document_number | append to remarks, set status `Cancelled` |
| IRN > 64 chars | truncate to 64, note in returned Message |
| Ack > 20 chars | truncate to 20, note in returned Message |
| QR length | TEXT column, no truncation |
| Token missing | 401, no log row |
| Token revoked | 401, no log row |
| Body not JSON | 400 `{"error":"Bad JSON"}` |

---

## 8. Print template impact

[fdcn01_print.html](../../modules/FDCN01/fdcn01_print.html) already renders IRN/Ack/QR. [finv01_invoice_print.html](../../modules/FINV01/finv01_invoice_print.html) — spot-check during impl, only edit if a field is missing. No design change.

---

## 9. File touchlist

| File | Change |
|------|--------|
| `database.py` (or migration) | Auto-create `sap_inbound_tokens` table on startup (matches existing pattern) |
| **NEW** `sap_inbound.py` | Token verification + callback apply logic |
| `app.py` | Register `/api/sap/callback` route |
| `modules/ADMIN/views.py` | Token CRUD endpoints + playground outbound/inbound endpoints |
| `templates/admin.html` | Two new tabs: "SAP Tokens", "SAP Playground" |
| `modules/FINV01/finv01_invoice_print.html` | Spot-check only |

---

## 10. Verification checklist

- [ ] Generate token in admin → modal shows full token once, table shows masked
- [ ] Token list refreshes; Last Used updates after first call
- [ ] Revoke → status flips, callbacks return 401
- [ ] Outbound playground: pick invoice → JSON pre-fills → Send → response shows in panel
- [ ] curl `POST /api/sap/callback` with valid token + sample body → invoice row updates IRN/Ack/QR/sap_doc, status → Posted to GST
- [ ] Same call again with same IRN → idempotent (no double update, returns "Already recorded")
- [ ] Inbound viewer shows the log row with token label + IP
- [ ] Print invoice → IRN, Ack, QR visible in correct positions on both FINV01 and FDCN01 prints
- [ ] FIN01 / FINV01 / FDCN01 existing flows unbroken (smoke)
