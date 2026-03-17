from flask import render_template, request, redirect, url_for, session, jsonify, make_response as flask_make_response
from datetime import datetime, timedelta
from . import bp
from modules.FIN01 import model  # reuse FIN01 model for invoice functions
from database import get_user_permissions, get_db, get_cursor, get_module_config
import sap_builder
import sap_client
import logging

log = logging.getLogger(__name__)

MODULE_CODE = 'FINV01'


def _parse_datetime(value):
    if not value:
        return None
    txt = str(value).strip()
    formats = (
        ('%Y-%m-%d %H:%M:%S', 19),
        ('%Y-%m-%dT%H:%M:%S', 19),
        ('%Y-%m-%d', 10),
    )
    for fmt, width in formats:
        try:
            return datetime.strptime(txt[:width], fmt)
        except Exception:
            continue
    return None


def get_perms():
    if session.get('is_admin'):
        return {'can_read': 1, 'can_add': 1, 'can_edit': 1, 'can_delete': 1}
    # Fall back to FIN01 permissions if FINV01 not set up yet
    perms = get_user_permissions(session.get('user_id'), MODULE_CODE)
    if not perms.get('can_read'):
        perms = get_user_permissions(session.get('user_id'), 'FIN01')
    return perms


# ===== Invoice List =====

@bp.route('/module/FINV01/')
def index():
    return redirect(url_for('FINV01.invoices'))


@bp.route('/module/FINV01/doc-series')
def doc_series_page():
    """Invoice doc series master management page"""
    if 'user_id' not in session:
        return redirect(url_for('login'))
    perms = get_perms()
    return render_template('finv01_doc_series.html',
                           perms=perms,
                           username=session.get('username'),
                           module_code='FINV01')


@bp.route('/module/FINV01/invoices')
def invoices():
    """List all invoices"""
    if 'user_id' not in session:
        return redirect(url_for('login'))

    perms = get_perms()
    page = int(request.args.get('page', 1))
    status_filter = request.args.get('status')
    data, total = model.get_invoice_data(page, status_filter=status_filter)

    return render_template('finv01_invoices.html',
                         data=data,
                         page=page,
                         last_page=(total + 19) // 20,
                         status_filter=status_filter,
                         perms=perms,
                         username=session.get('username'),
                         module_code='FINV01')


# ===== Generate Invoice from Bills =====

@bp.route('/module/FINV01/generate')
def generate_invoice():
    """Generate invoice from approved bills"""
    if 'user_id' not in session:
        return redirect(url_for('login'))

    perms = get_perms()

    # Get all approved bills not yet invoiced
    approved_bills, _ = model.get_bill_data(page=1, size=1000, status_filter='Approved')

    from datetime import datetime
    current_date = datetime.now().strftime('%Y-%m-%d')

    return render_template('finv01_generate_invoice.html',
                         approved_bills=approved_bills,
                         current_date=current_date,
                         perms=perms,
                         username=session.get('username'),
                         module_code='FINV01')


def _auto_post_to_sap(invoice_id, invoice_number):
    """Auto-post invoice to SAP. Updates status to 'Posted to SAP' or 'SAP Failed'."""
    try:
        invoice = model.get_invoice_by_id(invoice_id)
        invoice_lines = model.get_invoice_lines(invoice_id)
        payload = sap_builder.build_invoice_payload(invoice, invoice_lines)
        result = sap_client.post_invoice_to_sap(
            payload, 'Invoice', invoice_id,
            invoice_number, session.get('username')
        )
        now_ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        conn = get_db()
        cur = get_cursor(conn)
        if result['ok']:
            cur.execute('''UPDATE invoice_header
                SET sap_document_number=%s, sap_posting_date=%s,
                    posted_by=%s, posted_date=%s,
                    invoice_status='Posted to SAP'
                WHERE id=%s''',
                [result['sap_document_number'], now_ts,
                 session.get('username'), now_ts, invoice_id])
        else:
            cur.execute('''UPDATE invoice_header
                SET invoice_status='SAP Failed',
                    remarks = CASE
                        WHEN COALESCE(remarks, '') = '' THEN %s
                        ELSE %s
                    END
                WHERE id=%s''',
                [result['message'], result['message'], invoice_id])
        conn.commit()
        conn.close()
        return result
    except Exception as e:
        log.exception('Auto-post to SAP failed for invoice %s', invoice_number)
        # Mark as failed
        try:
            conn = get_db()
            cur = get_cursor(conn)
            cur.execute('''UPDATE invoice_header
                SET invoice_status='SAP Failed', remarks=%s WHERE id=%s''',
                [str(e), invoice_id])
            conn.commit()
            conn.close()
        except Exception:
            pass
        return {'ok': False, 'message': str(e)}


@bp.route('/api/module/FINV01/invoice/create', methods=['POST'])
def create_invoice():
    """Create invoice from selected bills"""
    if 'user_id' not in session:
        return jsonify({'success': False, 'error': 'Not logged in'})

    perms = get_perms()
    if not perms.get('can_add'):
        return jsonify({'success': False, 'error': 'No permission'})

    data = request.json
    bill_ids = data.get('bill_ids', [])

    # Resolve invoice number from doc series
    doc_series_id = data.get('doc_series_id')
    doc_series_prefix = data.get('doc_series_prefix', 'INV')
    doc_series_name = data.get('doc_series_name', '')

    invoice_date = data.get('invoice_date', '')
    # Determine financial year suffix e.g. 25-26
    fy_suffix = model.get_financial_year(invoice_date) if invoice_date else ''
    # Compute next sequence for this prefix/FY
    conn_seq = get_db()
    cur_seq = get_cursor(conn_seq)
    like_pat = f'{doc_series_prefix}/{fy_suffix}/%'
    cur_seq.execute(
        'SELECT MAX(doc_series_seq) FROM invoice_header WHERE doc_series=%s AND financial_year=%s',
        [doc_series_prefix, fy_suffix]
    )
    row_seq = cur_seq.fetchone()
    next_seq = (row_seq['max'] or 0) + 1 if row_seq else 1
    conn_seq.close()
    invoice_number_override = f'{doc_series_prefix}/{fy_suffix}/{next_seq}'

    invoice_data = {
        'invoice_date': invoice_date,
        'invoice_series': doc_series_prefix,
        'doc_series': doc_series_prefix,
        'doc_series_seq': next_seq,
        'customer_type': data.get('customer_type'),
        'customer_id': data.get('customer_id'),
        'customer_name': data.get('customer_name'),
        'customer_gstin': data.get('customer_gstin'),
        'customer_gst_state_code': data.get('customer_gst_state_code'),
        'customer_gl_code': data.get('customer_gl_code'),
        'customer_pan': data.get('customer_pan'),
        'billing_address': data.get('billing_address'),
        'customer_city': data.get('customer_city'),
        'customer_pincode': data.get('customer_pincode'),
        'customer_phone': data.get('customer_phone'),
        'customer_email': data.get('customer_email'),
        'ship_to_name': data.get('ship_to_name'),
        'ship_to_address': data.get('ship_to_address'),
        'ship_to_gstin': data.get('ship_to_gstin'),
        'ship_to_state_code': data.get('ship_to_state_code'),
        'currency_code': data.get('currency_code', 'INR'),
        'exchange_rate': data.get('exchange_rate', 1.0),
        'subtotal': data.get('subtotal'),
        'cgst_amount': data.get('cgst_amount'),
        'sgst_amount': data.get('sgst_amount'),
        'igst_amount': data.get('igst_amount'),
        'tds_amount': data.get('tds_amount', 0),
        'round_off': data.get('round_off', 0),
        'total_amount': data.get('total_amount'),
        'amount_in_words': data.get('amount_in_words'),
        'payment_terms': data.get('payment_terms'),
        'due_date': data.get('due_date'),
        'vessel_name': data.get('vessel_name'),
        'vessel_call_no': data.get('vessel_call_no'),
        'commodity': data.get('commodity'),
        'date_of_berthing': data.get('date_of_berthing'),
        'date_of_sailing': data.get('date_of_sailing'),
        'grt_of_vessel': data.get('grt_of_vessel'),
        'no_of_days': data.get('no_of_days'),
        'cargo_quantity': data.get('cargo_quantity'),
        'no_of_hrs': data.get('no_of_hrs'),
        'created_by': session.get('username'),
        'created_date': __import__('datetime').datetime.now().strftime('%Y-%m-%d'),
        'remarks': data.get('remarks'),
        '_invoice_number_override': invoice_number_override,
    }

    invoice_id, invoice_number = model.create_invoice_from_bills(bill_ids, invoice_data)

    # Auto-post to SAP immediately after creation
    sap_result = _auto_post_to_sap(invoice_id, invoice_number)

    return jsonify({
        'success': True,
        'id': invoice_id,
        'invoice_number': invoice_number,
        'sap_status': 'Posted to SAP' if sap_result.get('ok') else 'SAP Failed',
        'sap_document_number': sap_result.get('sap_document_number', ''),
        'sap_message': sap_result.get('message', ''),
    })


# ===== Bill lines (for generate invoice page) =====

@bp.route('/api/module/FINV01/bill-lines/<int:bill_id>')
def get_bill_lines_api(bill_id):
    """Get bill lines for expand in invoice generation page"""
    if 'user_id' not in session:
        return jsonify({'error': 'Not logged in'}), 401
    lines = model.get_bill_lines(bill_id)
    return jsonify({'lines': lines})


# ===== Print Invoice =====

@bp.route('/module/FINV01/invoice/print/<int:invoice_id>')
def print_invoice(invoice_id):
    """Print invoice"""
    if 'user_id' not in session:
        return redirect(url_for('login'))

    perms = get_perms()
    if not perms.get('can_read'):
        return render_template('no_access.html'), 403

    invoice = model.get_invoice_by_id(invoice_id)
    if not invoice:
        return "Invoice not found", 404

    invoice_lines = model.get_invoice_lines(invoice_id)
    sac_summary = model.get_invoice_sac_summary(invoice_id)

    # If vessel details are missing, re-fetch live from the bill chain
    if not (invoice.get('vessel_name') or '').strip():
        try:
            conn_v = get_db()
            cur_v = get_cursor(conn_v)
            cur_v.execute(
                'SELECT bill_id FROM invoice_bill_mapping WHERE invoice_id=%s ORDER BY id LIMIT 1',
                [invoice_id]
            )
            bm = cur_v.fetchone()
            conn_v.close()
            if bm:
                vd_resp = get_bill_vessel_details(bm['bill_id'])
                vd = vd_resp.get_json() if hasattr(vd_resp, 'get_json') else {}
                invoice = dict(invoice)
                for fld in ('vessel_name', 'vessel_call_no', 'commodity',
                            'date_of_berthing', 'date_of_sailing', 'grt_of_vessel', 'cargo_quantity'):
                    if vd.get(fld) and not (invoice.get(fld) or ''):
                        invoice[fld] = vd[fld]
        except Exception:
            pass

    # Port config for header GSTIN etc.
    config = get_module_config('FIN01')
    port_config = {
        'seller_gstin': config.get('seller_gstin', ''),
        'seller_legal_name': config.get('seller_legal_name', 'JSW Dharamtar Port Pvt. Ltd.'),
    }

    # Payment bank: use customer virtual account if available, else port bank account
    payment_bank = None
    conn_b = get_db()
    cur_b = get_cursor(conn_b)
    try:
        ctype = (invoice.get('customer_type') or '').lower()
        cid   = invoice.get('customer_id')
        va = None
        if cid:
            tbl = 'vessel_agents' if ctype == 'agent' else 'vessel_customers'
            cur_b.execute(f'SELECT virtual_account_number FROM {tbl} WHERE id=%s', [cid])
            row = cur_b.fetchone()
            if row:
                va = (row['virtual_account_number'] or '').strip()

        if va:
            # Build a synthetic bank dict from the virtual account number
            cur_b.execute('SELECT * FROM port_bank_accounts ORDER BY id LIMIT 1')
            base = cur_b.fetchone()
            payment_bank = dict(base) if base else {}
            payment_bank['account_number'] = va
        else:
            cur_b.execute('SELECT * FROM port_bank_accounts ORDER BY id LIMIT 1')
            row = cur_b.fetchone()
            payment_bank = dict(row) if row else None
    except Exception:
        payment_bank = None
    conn_b.close()

    from datetime import datetime
    current_datetime = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    return render_template('finv01_invoice_print.html',
                         invoice=invoice,
                         invoice_lines=invoice_lines,
                         sac_summary=sac_summary,
                         port_config=port_config,
                         payment_bank=payment_bank,
                         current_datetime=current_datetime)


# ===== GSTR-1 B2B Export =====

@bp.route('/api/module/FINV01/export/gstr1-b2b', methods=['POST'])
def export_gstr1_b2b():
    """Export selected invoices as GSTR-1 B2B JSON"""
    if 'user_id' not in session:
        return jsonify({'error': 'Not logged in'}), 401

    from datetime import datetime
    data = request.json
    invoice_ids = data.get('invoice_ids', [])
    supplier_gstin = data.get('supplier_gstin', '')
    filing_period = data.get('filing_period', '')

    if not invoice_ids:
        return jsonify({'error': 'No invoices selected'}), 400

    b2b = {}
    for inv_id in invoice_ids:
        invoice = model.get_invoice_by_id(inv_id)
        if not invoice:
            continue
        lines = model.get_invoice_lines(inv_id)
        ctin = invoice.get('customer_gstin', '')
        if not ctin:
            continue

        if ctin not in b2b:
            b2b[ctin] = {'ctin': ctin, 'inv': []}

        rate_groups = {}
        for line in lines:
            cgst = float(line.get('cgst_rate') or 0)
            sgst = float(line.get('sgst_rate') or 0)
            igst = float(line.get('igst_rate') or 0)
            rt = igst if igst > 0 else (cgst + sgst)
            if rt not in rate_groups:
                rate_groups[rt] = {'txval': 0, 'camt': 0, 'samt': 0, 'iamt': 0, 'csamt': 0}
            rate_groups[rt]['txval'] += float(line.get('line_amount') or 0)
            rate_groups[rt]['camt'] += float(line.get('cgst_amount') or 0)
            rate_groups[rt]['samt'] += float(line.get('sgst_amount') or 0)
            rate_groups[rt]['iamt'] += float(line.get('igst_amount') or 0)

        itms = [{'num': i+1, 'itm_det': {
            'rt': round(rt, 2), 'txval': round(v['txval'], 2),
            'camt': round(v['camt'], 2), 'samt': round(v['samt'], 2),
            'iamt': round(v['iamt'], 2), 'csamt': round(v['csamt'], 2)
        }} for i, (rt, v) in enumerate(rate_groups.items())]

        inv_date = invoice.get('invoice_date')
        if hasattr(inv_date, 'strftime'):
            inv_date = inv_date.strftime('%d-%m-%Y')
        else:
            inv_date = str(inv_date or '')
            if '-' in inv_date:
                parts = inv_date.split('-')
                if len(parts) == 3 and len(parts[0]) == 4:
                    inv_date = f"{parts[2]}-{parts[1]}-{parts[0]}"

        b2b[ctin]['inv'].append({
            'inum': invoice.get('invoice_number', ''),
            'idt': inv_date,
            'val': round(float(invoice.get('total_amount') or 0), 2),
            'pos': invoice.get('customer_gst_state_code', ''),
            'rchrg': 'N', 'inv_typ': 'R', 'itms': itms
        })

    return jsonify({'gstin': supplier_gstin, 'fp': filing_period, 'b2b': list(b2b.values())})


# ===== SAP Integration =====

@bp.route('/api/module/FINV01/invoice/retry-sap', methods=['POST'])
def retry_sap():
    """Retry SAP posting for a failed invoice"""
    if 'user_id' not in session:
        return jsonify({'success': False, 'error': 'Not logged in'}), 401

    perms = get_perms()
    if not perms.get('can_edit'):
        return jsonify({'success': False, 'error': 'No permission'}), 403

    invoice_id = request.json.get('invoice_id')
    invoice = model.get_invoice_by_id(invoice_id)
    if not invoice:
        return jsonify({'success': False, 'error': 'Invoice not found'}), 404

    if invoice.get('sap_document_number'):
        return jsonify({'success': False, 'error': 'Invoice already posted to SAP'})

    if invoice.get('invoice_status') not in ('SAP Failed', 'Generated'):
        return jsonify({'success': False, 'error': f"Cannot retry — status is '{invoice.get('invoice_status')}'"})

    result = _auto_post_to_sap(invoice_id, invoice['invoice_number'])

    return jsonify({
        'success': result.get('ok', False),
        'sap_document_number': result.get('sap_document_number'),
        'message': result.get('message', ''),
        'log_id': result.get('log_id')
    })


@bp.route('/api/module/FINV01/invoice/fetch-irn', methods=['POST'])
def fetch_irn():
    """Fetch IRN details from SAP (populated by Cygnet after e-invoice generation)"""
    if 'user_id' not in session:
        return jsonify({'success': False, 'error': 'Not logged in'}), 401

    invoice_id = request.json.get('invoice_id')
    invoice = model.get_invoice_by_id(invoice_id)
    if not invoice:
        return jsonify({'success': False, 'error': 'Invoice not found'}), 404

    if not invoice.get('sap_document_number'):
        return jsonify({'success': False, 'error': 'Invoice not yet posted to SAP'})

    if invoice.get('gst_irn'):
        return jsonify({'success': False, 'error': 'IRN already present',
                        'irn': invoice['gst_irn']})

    result = sap_client.fetch_irn_from_sap(
        invoice['invoice_number'], 'Invoice', invoice_id,
        session.get('username')
    )

    if result['ok']:
        conn = get_db()
        cur = get_cursor(conn)
        cur.execute('''UPDATE invoice_header
            SET gst_irn=%s, gst_ack_number=%s, gst_ack_date=%s
            WHERE id=%s''',
            [result['irn'], result['ack_no'],
             result.get('ack_date') or result.get('irn_date') or None,
             invoice_id])
        conn.commit()
        conn.close()

    return jsonify({
        'success': result['ok'],
        'irn': result.get('irn', ''),
        'ack_no': result.get('ack_no', ''),
        'irn_date': result.get('irn_date', ''),
        'message': result['message'],
    })


@bp.route('/api/module/FINV01/invoice/cancel-sap', methods=['POST'])
def cancel_invoice_sap():
    """Cancel/reverse an SAP-posted invoice (FB08 rule: within 24 hours only)"""
    if 'user_id' not in session:
        return jsonify({'success': False, 'error': 'Not logged in'}), 401

    perms = get_perms()
    if not perms.get('can_edit'):
        return jsonify({'success': False, 'error': 'No permission'}), 403

    invoice_id = request.json.get('invoice_id')
    invoice = model.get_invoice_by_id(invoice_id)
    if not invoice:
        return jsonify({'success': False, 'error': 'Invoice not found'}), 404

    if not invoice.get('sap_document_number'):
        return jsonify({'success': False, 'error': 'Invoice is not posted to SAP'})

    if invoice.get('invoice_status') == 'Cancelled':
        return jsonify({'success': False, 'error': 'Invoice is already cancelled'})

    posted_dt = (
        _parse_datetime(invoice.get('sap_posting_date')) or
        _parse_datetime(invoice.get('posted_date')) or
        _parse_datetime(invoice.get('created_date'))
    )
    if posted_dt and datetime.now() - posted_dt > timedelta(hours=24):
        return jsonify({
            'success': False,
            'error': 'Cancellation beyond 24 hours is not allowed. Create a new Debit/Credit Note instead.'
        }), 400

    invoice_lines = model.get_invoice_lines(invoice_id)
    payload = sap_builder.build_invoice_reversal_payload(invoice, invoice_lines)
    result = sap_client.post_invoice_to_sap(
        payload, 'InvoiceReversal', invoice_id,
        invoice['invoice_number'], session.get('username')
    )

    if result['ok']:
        now_ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        reversal_doc = result.get('sap_document_number') or ''
        original_doc = invoice.get('sap_document_number') or ''
        reversal_note = f"SAP FB08 reversal posted. Original: {original_doc}; Reversal: {reversal_doc}"
        conn = get_db()
        cur = get_cursor(conn)
        cur.execute('''UPDATE invoice_header
            SET invoice_status='Cancelled',
                posted_by=%s,
                posted_date=%s,
                remarks = CASE
                    WHEN COALESCE(remarks, '') = '' THEN %s
                    ELSE remarks || ' | ' || %s
                END
            WHERE id=%s''',
            [session.get('username'), now_ts, reversal_note, reversal_note, invoice_id])
        conn.commit()
        conn.close()

    return jsonify({
        'success': result['ok'],
        'sap_document_number': result.get('sap_document_number'),
        'message': result['message'],
        'log_id': result['log_id']
    })




def _build_sample_payload(invoice, lines, cancel=False):
    """Fallback sample payload when SAP config is not yet configured."""
    inv_date = invoice.get('invoice_date') or ''
    if inv_date:
        try:
            inv_date = datetime.strptime(str(inv_date)[:10], '%Y-%m-%d').strftime('%d.%m.%Y')
        except ValueError:
            pass

    total = float(invoice.get('total_amount') or 0)
    items = []
    for l in lines:
        cgst = float(l.get('cgst_amount') or 0)
        sgst = float(l.get('sgst_amount') or 0)
        igst = float(l.get('igst_amount') or 0)
        items.append({
            'Service_Code': l.get('service_code') or l.get('gl_code') or '',
            'CGST_AMT': f'{cgst:.2f}' if cgst else '',
            'SGST_AMT': f'{sgst:.2f}' if sgst else '',
            'IGST_AMT': f'{igst:.2f}' if igst else '',
            'Amount': f'{float(l.get("line_amount") or 0):.2f}',
            'Text': (l.get('service_name') or '')[:50],
            'Plant': '5130',
            'Business_Place': '5130',
            'Section_Code': '5130',
            'Tax_Code': l.get('sap_tax_code') or '',
            'Profit_Center': '',
            'HSN_SAC': l.get('sac_code') or l.get('hsn_sac') or '',
            'TDS_Amount': f'{float(l.get("tds_amount") or 0):.2f}' if l.get('tds_amount') else '',
            'TCS_Amount': '',
            'Rounding_off': '',
        })

    gstin = invoice.get('customer_gstin') or ''
    record = {
        'Company_Code': '5130',
        'Document_Date': inv_date,
        'Posting_Date': inv_date,
        'Document_Type': 'Y1',
        'Reference_Text': (invoice.get('invoice_number') or '')[:16],
        'Doc_Header_Text': (f"REV {invoice.get('invoice_number', '')}" if cancel else invoice.get('invoice_number', ''))[:25],
        'Currency': invoice.get('currency_code') or 'INR',
        'Customer_Code': invoice.get('customer_gl_code') or '',
        'Payment_Term': '',
        'Baseline_Date': inv_date,
        'Invoice_Amount': f'{total:.2f}',
        'IRN_No': invoice.get('gst_irn') or '',
        'Ack_No': str(invoice.get('gst_ack_number') or ''),
        'IRN_Date': '',
        'Nature_of_transaction': 'B2B' if gstin else 'B2C',
        'Cancellation_Flag': 'X' if cancel else '',
        'Item': items,
    }
    return {'Record': record}


# ===== SAP JSON Export (temporary — for SAP team review) =====

@bp.route('/api/module/FINV01/invoice/export-sap-json/<int:invoice_id>')
def export_sap_json(invoice_id):
    """Export SAP payload JSON for an invoice (posting + cancellation samples)"""
    if 'user_id' not in session:
        return jsonify({'error': 'Not logged in'}), 401

    invoice = model.get_invoice_by_id(invoice_id)
    if not invoice:
        return jsonify({'error': 'Invoice not found'}), 404

    invoice_lines = model.get_invoice_lines(invoice_id)

    # Build posting payload (Y1) — use defaults if SAP config not yet set up
    try:
        posting_payload = sap_builder.build_invoice_payload(invoice, invoice_lines)
        cancellation_payload = sap_builder.build_invoice_reversal_payload(invoice, invoice_lines)
    except ValueError:
        # No SAP config — build with hardcoded defaults for sample review
        posting_payload = _build_sample_payload(invoice, invoice_lines, cancel=False)
        cancellation_payload = _build_sample_payload(invoice, invoice_lines, cancel=True)

    # Enrich with customer & service master details for SAP team reference
    enriched = {
        '_info': 'Sample SAP payloads generated from PORTMAN for SAP team review',
        '_invoice_number': invoice.get('invoice_number'),
        '_customer_name': invoice.get('customer_name'),
        '_customer_gstin': invoice.get('customer_gstin'),
        '_invoice_date': invoice.get('invoice_date'),
        '_total_amount': float(invoice.get('total_amount') or 0),
        'posting_payload': posting_payload,
        'cancellation_payload': cancellation_payload,
    }

    import json
    response = flask_make_response(json.dumps(enriched, indent=2, ensure_ascii=False))
    response.headers['Content-Type'] = 'application/json'
    response.headers['Content-Disposition'] = f'attachment; filename=SAP_Payload_{invoice.get("invoice_number", invoice_id)}.json'
    return response


# ===== Customer/Agent lookup for virtual account + full billing details =====

@bp.route('/api/module/FINV01/customer-bank/<customer_type>/<int:customer_id>')
def get_customer_bank(customer_type, customer_id):
    """Return full billing details + virtual_account_number from VAM01/VCUM01 master"""
    if 'user_id' not in session:
        return jsonify({'error': 'Not logged in'}), 401
    conn = get_db()
    cur = get_cursor(conn)
    try:
        if customer_type.lower() == 'agent':
            cur.execute('''
                SELECT name, gstin, gst_state_code, gst_state_name, pan,
                       billing_address, city, pincode, virtual_account_number
                FROM vessel_agents WHERE id=%s
            ''', [customer_id])
        else:
            cur.execute('''
                SELECT name, gstin, gst_state_code, gst_state_name, pan,
                       billing_address, city, pincode, virtual_account_number
                FROM vessel_customers WHERE id=%s
            ''', [customer_id])
        row = cur.fetchone()
        result = dict(row) if row else {}
    except Exception:
        result = {}
    conn.close()
    return jsonify(result)


# ===== Bill → Vessel details (LDUD / VCN / MBC lookup) =====

@bp.route('/api/module/FINV01/bill-vessel-details/<int:bill_id>')
def get_bill_vessel_details(bill_id):
    """Trace a bill back to vessel/cargo details via LUEU01 chain, then bill_header fallback.
    Collects ALL linked sources and concatenates unique values with commas."""
    if 'user_id' not in session:
        return jsonify({'error': 'Not logged in'}), 401
    conn = get_db()
    cur = get_cursor(conn)
    result = {}
    try:
        # Primary path: bill_lines → lueu_lines → ldud_header/mbc_header (ALL lines)
        cur.execute('''
            SELECT DISTINCT ll.source_type, ll.source_id,
                            ll.cargo_name AS eu_cargo, ll.quantity AS eu_qty
            FROM bill_lines bl
            JOIN lueu_lines ll ON ll.id = bl.eu_line_id
            WHERE bl.bill_id = %s AND bl.eu_line_id IS NOT NULL
        ''', [bill_id])
        eu_rows = cur.fetchall()

        if eu_rows:
            vessel_names, vcn_docs, commodities = [], [], []
            berthing_dates, sailing_dates, grts = [], [], []
            total_qty = 0

            # Separate LDUD and MBC source IDs
            ldud_ids = list({r['source_id'] for r in eu_rows
                             if (r['source_type'] or '').upper() == 'LDUD' and r['source_id']})
            mbc_ids  = list({r['source_id'] for r in eu_rows
                             if (r['source_type'] or '').upper() == 'MBC'  and r['source_id']})

            if ldud_ids:
                cur.execute('''
                    SELECT DISTINCT v.vessel_name, v.vcn_doc_num, v.vessel_master_doc,
                           l.nor_tendered, l.discharge_completed
                    FROM ldud_header l
                    JOIN vcn_header v ON v.id = l.vcn_id
                    WHERE l.id = ANY(%s)
                    ORDER BY v.vessel_name
                ''', (ldud_ids,))
                for row in cur.fetchall():
                    if row['vessel_name'] and row['vessel_name'] not in vessel_names:
                        vessel_names.append(row['vessel_name'])
                    if row['vcn_doc_num'] and row['vcn_doc_num'] not in vcn_docs:
                        vcn_docs.append(row['vcn_doc_num'])
                    if row['nor_tendered']:
                        d = _fmt_date(row['nor_tendered'])
                        if d and d not in berthing_dates:
                            berthing_dates.append(d)
                    if row['discharge_completed']:
                        d = _fmt_date(row['discharge_completed'])
                        if d and d not in sailing_dates:
                            sailing_dates.append(d)
                    if row.get('vessel_master_doc'):
                        cur.execute('SELECT gt FROM vessels WHERE doc_num=%s LIMIT 1',
                                    [row['vessel_master_doc']])
                        vrow = cur.fetchone()
                        if vrow and vrow['gt'] and str(vrow['gt']) not in grts:
                            grts.append(str(vrow['gt']))

            if mbc_ids:
                cur.execute('''
                    SELECT mbc_name, cargo_name, bl_quantity
                    FROM mbc_header WHERE id = ANY(%s)
                ''', (mbc_ids,))
                for row in cur.fetchall():
                    if row['mbc_name'] and row['mbc_name'] not in vessel_names:
                        vessel_names.append(row['mbc_name'])
                    if row['cargo_name'] and row['cargo_name'] not in commodities:
                        commodities.append(row['cargo_name'])

            for r in eu_rows:
                if r['eu_cargo'] and r['eu_cargo'] not in commodities:
                    commodities.append(r['eu_cargo'])
                if r['eu_qty']:
                    total_qty += float(r['eu_qty'] or 0)

            result['vessel_name']      = ', '.join(vessel_names)
            result['vessel_call_no']   = ', '.join(vcn_docs)
            result['date_of_berthing'] = ', '.join(berthing_dates)
            result['date_of_sailing']  = ', '.join(sailing_dates)
            result['grt_of_vessel']    = ', '.join(grts) if grts else ''
            result['commodity']        = ', '.join(commodities)
            result['cargo_quantity']   = total_qty if total_qty else None

        else:
            # Fallback: bill_header.source_type / source_id
            cur.execute('SELECT source_type, source_id FROM bill_header WHERE id=%s', [bill_id])
            bill = cur.fetchone()
            if not bill:
                conn.close()
                return jsonify(result)

            src_type = (bill['source_type'] or '').upper()
            src_id   = bill['source_id']

            if src_type == 'VCN' and src_id:
                cur.execute('''
                    SELECT v.vessel_name, v.vcn_doc_num, v.vessel_master_doc,
                           l.nor_tendered, l.discharge_completed
                    FROM vcn_header v
                    LEFT JOIN ldud_header l ON l.vcn_id = v.id
                    WHERE v.id = %s
                    ORDER BY l.id DESC LIMIT 1
                ''', [src_id])
                row = cur.fetchone()
                if row:
                    result['vessel_name']      = row['vessel_name'] or ''
                    result['vessel_call_no']   = row['vcn_doc_num'] or ''
                    result['date_of_berthing'] = _fmt_date(row['nor_tendered'])
                    result['date_of_sailing']  = _fmt_date(row['discharge_completed'])
                    if row.get('vessel_master_doc'):
                        cur.execute('SELECT gt FROM vessels WHERE doc_num=%s LIMIT 1',
                                    [row['vessel_master_doc']])
                        vrow = cur.fetchone()
                        result['grt_of_vessel'] = vrow['gt'] if vrow else ''
                cur.execute('''
                    SELECT cargo_name, bl_quantity FROM vcn_cargo_declaration
                    WHERE vcn_id = %s ORDER BY id LIMIT 1
                ''', [src_id])
                cargo = cur.fetchone()
                if cargo:
                    result['commodity']      = cargo['cargo_name'] or ''
                    result['cargo_quantity'] = cargo['bl_quantity']

            elif src_type == 'MBC' and src_id:
                cur.execute('''
                    SELECT mbc_name, cargo_name, bl_quantity
                    FROM mbc_header WHERE id = %s
                ''', [src_id])
                row = cur.fetchone()
                if row:
                    result['vessel_name']    = row['mbc_name'] or ''
                    result['commodity']      = row['cargo_name'] or ''
                    result['cargo_quantity'] = row['bl_quantity']

    except Exception:
        pass
    conn.close()
    return jsonify(result)


def _fmt_date(val):
    """Extract YYYY-MM-DD from a datetime/text value"""
    if not val:
        return ''
    s = str(val)
    if len(s) >= 10:
        return s[:10]
    return s


# ===== Invoice Doc Series =====

def _ensure_invoice_doc_series(cur):
    cur.execute('''
        CREATE TABLE IF NOT EXISTS invoice_doc_series (
            id SERIAL PRIMARY KEY,
            name TEXT NOT NULL,
            prefix TEXT NOT NULL,
            is_default BOOLEAN DEFAULT FALSE
        )
    ''')


@bp.route('/api/module/FINV01/doc-series')
def get_doc_series():
    if 'user_id' not in session:
        return jsonify({'error': 'Not logged in'}), 401
    conn = get_db()
    cur = get_cursor(conn)
    _ensure_invoice_doc_series(cur)
    conn.commit()
    cur.execute('SELECT * FROM invoice_doc_series ORDER BY name')
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return jsonify(rows)


@bp.route('/api/module/FINV01/doc-series/save', methods=['POST'])
def save_doc_series():
    if 'user_id' not in session:
        return jsonify({'error': 'Not logged in'}), 401
    perms = get_perms()
    if not perms.get('can_edit') and not perms.get('can_add'):
        return jsonify({'error': 'No permission'}), 403
    data = request.json
    conn = get_db()
    cur = get_cursor(conn)
    _ensure_invoice_doc_series(cur)
    is_default = bool(data.get('is_default', False))
    if is_default:
        cur.execute('UPDATE invoice_doc_series SET is_default=FALSE WHERE is_default=TRUE')
    if data.get('id'):
        cur.execute('UPDATE invoice_doc_series SET name=%s, prefix=%s, is_default=%s WHERE id=%s',
                    [data['name'], data['prefix'], is_default, data['id']])
        row_id = data['id']
    else:
        cur.execute('INSERT INTO invoice_doc_series (name, prefix, is_default) VALUES (%s,%s,%s) RETURNING id',
                    [data['name'], data['prefix'], is_default])
        row_id = cur.fetchone()['id']
    conn.commit()
    conn.close()
    return jsonify({'success': True, 'id': row_id})


@bp.route('/api/module/FINV01/doc-series/delete', methods=['POST'])
def delete_doc_series():
    if 'user_id' not in session:
        return jsonify({'error': 'Not logged in'}), 401
    perms = get_perms()
    if not perms.get('can_delete'):
        return jsonify({'error': 'No permission'}), 403
    row_id = request.json.get('id')
    conn = get_db()
    cur = get_cursor(conn)
    cur.execute('DELETE FROM invoice_doc_series WHERE id=%s', [row_id])
    conn.commit()
    conn.close()
    return jsonify({'success': True})


# ===== Port Config (shared) =====

@bp.route('/api/module/FINV01/port-config')
def get_port_config():
    if 'user_id' not in session:
        return jsonify({'error': 'Not logged in'}), 401
    config = get_module_config('FIN01')
    conn = get_db()
    cur = get_cursor(conn)
    # Fetch the primary port bank account for invoice footer
    bank = {}
    try:
        _ensure_invoice_doc_series(cur)  # ensure tables exist
        cur.execute('''
            SELECT * FROM port_bank_accounts ORDER BY id LIMIT 1
        ''')
        row = cur.fetchone()
        if row:
            bank = dict(row)
    except Exception:
        pass
    conn.commit()
    conn.close()
    return jsonify({
        'port_gst_state_code': config.get('port_gst_state_code', ''),
        'port_gstin': config.get('port_gstin', ''),
        'seller_gstin': config.get('seller_gstin', ''),
        'seller_legal_name': config.get('seller_legal_name', ''),
        'bank': bank,
    })
