"""
SAP Inbound Callback handler.

SAP team POSTs results back to /api/sap/callback after they process a Y1
staging push. This module:

  * verifies the bearer token against sap_inbound_tokens
  * matches the Reference field to invoice_header.invoice_number first,
    then fdcn_header.doc_number
  * writes back Document_Number / IRN / Ack / IRN_Date / QR_Code on the
    matched row and transitions status
  * logs every authenticated call to integration_logs

Only one route is exposed: app.register the function returned by
build_callback_view().
"""
import json
import secrets
from datetime import datetime
from flask import request, jsonify
from database import get_db, get_cursor


# ---------------------------------------------------------------------------
# Schema bootstrap
# ---------------------------------------------------------------------------

def ensure_token_table():
    """Create sap_inbound_tokens if missing. Called once at app startup."""
    conn = get_db()
    cur = get_cursor(conn)
    cur.execute('''
        CREATE TABLE IF NOT EXISTS sap_inbound_tokens (
            id SERIAL PRIMARY KEY,
            label VARCHAR(100) NOT NULL,
            token VARCHAR(64) NOT NULL UNIQUE,
            is_active SMALLINT DEFAULT 1,
            created_by VARCHAR(100),
            created_date TIMESTAMP,
            last_used_at TIMESTAMP,
            last_used_ip VARCHAR(45),
            revoked_at TIMESTAMP,
            revoked_by VARCHAR(100)
        )
    ''')
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# Token CRUD (used by admin endpoints)
# ---------------------------------------------------------------------------

def list_tokens():
    """Return all tokens, masked."""
    conn = get_db()
    cur = get_cursor(conn)
    cur.execute('SELECT * FROM sap_inbound_tokens ORDER BY id DESC')
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    for r in rows:
        t = r.get('token') or ''
        r['token_masked'] = (t[:4] + '...' + t[-4:]) if len(t) >= 8 else '****'
        r.pop('token', None)
    return rows


def generate_token(label, created_by=None):
    """Generate, store, and return the new full token. Call once per token — value is shown only here."""
    if not label or not label.strip():
        raise ValueError('Label required')
    token = secrets.token_hex(32)  # 64-char hex
    conn = get_db()
    cur = get_cursor(conn)
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    cur.execute('''INSERT INTO sap_inbound_tokens
        (label, token, is_active, created_by, created_date)
        VALUES (%s, %s, 1, %s, %s) RETURNING id''',
        [label.strip(), token, created_by, now])
    row_id = cur.fetchone()['id']
    conn.commit()
    conn.close()
    return {'id': row_id, 'label': label.strip(), 'token': token}


def revoke_token(token_id, revoked_by=None):
    conn = get_db()
    cur = get_cursor(conn)
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    cur.execute('''UPDATE sap_inbound_tokens
        SET is_active=0, revoked_at=%s, revoked_by=%s
        WHERE id=%s''', [now, revoked_by, token_id])
    conn.commit()
    conn.close()


def reactivate_token(token_id):
    conn = get_db()
    cur = get_cursor(conn)
    cur.execute('''UPDATE sap_inbound_tokens
        SET is_active=1, revoked_at=NULL, revoked_by=NULL
        WHERE id=%s''', [token_id])
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# Token verify + record application
# ---------------------------------------------------------------------------

def _verify_token(authorization_header):
    """Return active token row dict on success, else None. Updates last_used."""
    if not authorization_header:
        return None
    parts = authorization_header.split(None, 1)
    if len(parts) != 2 or parts[0].lower() != 'bearer':
        return None
    raw = parts[1].strip()
    if not raw:
        return None
    conn = get_db()
    cur = get_cursor(conn)
    cur.execute('''SELECT * FROM sap_inbound_tokens
        WHERE token = %s AND COALESCE(is_active, 0) = 1
          AND revoked_at IS NULL
        LIMIT 1''', [raw])
    row = cur.fetchone()
    if row:
        now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        ip = (request.headers.get('X-Forwarded-For') or request.remote_addr or '')[:45]
        cur.execute('UPDATE sap_inbound_tokens SET last_used_at=%s, last_used_ip=%s WHERE id=%s',
                    [now, ip, row['id']])
        conn.commit()
    conn.close()
    return dict(row) if row else None


def _truncate(value, length):
    if value is None:
        return None
    s = str(value)
    return s[:length], (len(s) > length)


def _parse_posting_date(value):
    """Parse SAP date string. Accepts dd.mm.yyyy or yyyy-mm-dd. Return YYYY-MM-DD or None.
    SAP null date (00.00.0000 or 0000-00-00) is treated as None."""
    if not value:
        return None
    s = str(value).strip()
    # SAP null/zero date representations
    if s in ('00.00.0000', '0000-00-00', '00/00/0000', '00-00-0000'):
        return None
    for fmt in ('%d.%m.%Y', '%Y-%m-%d', '%d-%m-%Y', '%d/%m/%Y'):
        try:
            return datetime.strptime(s[:10], fmt).strftime('%Y-%m-%d')
        except ValueError:
            continue
    return None


def _apply_record(rec):
    """Apply one callback record. Return (ok: bool, message: str)."""
    ref = (rec.get('Reference') or '').strip()
    if not ref:
        return False, 'Missing Reference'

    sap_doc, _      = _truncate(rec.get('Document_Number') or '', 50)
    irn, irn_trim   = _truncate(rec.get('IRN_No') or rec.get('IRN') or '', 64)
    ack, ack_trim   = _truncate(rec.get('Ack_No') or rec.get('Acknowledgement_No') or '', 20)
    irn_date_raw    = rec.get('IRN_Date') or rec.get('Ack_Date') or ''
    irn_date        = _parse_posting_date(irn_date_raw)  # already YYYY-MM-DD (10 chars) or None
    qr              = rec.get('QR_Code') or rec.get('QR') or ''
    sap_message     = (rec.get('Message') or '').strip()
    company_code, _ = _truncate(rec.get('Company_Code') or rec.get('Company_code') or '', 10)
    posting_date    = _parse_posting_date(rec.get('Posting_Date') or rec.get('Posting_date') or '')

    # Decide success vs SAP error: SAP message containing 'Error'/'invalid' marks failure.
    is_sap_error = bool(sap_message) and (
        'error' in sap_message.lower()
        or 'invalid' in sap_message.lower()
        or 'fail' in sap_message.lower()
    )

    note_bits = []
    if irn_trim: note_bits.append('IRN truncated to 64')
    if ack_trim: note_bits.append('Ack truncated to 20')

    conn = get_db()
    cur = get_cursor(conn)

    # Try invoice_header first
    cur.execute('SELECT id, sap_document_number, gst_irn, invoice_status FROM invoice_header WHERE invoice_number=%s', [ref])
    row = cur.fetchone()
    table, row_id = ('invoice_header', row['id']) if row else (None, None)

    if not row:
        cur.execute('SELECT id, sap_document_number, gst_irn, doc_status FROM fdcn_header WHERE doc_number=%s', [ref])
        row = cur.fetchone()
        if row:
            table, row_id = 'fdcn_header', row['id']

    if not table:
        conn.close()
        return True, 'Reference not found - accepted'

    existing_irn = (row.get('gst_irn') or '').strip()
    existing_sap = (row.get('sap_document_number') or '').strip()

    # Idempotency: same IRN already on row → no-op
    if irn and existing_irn and existing_irn == irn:
        conn.close()
        return True, 'Already recorded'

    # IRN mismatch protection
    if irn and existing_irn and existing_irn != irn:
        conn.close()
        return False, 'IRN mismatch with existing record'

    # Reversal detection: invoice already posted, incoming SAP doc# differs → treat as reversal
    if (table == 'invoice_header' and existing_sap and sap_doc and existing_sap != sap_doc and not irn):
        rev_note = f'Reversal SAP Doc: {sap_doc}'
        cur.execute('''UPDATE invoice_header
            SET invoice_status='Cancelled',
                remarks = CASE WHEN COALESCE(remarks,'')='' THEN %s ELSE remarks || ' | ' || %s END
            WHERE id=%s''', [rev_note, rev_note, row_id])
        conn.commit()
        conn.close()
        return True, 'Reversal recorded'

    # Build update set
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    if is_sap_error:
        if table == 'invoice_header':
            cur.execute('''UPDATE invoice_header
                SET invoice_status='SAP Failed', sap_error=%s
                WHERE id=%s''', [sap_message[:500], row_id])
        else:
            cur.execute('''UPDATE fdcn_header
                SET doc_status='SAP Failed', rejection_reason=%s
                WHERE id=%s''', [sap_message[:500], row_id])
        conn.commit()
        conn.close()
        return True, f'Stored SAP error: {sap_message[:80]}'

    # Success path: write all fields. Posting_Date from SAP overrides server-side fallback.
    new_status = 'Posted to GST' if irn else None
    posting_date_value = posting_date or now
    if table == 'invoice_header':
        # invoice_header date columns are now properly typed (migration
        # c7d8e9f0a1b2): sap_posting_date=TIMESTAMP, gst_ack_date=DATE.
        cur.execute('''UPDATE invoice_header SET
            sap_document_number = COALESCE(NULLIF(%s,''), sap_document_number),
            sap_posting_date    = COALESCE(NULLIF(%s,'')::timestamp, sap_posting_date),
            sap_company_code    = COALESCE(NULLIF(%s,''), sap_company_code),
            gst_irn             = COALESCE(NULLIF(%s,''), gst_irn),
            gst_ack_number      = COALESCE(NULLIF(%s,''), gst_ack_number),
            gst_ack_date        = COALESCE(NULLIF(%s,'')::date, gst_ack_date),
            gst_qr_code         = COALESCE(NULLIF(%s,''), gst_qr_code),
            invoice_status      = COALESCE(%s, invoice_status)
            WHERE id=%s''',
            [sap_doc, posting_date_value, company_code, irn, ack, irn_date or None, qr, new_status, row_id])
    else:
        cur.execute('''UPDATE fdcn_header SET
            sap_document_number = COALESCE(NULLIF(%s,''), sap_document_number),
            sap_posting_date    = COALESCE(NULLIF(%s,'')::date, sap_posting_date),
            sap_company_code    = COALESCE(NULLIF(%s,''), sap_company_code),
            gst_irn             = COALESCE(NULLIF(%s,''), gst_irn),
            gst_ack_number      = COALESCE(NULLIF(%s,''), gst_ack_number),
            gst_ack_date        = COALESCE(NULLIF(%s,'')::date, gst_ack_date),
            gst_qr_code         = COALESCE(NULLIF(%s,''), gst_qr_code),
            doc_status          = COALESCE(%s, doc_status)
            WHERE id=%s''',
            [sap_doc, posting_date_value, company_code, irn, ack, irn_date or None, qr, new_status, row_id])

    conn.commit()
    conn.close()
    msg = 'Updated'
    if note_bits:
        msg += ' (' + '; '.join(note_bits) + ')'
    return True, msg


def _log_inbound(token_row, request_body, response_body, status_code, http_status):
    """Insert one integration_logs row for the inbound call."""
    conn = get_db()
    cur = get_cursor(conn)
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    label = (token_row or {}).get('label', '')
    ip = (request.headers.get('X-Forwarded-For') or request.remote_addr or '')[:45]
    cur.execute('''INSERT INTO integration_logs
        (integration_type, source_type, source_id, source_reference,
         request_url, request_body, response_status_code, response_body,
         status, error_message, duration_ms, created_by, created_date)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)''',
        ['SAP_INBOUND', 'Token', (token_row or {}).get('id'),
         label,
         request.path,
         json.dumps(request_body) if request_body else None,
         http_status,
         json.dumps(response_body) if response_body else None,
         status_code, None, None, f'sap_callback@{ip}', now])
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# Flask route handler
# ---------------------------------------------------------------------------

def sap_callback_view():
    """Handler for POST /api/sap/callback."""
    auth = request.headers.get('Authorization', '')
    token_row = _verify_token(auth)
    if not token_row:
        return jsonify({'error': 'Unauthorized'}), 401

    try:
        body = request.get_json(force=True, silent=False)
    except Exception:
        resp = {'error': 'Bad JSON'}
        _log_inbound(token_row, None, resp, 'Error', 400)
        return jsonify(resp), 400

    if not isinstance(body, dict):
        resp = {'error': 'Body must be a JSON object'}
        _log_inbound(token_row, body, resp, 'Error', 400)
        return jsonify(resp), 400

    records = body.get('Record') or []
    if not isinstance(records, list):
        resp = {'error': 'Record must be an array'}
        _log_inbound(token_row, body, resp, 'Error', 400)
        return jsonify(resp), 400

    results = []
    any_error = False
    for rec in records:
        if not isinstance(rec, dict):
            results.append({'Reference': '', 'Status': 'E', 'Message': 'Record must be an object'})
            any_error = True
            continue
        try:
            ok, msg = _apply_record(rec)
        except Exception as e:
            ok, msg = False, f'Internal error: {str(e)[:200]}'
        results.append({
            'Reference': (rec.get('Reference') or '').strip(),
            'Status': 'S' if ok else 'E',
            'Message': msg
        })
        if not ok:
            any_error = True

    response = {'Record': results}
    _log_inbound(token_row, body, response, 'Error' if any_error else 'Success', 200)
    return jsonify(response), 200
