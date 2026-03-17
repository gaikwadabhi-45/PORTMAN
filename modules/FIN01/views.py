from flask import render_template, request, redirect, url_for, session, jsonify
from . import bp
from . import model
from database import get_user_permissions, get_db, get_cursor, get_module_config

@bp.route('/module/FIN01/')
def index():
    """Main FIN01 index - redirect to bills"""
    return redirect(url_for('FIN01.bills'))


@bp.route('/module/FIN01/invoices')
def legacy_invoices():
    """Legacy invoice list route; moved to FINV01"""
    return redirect(url_for('FINV01.invoices'))


@bp.route('/module/FIN01/invoice/generate')
def legacy_generate_invoice():
    """Legacy invoice generation route; moved to FINV01"""
    return redirect(url_for('FINV01.generate_invoice'))


@bp.route('/module/FIN01/invoice/print/<int:invoice_id>')
def legacy_print_invoice(invoice_id):
    """Legacy invoice print route; moved to FINV01"""
    return redirect(url_for('FINV01.print_invoice', invoice_id=invoice_id))


@bp.route('/module/FIN01/bills')
def bills():
    """List all bills"""
    if 'user_id' not in session:
        return redirect(url_for('login'))

    perms = get_user_permissions(session['user_id'], 'FIN01')
    page = int(request.args.get('page', 1))
    status_filter = request.args.get('status')
    data, total = model.get_bill_data(page, status_filter=status_filter)

    return render_template('bills.html',
                         data=data,
                         page=page,
                         last_page=(total + 19) // 20,
                         status_filter=status_filter,
                         perms=perms,
                         username=session.get('username'))


@bp.route('/module/FIN01/bill/<int:bill_id>')
def view_bill(bill_id):
    """View bill details"""
    if 'user_id' not in session:
        return redirect(url_for('login'))

    perms = get_user_permissions(session['user_id'], 'FIN01')

    # Get bill header
    bill = model.get_bill_by_id(bill_id)
    if not bill:
        return "Bill not found", 404

    # Get bill lines
    bill_lines = model.get_bill_lines(bill_id)

    return render_template('bill_view.html',
                         bill=bill,
                         bill_lines=bill_lines,
                         perms=perms,
                         username=session.get('username'))


@bp.route('/module/FIN01/bill/generate')
def generate_bill():
    """Generate bill - customer-centric"""
    if 'user_id' not in session:
        return redirect(url_for('login'))

    perms = get_user_permissions(session['user_id'], 'FIN01')

    from datetime import datetime
    current_date = datetime.now().strftime('%Y-%m-%d')

    return render_template('generate_bill.html',
                         current_date=current_date,
                         perms=perms,
                         username=session.get('username'))


@bp.route('/api/module/FIN01/bill/save', methods=['POST'])
def save_bill():
    """Save bill header"""
    if 'user_id' not in session:
        return jsonify({'success': False, 'error': 'Not logged in'})

    perms = get_user_permissions(session['user_id'], 'FIN01')
    if not perms['can_add'] and not perms['can_edit']:
        return jsonify({'success': False, 'error': 'No permission'})

    data = request.json

    # Extract lines from data before saving header (lines belong to bill_lines table, not bill_header)
    lines = data.pop('lines', [])

    data['created_by'] = session.get('username')
    data['created_date'] = __import__('datetime').datetime.now().strftime('%Y-%m-%d')

    # Set bill status based on approval config
    config = get_module_config('FIN01')
    user_id = session.get('user_id')
    is_approver = str(config.get('approver_id', '')) == str(user_id)
    is_admin = session.get('is_admin')

    if is_approver or is_admin:
        data['bill_status'] = 'Approved'
        data['approved_by'] = session.get('username')
        data['approved_date'] = data['created_date']
    elif config.get('approval_add'):
        data['bill_status'] = 'Pending Approval'
    else:
        data['bill_status'] = 'Draft'

    # Get source display name if not provided
    if not data.get('source_display') and data.get('source_type') and data.get('source_id'):
        conn = get_db()
        cur = get_cursor(conn)
        if data['source_type'] == 'VCN':
            cur.execute('SELECT vcn_doc_num FROM vcn_header WHERE id=%s', (data['source_id'],))
            row = cur.fetchone()
            data['source_display'] = row['vcn_doc_num'] if row else ''
        elif data['source_type'] == 'MBC':
            cur.execute('SELECT doc_num FROM mbc_header WHERE id=%s', (data['source_id'],))
            row = cur.fetchone()
            data['source_display'] = row['doc_num'] if row else ''
        conn.close()

    # Extract fields not in bill_header table before saving
    customer_state_code = data.pop('customer_state_code', '') or ''

    row_id, bill_number = model.save_bill_header(data)

    # Save bill lines and calculate totals
    subtotal = 0
    cgst_total = 0
    sgst_total = 0
    igst_total = 0

    customer_gstin = data.get('customer_gstin') or ''

    for line in lines:
        line['bill_id'] = row_id
        line['customer_gstin'] = customer_gstin
        line['customer_state_code'] = customer_state_code
        # Map frontend field names to model field names
        if not line.get('service_name') and line.get('description'):
            line['service_name'] = line['description']
        if not line.get('service_description'):
            line['service_description'] = line.get('description', '')
        model.save_bill_line(line)
        subtotal += float(line.get('line_amount') or 0)
        cgst_total += float(line.get('cgst_amount') or 0)
        sgst_total += float(line.get('sgst_amount') or 0)
        igst_total += float(line.get('igst_amount') or 0)

    # Update bill header with calculated totals + mark source records as billed
    total_amount = subtotal + cgst_total + sgst_total + igst_total
    conn = get_db()
    cur = get_cursor(conn)
    cur.execute('''UPDATE bill_header
        SET subtotal=%s, cgst_amount=%s, sgst_amount=%s, igst_amount=%s, total_amount=%s
        WHERE id=%s''',
        [subtotal, cgst_total, sgst_total, igst_total, total_amount, row_id])

    # Mark EU lines as billed
    for line in lines:
        if line.get('line_type') == 'cargo_handling':
            eu_ids = line.get('eu_line_ids') or []
            for eu_id in eu_ids:
                cur.execute('UPDATE lueu_lines SET is_billed=1, bill_id=%s WHERE id=%s',
                            [row_id, eu_id])

    # Mark service records as billed
    for line in lines:
        if line.get('line_type') == 'service_record' and line.get('service_record_id'):
            cur.execute('UPDATE service_records SET is_billed=1, bill_id=%s WHERE id=%s',
                        [row_id, line['service_record_id']])

    conn.commit()
    conn.close()

    return jsonify({'success': True, 'id': row_id, 'bill_number': bill_number})


@bp.route('/api/module/FIN01/bill/approve', methods=['POST'])
def approve_bill():
    """Approve a bill - only approver or admin"""
    if 'user_id' not in session:
        return jsonify({'success': False, 'error': 'Not logged in'})

    config = get_module_config('FIN01')
    user_id = session.get('user_id')
    is_approver = str(config.get('approver_id', '')) == str(user_id)
    is_admin = session.get('is_admin')

    if not is_approver and not is_admin:
        return jsonify({'success': False, 'error': 'Only approver or admin can approve bills'})

    bill_id = request.json.get('id')
    conn = get_db()
    cur = get_cursor(conn)
    cur.execute('''UPDATE bill_header
        SET bill_status='Approved', approved_by=%s, approved_date=%s
        WHERE id=%s''',
        [session.get('username'), __import__('datetime').datetime.now().strftime('%Y-%m-%d'), bill_id])
    conn.commit()
    conn.close()

    return jsonify({'success': True})


@bp.route('/api/module/FIN01/bill/submit', methods=['POST'])
def submit_bill():
    """Submit bill for approval"""
    if 'user_id' not in session:
        return jsonify({'success': False, 'error': 'Not logged in'})

    bill_id = request.json.get('id')
    conn = get_db()
    cur = get_cursor(conn)
    cur.execute('''UPDATE bill_header
        SET bill_status='Pending Approval'
        WHERE id=%s''', [bill_id])
    conn.commit()
    conn.close()

    return jsonify({'success': True})


@bp.route('/api/module/FIN01/bill/reject', methods=['POST'])
def reject_bill():
    """Reject a bill - only approver or admin"""
    if 'user_id' not in session:
        return jsonify({'success': False, 'error': 'Not logged in'})

    config = get_module_config('FIN01')
    user_id = session.get('user_id')
    is_approver = str(config.get('approver_id', '')) == str(user_id)
    is_admin = session.get('is_admin')

    if not is_approver and not is_admin:
        return jsonify({'success': False, 'error': 'Only approver or admin can reject bills'})

    bill_id = request.json.get('id')
    reason = request.json.get('reason', '')
    conn = get_db()
    cur = get_cursor(conn)
    cur.execute('''UPDATE bill_header
        SET bill_status='Rejected', rejection_reason=%s
        WHERE id=%s''', [reason, bill_id])
    conn.commit()
    conn.close()

    return jsonify({'success': True})


@bp.route('/api/module/FIN01/bill-lines/<int:bill_id>')
def get_bill_lines_api(bill_id):
    """Get bill lines for a specific bill (used in invoice generation page)"""
    if 'user_id' not in session:
        return jsonify({'error': 'Not logged in'}), 401
    lines = model.get_bill_lines(bill_id)
    return jsonify({'lines': lines})


@bp.route('/api/module/FIN01/eu-lines/<source_type>/<int:source_id>')
def get_lueu_lines(source_type, source_id):
    """Get all EU lines for a specific source (both billed and unbilled)"""
    if 'user_id' not in session:
        return jsonify({'error': 'Not logged in'}), 401

    conn = get_db()
    cur = get_cursor(conn)
    cur.execute('''
        SELECT el.*, st.service_name
        FROM lueu_lines el
        LEFT JOIN finance_service_types st ON el.service_type_id = st.id
        WHERE el.source_type = %s AND el.source_id = %s
        ORDER BY el.is_billed ASC, el.id
    ''', [source_type, source_id])
    rows = cur.fetchall()
    conn.close()

    return jsonify({'data': [dict(r) for r in rows]})


@bp.route('/api/module/FIN01/service-types')
def get_service_types():
    """Get all active service types with GST rate details"""
    if 'user_id' not in session:
        return jsonify({'error': 'Not logged in'}), 401

    conn = get_db()
    cur = get_cursor(conn)
    cur.execute('''
        SELECT s.id, s.service_name, s.service_code, s.sac_code, s.uom, s.gl_code,
               s.gst_rate_id,
               COALESCE(g.cgst_rate, 0) as cgst_rate,
               COALESCE(g.sgst_rate, 0) as sgst_rate,
               COALESCE(g.igst_rate, 0) as igst_rate,
               g.rate_name as gst_rate_name
        FROM finance_service_types s
        LEFT JOIN gst_rates g ON s.gst_rate_id = g.id
        WHERE s.is_active = 1
        ORDER BY s.service_name
    ''')
    rows = cur.fetchall()
    conn.close()

    return jsonify({'data': [dict(r) for r in rows]})


@bp.route('/api/module/FIN01/port-config')
def get_port_config():
    """Get port GST config (state code, GSTIN) from FIN01 module config"""
    if 'user_id' not in session:
        return jsonify({'error': 'Not logged in'}), 401

    config = get_module_config('FIN01')
    return jsonify({
        'port_gst_state_code': config.get('port_gst_state_code', ''),
        'port_gstin': config.get('port_gstin', ''),
        'seller_gstin': config.get('seller_gstin', ''),
        'seller_legal_name': config.get('seller_legal_name', ''),
        'seller_address': config.get('seller_address', ''),
        'seller_location': config.get('seller_location', ''),
        'seller_pincode': config.get('seller_pincode', ''),
        'seller_phone': config.get('seller_phone', ''),
        'seller_email': config.get('seller_email', '')
    })


@bp.route('/api/module/FIN01/customer-agreements/<int:customer_id>')
def get_customer_agreements(customer_id):
    """Get all valid active approved agreements for a customer"""
    if 'user_id' not in session:
        return jsonify({'error': 'Not logged in'}), 401

    from datetime import datetime
    today = datetime.now().strftime('%Y-%m-%d')

    conn = get_db()
    cur = get_cursor(conn)
    cur.execute('''
        SELECT id, agreement_code, agreement_name, currency_code, valid_from, valid_to
        FROM customer_agreements
        WHERE customer_id = %s
        AND is_active = 1
        AND agreement_status = 'Approved'
        AND valid_from <= %s
        AND (valid_to IS NULL OR valid_to >= %s)
        ORDER BY valid_from DESC
    ''', [customer_id, today, today])
    rows = cur.fetchall()
    conn.close()

    return jsonify({'data': [dict(r) for r in rows]})


@bp.route('/api/module/FIN01/agreement-rate/<customer_type>/<int:customer_id>/<int:service_type_id>')
def get_agreement_rate(customer_type, customer_id, service_type_id):
    """Get rate from active customer/agent agreement. Optionally filter by agreement_id and cargo_name."""
    if 'user_id' not in session:
        return jsonify({'error': 'Not logged in'}), 401

    from datetime import datetime

    conn = get_db()
    cur = get_cursor(conn)
    today = datetime.now().strftime('%Y-%m-%d')
    agreement_id = request.args.get('agreement_id')
    cargo_name = request.args.get('cargo_name')

    if agreement_id:
        # Try cargo-specific rate first
        if cargo_name:
            cur.execute('''
                SELECT cal.rate, cal.uom, cal.currency_code,
                       ca.agreement_code, ca.agreement_name, cal.cargo_name
                FROM customer_agreement_lines cal
                INNER JOIN customer_agreements ca ON cal.agreement_id = ca.id
                WHERE ca.id = %s AND cal.service_type_id = %s AND cal.cargo_name = %s
            ''', [agreement_id, service_type_id, cargo_name])
            row = cur.fetchone()
            if row:
                conn.close()
                return jsonify({'success': True, 'data': dict(row)})

        # Fallback to generic (no cargo) rate
        cur.execute('''
            SELECT cal.rate, cal.uom, cal.currency_code,
                   ca.agreement_code, ca.agreement_name, cal.cargo_name
            FROM customer_agreement_lines cal
            INNER JOIN customer_agreements ca ON cal.agreement_id = ca.id
            WHERE ca.id = %s AND cal.service_type_id = %s
              AND (cal.cargo_id IS NULL OR cal.cargo_name IS NULL)
        ''', [agreement_id, service_type_id])
    else:
        # Try cargo-specific rate first
        if cargo_name:
            cur.execute('''
                SELECT cal.rate, cal.uom, cal.currency_code,
                       ca.agreement_code, ca.agreement_name, cal.cargo_name
                FROM customer_agreement_lines cal
                INNER JOIN customer_agreements ca ON cal.agreement_id = ca.id
                WHERE ca.customer_type = %s
                AND ca.customer_id = %s
                AND cal.service_type_id = %s
                AND cal.cargo_name = %s
                AND ca.is_active = 1
                AND ca.agreement_status = 'Approved'
                AND ca.valid_from <= %s
                AND (ca.valid_to IS NULL OR ca.valid_to >= %s)
                ORDER BY ca.valid_from DESC
                LIMIT 1
            ''', [customer_type, customer_id, service_type_id, cargo_name, today, today])
            row = cur.fetchone()
            if row:
                conn.close()
                return jsonify({'success': True, 'data': dict(row)})

        # Fallback to generic rate
        cur.execute('''
            SELECT cal.rate, cal.uom, cal.currency_code,
                   ca.agreement_code, ca.agreement_name, cal.cargo_name
            FROM customer_agreement_lines cal
            INNER JOIN customer_agreements ca ON cal.agreement_id = ca.id
            WHERE ca.customer_type = %s
            AND ca.customer_id = %s
            AND cal.service_type_id = %s
            AND ca.is_active = 1
            AND ca.agreement_status = 'Approved'
            AND ca.valid_from <= %s
            AND (ca.valid_to IS NULL OR ca.valid_to >= %s)
            ORDER BY ca.valid_from DESC
            LIMIT 1
        ''', [customer_type, customer_id, service_type_id, today, today])
    row = cur.fetchone()
    conn.close()

    if row:
        return jsonify({'success': True, 'data': dict(row)})
    else:
        return jsonify({'success': False, 'error': 'No valid agreement found'})


@bp.route('/api/module/FIN01/cargo-rates/<customer_type>/<int:customer_id>/<int:service_type_id>')
def get_cargo_rates(customer_type, customer_id, service_type_id):
    """Get all cargo-specific rates for a service type from the agreement."""
    if 'user_id' not in session:
        return jsonify({'error': 'Not logged in'}), 401

    from datetime import datetime
    conn = get_db()
    cur = get_cursor(conn)
    today = datetime.now().strftime('%Y-%m-%d')
    agreement_id = request.args.get('agreement_id')

    if agreement_id:
        cur.execute('''
            SELECT cal.rate, cal.uom, cal.currency_code, cal.cargo_id, cal.cargo_name
            FROM customer_agreement_lines cal
            INNER JOIN customer_agreements ca ON cal.agreement_id = ca.id
            WHERE ca.id = %s AND cal.service_type_id = %s AND cal.cargo_name IS NOT NULL
        ''', [agreement_id, service_type_id])
    else:
        cur.execute('''
            SELECT cal.rate, cal.uom, cal.currency_code, cal.cargo_id, cal.cargo_name
            FROM customer_agreement_lines cal
            INNER JOIN customer_agreements ca ON cal.agreement_id = ca.id
            WHERE ca.customer_type = %s
            AND ca.customer_id = %s
            AND cal.service_type_id = %s
            AND cal.cargo_name IS NOT NULL
            AND ca.is_active = 1
            AND ca.agreement_status = 'Approved'
            AND ca.valid_from <= %s
            AND (ca.valid_to IS NULL OR ca.valid_to >= %s)
            ORDER BY ca.valid_from DESC
        ''', [customer_type, customer_id, service_type_id, today, today])
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()

    # Build a map: cargo_name -> rate
    rate_map = {}
    for r in rows:
        if r['cargo_name'] and r['cargo_name'] not in rate_map:
            rate_map[r['cargo_name']] = r['rate']

    return jsonify({'success': True, 'rates': rate_map})


@bp.route('/api/module/FIN01/customer-billables/<customer_type>/<int:customer_id>')
def get_customer_billables(customer_type, customer_id):
    """Get all billable items for a customer/agent.
    Returns:
      cargo_handling: lueu_lines grouped by source doc with LDUD closure status
      other_services: approved unbilled service records for this customer
      billed: already billed bill_lines for reference
    """
    if 'user_id' not in session:
        return jsonify({'error': 'Not logged in'}), 401

    conn = get_db()
    cur = get_cursor(conn)

    # Look up the selected customer/agent name for cargo filtering
    if customer_type == 'Customer':
        cur.execute("SELECT name FROM vessel_customers WHERE id = %s", [customer_id])
    else:
        cur.execute("SELECT name FROM vessel_agents WHERE id = %s", [customer_id])
    cust_row = cur.fetchone()
    customer_name = cust_row['name'] if cust_row else ''

    # --- A. Cargo Handling: unbilled lueu_lines for this customer's VCNs/MBCs ---
    # Get CARGO_LOAD and CARGO_UNLOAD service type IDs
    cur.execute("""
        SELECT id, service_code, service_name, sac_code, uom
        FROM finance_service_types
        WHERE service_code IN ('CHGL01', 'CHGU01')
    """)
    cargo_st_map = {r['service_code']: dict(r) for r in cur.fetchall()}

    # Find VCN IDs where this customer appears in cargo declarations
    cur.execute("""
        SELECT DISTINCT vcn_id, cargo_name FROM vcn_cargo_declaration
        WHERE customer_name = %s
        UNION
        SELECT DISTINCT vcn_id, cargo_name FROM vcn_export_cargo_declaration
        WHERE customer_name = %s
    """, [customer_name, customer_name])
    vcn_cargo_map = {}
    for r in cur.fetchall():
        vcn_cargo_map.setdefault(r['vcn_id'], set()).add(r['cargo_name'])

    # Find MBC IDs where this customer appears in customer details
    cur.execute("""
        SELECT DISTINCT mbc_id, cargo_name FROM mbc_customer_details
        WHERE customer_name = %s
    """, [customer_name])
    mbc_cargo_map = {}
    for r in cur.fetchall():
        mbc_cargo_map.setdefault(r['mbc_id'], set())
        if r['cargo_name']:
            mbc_cargo_map[r['mbc_id']].add(r['cargo_name'])

    # Build list of allowed (source_type, source_id) pairs
    allowed_sources = set()
    for vid in vcn_cargo_map:
        allowed_sources.add(('VCN', vid))
    for mid in mbc_cargo_map:
        allowed_sources.add(('MBC', mid))

    cur.execute("""
        SELECT el.*
        FROM lueu_lines el
        WHERE el.is_billed = 0 OR el.is_billed IS NULL
        ORDER BY el.source_type, el.source_id, el.operation_type, el.id
    """)
    eu_rows = [dict(r) for r in cur.fetchall()]

    # Group by (source_type, source_id), filtering to allowed sources only
    from collections import defaultdict
    eu_groups = defaultdict(list)
    for r in eu_rows:
        key = (r['source_type'], r['source_id'])
        if key not in allowed_sources:
            continue
        # For VCN sources, filter by customer's cargo names
        if r['source_type'] == 'VCN' and r['source_id'] in vcn_cargo_map:
            if r.get('cargo_name') not in vcn_cargo_map[r['source_id']]:
                continue
        # For MBC sources with cargo-level filtering
        if r['source_type'] == 'MBC' and r['source_id'] in mbc_cargo_map:
            mbc_cargos = mbc_cargo_map[r['source_id']]
            if mbc_cargos and r.get('cargo_name') not in mbc_cargos:
                continue
        eu_groups[key].append(r)

    cargo_handling = []
    for (src_type, src_id), lines in eu_groups.items():
        # Determine LDUD closure status
        is_billable = False
        doc_label = ''
        doc_status = ''

        material_po = ''
        material_po_options = []

        if src_type == 'VCN' and src_id:
            cur.execute("""
                SELECT lh.doc_status, lh.material_po_number, h.vcn_doc_num, h.vessel_name
                FROM ldud_header lh
                JOIN vcn_header h ON lh.vcn_id = h.id
                WHERE lh.vcn_id = %s
                ORDER BY lh.id DESC LIMIT 1
            """, [src_id])
            row = cur.fetchone()
            if row:
                doc_status = row['doc_status'] or ''
                is_billable = doc_status in ('Closed', 'Partial Close')
                doc_label = f"{row['vcn_doc_num']} / {row['vessel_name']}"
                material_po = row.get('material_po_number') or ''
            else:
                cur.execute("SELECT vcn_doc_num, vessel_name FROM vcn_header WHERE id=%s", [src_id])
                vcn = cur.fetchone()
                doc_label = f"{vcn['vcn_doc_num']} / {vcn['vessel_name']}" if vcn else f"VCN-{src_id}"

        elif src_type == 'MBC' and src_id:
            cur.execute("SELECT doc_num, mbc_name, doc_status FROM mbc_header WHERE id=%s", [src_id])
            row = cur.fetchone()
            if row:
                doc_status = row['doc_status'] or ''
                is_billable = doc_status in ('Closed', 'Partial Close')
                doc_label = f"{row['doc_num']} / {row['mbc_name']}"
            # Get material PO from customer details
            cur.execute("""
                SELECT customer_name, material_po FROM mbc_customer_details
                WHERE mbc_id = %s AND customer_name = %s AND material_po IS NOT NULL AND material_po != ''
            """, [src_id, customer_name])
            mbc_pos = cur.fetchall()
            if len(mbc_pos) == 1:
                material_po = mbc_pos[0]['material_po'] or ''
            elif len(mbc_pos) > 1:
                material_po_options = [{'value': p['material_po'], 'label': p['material_po']} for p in mbc_pos]
                material_po = mbc_pos[0]['material_po'] or ''

        # Split lines by operation_type → service type
        load_lines = [l for l in lines if l.get('operation_type') in ('Loading', 'Export', 'Load')]
        unload_lines = [l for l in lines if l not in load_lines]

        for grp_lines, svc_code in [(load_lines, 'CHGL01'), (unload_lines, 'CHGU01')]:
            if not grp_lines:
                continue
            total_qty = sum(float(l.get('quantity') or 0) for l in grp_lines)
            st = cargo_st_map.get(svc_code, {})
            cargo_handling.append({
                'source_type': src_type,
                'source_id': src_id,
                'doc_label': doc_label,
                'doc_status': doc_status,
                'is_billable': is_billable,
                'service_code': svc_code,
                'service_type_id': st.get('id'),
                'service_name': st.get('service_name', svc_code),
                'sac_code': st.get('sac_code', ''),
                'total_quantity': total_qty,
                'uom': grp_lines[0].get('quantity_uom', 'MT'),
                'lines': grp_lines,
                'material_po': material_po,
                'material_po_options': material_po_options
            })

    # --- B. Other Services: approved unbilled service records for this customer ---
    cur.execute("""
        SELECT sr.*, st.service_name, st.service_code, st.sac_code, st.gst_rate_id
        FROM service_records sr
        JOIN finance_service_types st ON sr.service_type_id = st.id
        WHERE sr.source_type = %s AND sr.source_id = %s
        AND sr.doc_status = 'Approved' AND (sr.is_billed = 0 OR sr.is_billed IS NULL)
        ORDER BY sr.id
    """, [customer_type, customer_id])
    other_services = [dict(r) for r in cur.fetchall()]

    # --- C. Already billed lines for reference ---
    cur.execute("""
        SELECT
            bl.*,
            bh.bill_number,
            bh.bill_date,
            bh.bill_status,
            ca.agreement_code,
            ca.agreement_name,
            NULLIF(
                TRIM(
                    COALESCE(ca.agreement_code, '') ||
                    CASE
                        WHEN COALESCE(ca.agreement_name, '') <> '' THEN ' - ' || ca.agreement_name
                        ELSE ''
                    END
                ),
                ''
            ) AS agreement_display
        FROM bill_lines bl
        JOIN bill_header bh ON bl.bill_id = bh.id
        LEFT JOIN customer_agreements ca ON bh.agreement_id = ca.id
        WHERE bh.customer_type = %s AND bh.customer_id = %s
        ORDER BY bh.id DESC, bl.id
        LIMIT 50
    """, [customer_type, customer_id])
    billed = [dict(r) for r in cur.fetchall()]

    conn.close()

    return jsonify({
        'cargo_handling': cargo_handling,
        'other_services': other_services,
        'billed': billed
    })


@bp.route('/api/module/FIN01/service-records/<customer_type>/<int:customer_id>')
def get_service_records(customer_type, customer_id):
    """Get approved, unbilled service records for a customer/agent"""
    if 'user_id' not in session:
        return jsonify({'error': 'Not logged in'}), 401

    from modules.SRV01 import model as srv_model
    records = srv_model.get_unbilled_records_for_customer(customer_type, customer_id)

    conn = get_db()
    cur = get_cursor(conn)
    for rec in records:
        cur.execute('''
            SELECT sfd.field_label, srv.field_value
            FROM service_record_values srv
            JOIN service_field_definitions sfd ON srv.field_definition_id = sfd.id
            WHERE srv.service_record_id = %s
            ORDER BY sfd.display_order, sfd.id
        ''', [rec['id']])
        rec['field_values'] = [dict(r) for r in cur.fetchall()]
    conn.close()

    return jsonify({'data': records})


@bp.route('/api/module/FIN01/customers/<path:customer_type>')
def get_customers_for_billing(customer_type):
    """Get customers or agents with billing details"""
    if 'user_id' not in session:
        return jsonify({'error': 'Not logged in'}), 401

    conn = get_db()
    cur = get_cursor(conn)
    if customer_type == 'Customer':
        cur.execute('''
            SELECT id, name, gstin, gst_state_code,
                   billing_address, city, pincode, contact_phone, contact_email
            FROM vessel_customers ORDER BY name
        ''')
    elif customer_type == 'Agent':
        cur.execute('''
            SELECT id, name, gstin, gst_state_code,
                   billing_address, city, pincode, contact_phone, contact_email
            FROM vessel_agents WHERE is_active = 1 ORDER BY name
        ''')
    else:
        conn.close()
        return jsonify({'error': 'Invalid customer type'}), 400
    rows = cur.fetchall()
    conn.close()
    return jsonify({'data': [dict(r) for r in rows]})
