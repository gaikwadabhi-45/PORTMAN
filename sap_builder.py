"""
SAP Inbound Interface JSON Payload Builder.
Spec: docs/plans/2026-04-08-sap-inbound-interface-requirements.md  (v1.0)

Header fields (Portbird sends):
  Invoice_Type           I=Invoice/DN/Reversal-of-Invoice, C=Credit Note/Reversal-of-CN
  Company_Code
  Invoice_Date           DD.MM.YYYY
  Posting_Date           DD.MM.YYYY (= Invoice_Date)
  Reference_Text         16 char — PMS doc number; for reversals: original SAP Document_Number
  Document_Type          DR (Invoice/DN) / DG (Credit Note)
  Cancellation_Flag      'X' for reversals, blank otherwise
  Nature_of_transaction  B2B / B2C
  Service_Sale           S=Service, A=Sale
  Customer_Code          10 char
  Invoice_Amount         13 curr (taxable + GST - TDS + TCS, always positive)
  Currency               INR
  Business_Place
  Section_Code
  Payment_Term           4 char
  Baseline_Date          DD.MM.YYYY
  Header_Text            25 char

Line Item fields (not sent for reversals):
  GL_Account         SAP GL account 10 char
  GL_Amount          taxable line amount, always positive 13 curr
  Plant
  Profit_Center
  Text_Description   25 char
  Tax_Code
  IGST_GL            10 char (blank if zero)
  IGST_Amount        blank if zero
  SGST_GL            10 char (blank if zero)
  SGST_Amount        blank if zero
  CGST_GL            10 char (blank if zero)
  CGST_Amount        blank if zero
  HSN_or_SAC_code    16 char
  UOM
  Unit_Price
  Quantity
  TDS_GL
  TDS_Amount         blank if zero
  TCS_GL
  TCS_Amount         blank if zero
  Round_off_GL
  Round_off_Value    ±13 curr (only signed field; blank if zero)

Auto (SAP fills — Portbird does NOT send):
  Processing_Status, Fiscal_Year, Fiscal_Period, Push_Date, Push_Time,
  Document_Number, Message, IRN_Number, Acknowledgement_Number, IRN_Date, QR_Code
"""
from datetime import datetime
from database import get_db, get_cursor
from modules.SAPCFG.model import get_active_config


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------

def _fmt_date(date_str):
    """Convert 'YYYY-MM-DD' or datetime → 'DD.MM.YYYY'."""
    if not date_str:
        return datetime.now().strftime('%d.%m.%Y')
    if isinstance(date_str, datetime):
        return date_str.strftime('%d.%m.%Y')
    date_str = str(date_str)[:10]
    try:
        return datetime.strptime(date_str, '%Y-%m-%d').strftime('%d.%m.%Y')
    except ValueError:
        return date_str


def _fmt_amount(amount):
    """Format amount; empty string if zero/None."""
    if amount is None:
        return ''
    val = float(amount)
    return f'{val:.2f}' if val else ''


def _fmt_amount_required(amount):
    """Format amount — always returns a value (defaults to '0.00')."""
    if amount is None:
        return '0.00'
    return f'{float(amount):.2f}'


# ---------------------------------------------------------------------------
# Lookup helpers
# ---------------------------------------------------------------------------

def _get_customer_company_code(customer_type, customer_id):
    """Inter-company override: return customer's company_code if set."""
    table_map = {
        'Agent':            'vessel_agents',
        'Customer':         'vessel_customers',
        'ImporterExporter': 'vessel_importer_exporters',
    }
    table = table_map.get(customer_type)
    if not table:
        return None
    conn = get_db()
    cur = get_cursor(conn)
    cur.execute(f'SELECT company_code FROM {table} WHERE id = %s', [customer_id])
    row = cur.fetchone()
    conn.close()
    return row['company_code'] if row and row.get('company_code') else None


def _nature_of_transaction(customer_gstin):
    return 'B2B' if customer_gstin and customer_gstin.strip() else 'B2C'


def _get_service_gl_map(service_codes):
    """
    Batch-fetch service GL accounts from finance_service_types by service_code.
    Returns dict keyed by service_code.
    """
    if not service_codes:
        return {}
    conn = get_db()
    cur = get_cursor(conn)
    placeholders = ','.join(['%s'] * len(service_codes))
    cur.execute(f'''
        SELECT service_code, sap_gl_account,
               sap_igst_gl, sap_cgst_gl, sap_sgst_gl,
               sap_tds_gl, sap_tcs_gl,
               service_sale_flag, uom
        FROM finance_service_types
        WHERE service_code IN ({placeholders})
    ''', list(service_codes))
    rows = cur.fetchall()
    conn.close()
    return {r['service_code']: dict(r) for r in rows if r['service_code']}


def _get_service_type_map_by_ids(type_ids):
    """
    Batch-fetch service data from finance_service_types by integer id.
    Used to enrich fdcn_lines (which store service_type_id, not service_code).
    Returns dict keyed by id.
    """
    if not type_ids:
        return {}
    conn = get_db()
    cur = get_cursor(conn)
    placeholders = ','.join(['%s'] * len(type_ids))
    cur.execute(f'''
        SELECT id, service_code, sap_gl_account,
               sap_igst_gl, sap_cgst_gl, sap_sgst_gl,
               sap_tds_gl, sap_tcs_gl,
               service_sale_flag, uom
        FROM finance_service_types
        WHERE id IN ({placeholders})
    ''', list(type_ids))
    rows = cur.fetchall()
    conn.close()
    return {r['id']: dict(r) for r in rows}


def _service_sale_flag(lines, svc_map):
    """Derive header-level SERVICE_SALE from the first line that has a service master entry."""
    for line in lines:
        svc_code = line.get('service_code') or ''
        flag = (
            line.get('service_sale_flag')
            or svc_map.get(svc_code, {}).get('service_sale_flag')
        )
        if flag:
            return flag
    return 'S'


# ---------------------------------------------------------------------------
# Item builder (shared by all document types)
# ---------------------------------------------------------------------------

def _build_items(lines, company, amount_field='line_amount', config_defaults=None, svc_map=None):
    """
    Build the Item list from service lines.

    GL source for IGST/CGST/SGST: service master only (sap_igst_gl / sap_cgst_gl / sap_sgst_gl).
    GL source for TDS/TCS:        service master → SAP config fallback (tds_gl / tcs_gl).
    Plant / Section_Code / Business_Place are header-level only in the new spec;
    Plant is still sent per line as required by SAP PI.
    """
    config_defaults = config_defaults or {}

    # Use pre-fetched svc_map if provided (avoids duplicate DB query from builders)
    if svc_map is None:
        service_codes = {l.get('service_code') for l in lines if l.get('service_code')}
        svc_map = _get_service_gl_map(service_codes)

    items = []
    for line in lines:
        taxable = float(line.get(amount_field) or 0)
        cgst    = float(line.get('cgst_amount') or 0)
        sgst    = float(line.get('sgst_amount') or 0)
        igst    = float(line.get('igst_amount') or 0)

        svc_code = line.get('service_code') or ''
        svc      = svc_map.get(svc_code, {})

        # GL_Account: use sap_gl_account from service master, fall back to service_code
        gl_account = svc.get('sap_gl_account') or svc_code

        plant = line.get('plant') or config_defaults.get('plant_code') or ''

        igst_gl = svc.get('sap_igst_gl') or ''
        cgst_gl = svc.get('sap_cgst_gl') or ''
        sgst_gl = svc.get('sap_sgst_gl') or ''

        uom        = line.get('uom')        or svc.get('uom')        or ''
        unit_price = line.get('unit_price') or line.get('rate')       or ''
        quantity   = line.get('quantity')   or ''

        # TDS/TCS GL: service master → SAP config fallback
        tds_gl       = svc.get('sap_tds_gl') or config_defaults.get('tds_gl') or ''
        tcs_gl       = svc.get('sap_tcs_gl') or config_defaults.get('tcs_gl') or ''
        round_off_gl = config_defaults.get('round_off_gl') or ''

        items.append({
            'GL_Account':       gl_account[:10],
            'GL_Amount':        _fmt_amount_required(taxable),
            'Plant':            plant,
            'Profit_Center':    line.get('profit_center') or config_defaults.get('profit_center') or '',
            'Text_Description': (line.get('service_name') or '')[:25],
            'Tax_Code':         line.get('sap_tax_code') or config_defaults.get('tax_code') or '',
            'IGST_GL':          igst_gl[:10] if igst_gl else '',
            'IGST_Amount':      _fmt_amount(igst),
            'SGST_GL':          sgst_gl[:10] if sgst_gl else '',
            'SGST_Amount':      _fmt_amount(sgst),
            'CGST_GL':          cgst_gl[:10] if cgst_gl else '',
            'CGST_Amount':      _fmt_amount(cgst),
            'HSN_or_SAC_code':  (line.get('sac_code') or line.get('hsn_sac') or '')[:16],
            'UOM':              uom,
            'Unit_Price':       _fmt_amount(float(unit_price)) if unit_price else '',
            'Quantity':         str(quantity) if quantity else '',
            'TDS_GL':           tds_gl,
            'TDS_Amount':       _fmt_amount(line.get('tds_amount')),
            'TCS_GL':           tcs_gl,
            'TCS_Amount':       _fmt_amount(line.get('tcs_amount')),
            'Round_off_GL':     round_off_gl,
            'Round_off_Value':  _fmt_amount(line.get('rounding_off')),
        })
    return items


def _total_invoice_amount(header, lines, amount_field='line_amount'):
    """Return net invoice value (taxable + GST - TDS + TCS)."""
    total = float(header.get('total_amount') or 0)
    if not total:
        total  = sum(float(l.get(amount_field) or 0) for l in lines)
        total += sum(float(l.get('cgst_amount') or 0) for l in lines)
        total += sum(float(l.get('sgst_amount') or 0) for l in lines)
        total += sum(float(l.get('igst_amount') or 0) for l in lines)

    tds = float(header.get('tds_amount') or 0)
    if not tds:
        tds = sum(float(l.get('tds_amount') or 0) for l in lines)
    tcs = float(header.get('tcs_amount') or 0)
    if not tcs:
        tcs = sum(float(l.get('tcs_amount') or 0) for l in lines)

    return total - tds + tcs


# ---------------------------------------------------------------------------
# Invoice builder  (Invoice_Type = I, Document_Type = Y1)
# ---------------------------------------------------------------------------

def build_invoice_payload(invoice_header, invoice_lines):
    config = get_active_config()
    if not config:
        raise ValueError('No active SAP configuration found')

    default_company = config.get('company_code', '5171')
    payment_term    = (config.get('default_payment_term') or config.get('payment_term') or '')[:4]
    business_place  = config.get('business_place') or default_company
    section_code    = config.get('section_code')   or default_company

    cust_company = _get_customer_company_code(
        invoice_header.get('customer_type'),
        invoice_header.get('customer_id'),
    )
    company  = cust_company or default_company
    inv_date = _fmt_date(invoice_header.get('invoice_date'))

    svc_codes = {l.get('service_code') for l in invoice_lines if l.get('service_code')}
    svc_map   = _get_service_gl_map(svc_codes)

    record = {
        'Invoice_Type':          'I',
        'Company_Code':          company,
        'Invoice_Date':          inv_date,
        'Posting_Date':          inv_date,
        'Reference_Text':        (invoice_header.get('invoice_number') or '')[:16],
        'Document_Type':         'DR',
        'Cancellation_Flag':     '',
        'Nature_of_transaction': _nature_of_transaction(invoice_header.get('customer_gstin')),
        'Service_Sale':          _service_sale_flag(invoice_lines, svc_map),
        'Customer_Code':         (invoice_header.get('customer_gl_code') or '')[:10],
        'Invoice_Amount':        _fmt_amount_required(
                                     _total_invoice_amount(invoice_header, invoice_lines)
                                 ),
        'Currency':              invoice_header.get('currency_code') or 'INR',
        'Business_Place':        business_place,
        'Section_Code':          section_code,
        'Payment_Term':          payment_term,
        'Baseline_Date':         inv_date,
        'Header_Text':           (invoice_header.get('invoice_number') or '')[:25],
        'Item':                  _build_items(invoice_lines, company, config_defaults=config, svc_map=svc_map),
    }

    return {'Record': record}


# ---------------------------------------------------------------------------
# FDCN01 Debit / Credit Note builder
# ---------------------------------------------------------------------------

def build_fdcn_payload(fdcn_header, fdcn_lines):
    config = get_active_config()
    if not config:
        raise ValueError('No active SAP configuration found')

    default_company = config.get('company_code', '5171')
    payment_term    = (config.get('default_payment_term') or config.get('payment_term') or '')[:4]
    business_place  = config.get('business_place') or default_company
    section_code    = config.get('section_code')   or default_company

    cust_company = _get_customer_company_code(
        fdcn_header.get('customer_type'),
        fdcn_header.get('customer_id'),
    )
    company  = cust_company or default_company
    doc_date = _fmt_date(fdcn_header.get('doc_date'))
    doc_type = fdcn_header.get('doc_type', 'CN')   # 'DN' or 'CN'

    # DN uses DR (same as invoice); CN uses DG
    sap_doc_type = 'DR' if doc_type == 'DN' else 'DG'
    invoice_type = 'I' if doc_type == 'DN' else 'C'

    irn_date_raw = fdcn_header.get('gst_ack_date') or fdcn_header.get('irn_date')

    # fdcn_lines store service_type_id (integer FK), not service_code (string).
    # Enrich each line with service_code by looking up finance_service_types by id.
    type_ids = {l.get('service_type_id') for l in fdcn_lines if l.get('service_type_id')}
    type_map = _get_service_type_map_by_ids(type_ids)
    enriched_lines = []
    for line in fdcn_lines:
        l = dict(line)
        if not l.get('service_code'):
            tid = l.get('service_type_id')
            svc_type = type_map.get(tid, {})
            # Fall back to gl_code if service_code not found in master
            l['service_code'] = svc_type.get('service_code') or l.get('gl_code') or ''
        enriched_lines.append(l)

    svc_codes = {l.get('service_code') for l in enriched_lines if l.get('service_code')}
    svc_map   = _get_service_gl_map(svc_codes)

    record = {
        'Invoice_Type':          invoice_type,
        'Company_Code':          company,
        'Invoice_Date':          doc_date,
        'Posting_Date':          doc_date,
        'Reference_Text':        (fdcn_header.get('doc_number') or '')[:16],
        'Document_Type':         sap_doc_type,
        'Cancellation_Flag':     '',
        'Nature_of_transaction': _nature_of_transaction(fdcn_header.get('customer_gstin')),
        'Service_Sale':          _service_sale_flag(enriched_lines, svc_map),
        'Customer_Code':         (fdcn_header.get('customer_gl_code') or '')[:10],
        'Invoice_Amount':        _fmt_amount_required(
                                     _total_invoice_amount(fdcn_header, enriched_lines)
                                 ),
        'Currency':              'INR',
        'Business_Place':        business_place,
        'Section_Code':          section_code,
        'Payment_Term':          payment_term,
        'Baseline_Date':         doc_date,
        'Header_Text':           (fdcn_header.get('doc_number') or '')[:25],
        'Original_Invoice_No':   fdcn_header.get('original_invoice_number') or '',
        'Item':                  _build_items(enriched_lines, company, config_defaults=config, svc_map=svc_map),
    }

    return {'Record': record}


# ---------------------------------------------------------------------------
# Invoice reversal builder  (Invoice_Type = I, Cancellation_Flag = 'X')
# ---------------------------------------------------------------------------

def build_invoice_reversal_payload(invoice_header, invoice_lines):
    payload = build_invoice_payload(invoice_header, invoice_lines)

    original_ref = (
        invoice_header.get('sap_document_number')
        or invoice_header.get('invoice_number')
        or ''
    )
    payload['Record']['Reference_Text']    = original_ref[:16]
    payload['Record']['Header_Text']       = f"REV {original_ref}"[:25]
    payload['Record']['Cancellation_Flag'] = 'X'
    # Invoice_Type stays 'I' — it's a reversal of an invoice, not a credit note
    # Spec: reversals send header fields only — no Item array
    payload['Record'].pop('Item', None)

    return payload
