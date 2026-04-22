"""
SAP / PORTBIRD DynaportInvoice JSON Payload Builder.

Spec: PORTBIRD API document (adapter doc from SAP team).

Payload envelope:  { "Record_Header": [ { ...header..., "ITEM": [...] } ] }

Header fields (PORTBIRD spec):
  Invoice_Credit         I=Invoice/DN/Reversal-of-Invoice, C=Credit Note/Reversal-of-CN
  Company_code
  Invoice_date           DD.MM.YYYY
  Posting_Date           DD.MM.YYYY (= Invoice_date)
  Reference              16 char — PMS doc number; for reversals: original SAP Document_Number
  Document_type          INV / CRN / DN (mapped from doc_type)
  Customer_Code          10 char
  Invoice_Amount         13 curr (taxable + GST - TDS + TCS, always positive)
  Business_place
  Section_code
  Text                   short narration (25 char)
  Document_Header_Text   25 char
  Payment_Term           4 char
  Credit_Control_Area    from sap_api_config.credit_control_area
  Cancellation_Flag      'F' for reversals, blank otherwise
  Nature_of_transaction  B2B / B2C
  Service_Sale           S=Service, A=Sale
  Currency               INR
  Payment_term           duplicate per spec (kept for compatibility)
  Baseline_Date          DD.MM.YYYY

Line Item fields (per spec — ITEM array; omitted entirely for reversals):
  Reference           same as header Reference (per-item)
  GL_account          SAP GL account 10 char
  Amount              taxable line amount 13 curr
  Tax_Code
  Cost_Center
  Plant
  Text                25 char description
  Profit_Center
  HSN_SAC             16 char
  CGST_AMT            blank if zero
  SGST_AMT            blank if zero
  IGST_AMT            blank if zero
  IGST_GL             10 char (blank if zero)
  SGST_GL             10 char (blank if zero)
  CGST_GL             10 char (blank if zero)
  UOM
  Unit_Price
  Quantity
  TDS_GL
  TDS_amount          blank if zero
  TCS_GL
  TCS_amount          blank if zero
  Round_off_GL
  Round_off_Value     ±13 curr (only signed field; blank if zero)

Reversal rule: For reversals, Portbird sends ONLY the header fields.
No ITEM array is included in the payload.
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

_CUSTOMER_TABLE_MAP = {
    'Agent':            'vessel_agents',
    'Customer':         'vessel_customers',
    'ImporterExporter': 'vessel_importer_exporters',
}


def _get_customer_sap_info(customer_type, customer_id):
    """
    Fetch both sap_customer_code (for Customer_Code field) and company_code
    (for inter-company override) in a single query.
    """
    table = _CUSTOMER_TABLE_MAP.get(customer_type)
    if not table or not customer_id:
        return {}
    conn = get_db()
    cur = get_cursor(conn)
    cur.execute(
        f'SELECT sap_customer_code, company_code FROM {table} WHERE id = %s',
        [customer_id],
    )
    row = cur.fetchone()
    conn.close()
    return dict(row) if row else {}


def _nature_of_transaction(customer_gstin):
    return 'B2B' if customer_gstin and customer_gstin.strip() else 'B2C'


def _get_service_gl_map(service_codes):
    """Batch-fetch SAP-relevant fields from finance_service_types by service_code."""
    if not service_codes:
        return {}
    conn = get_db()
    cur = get_cursor(conn)
    placeholders = ','.join(['%s'] * len(service_codes))
    cur.execute(f'''
        SELECT service_code, sap_gl_account,
               sap_igst_gl, sap_cgst_gl, sap_sgst_gl,
               sap_tds_gl, sap_tcs_gl,
               sap_tax_code, sap_profit_center, sap_cost_center,
               service_sale_flag, uom
        FROM finance_service_types
        WHERE service_code IN ({placeholders})
    ''', list(service_codes))
    rows = cur.fetchall()
    conn.close()
    return {r['service_code']: dict(r) for r in rows if r['service_code']}


def _get_service_type_map_by_ids(type_ids):
    """Batch-fetch SAP-relevant fields from finance_service_types by integer id."""
    if not type_ids:
        return {}
    conn = get_db()
    cur = get_cursor(conn)
    placeholders = ','.join(['%s'] * len(type_ids))
    cur.execute(f'''
        SELECT id, service_code, sap_gl_account,
               sap_igst_gl, sap_cgst_gl, sap_sgst_gl,
               sap_tds_gl, sap_tcs_gl,
               sap_tax_code, sap_profit_center, sap_cost_center,
               service_sale_flag, uom
        FROM finance_service_types
        WHERE id IN ({placeholders})
    ''', list(type_ids))
    rows = cur.fetchall()
    conn.close()
    return {r['id']: dict(r) for r in rows}


def _service_sale_flag(lines, svc_map):
    """Derive header-level Service_Sale from the first line that has a service master entry."""
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
# Item builder (shared by all non-reversal document types)
# ---------------------------------------------------------------------------

def _build_items(lines, reference, amount_field='line_amount',
                 config_defaults=None, svc_map=None, doc_type='INV'):
    """
    Build the ITEM list per PORTBIRD spec.

    Reference     — per-line, mirrors header Reference (PMS doc number)
    GL sources    — service master (sap_gl_account, sap_igst_gl, sap_cgst_gl,
                    sap_sgst_gl, sap_tds_gl, sap_tcs_gl) with SAP config fallback
                    for TDS/TCS/round-off.
    """
    config_defaults = config_defaults or {}

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

        gl_account = svc.get('sap_gl_account') or svc_code
        plant      = line.get('plant') or config_defaults.get('plant_code') or ''

        igst_gl = svc.get('sap_igst_gl') or ''
        cgst_gl = svc.get('sap_cgst_gl') or ''
        sgst_gl = svc.get('sap_sgst_gl') or ''

        uom        = line.get('uom') or svc.get('uom') or ''
        unit_price = line.get('unit_price') if line.get('unit_price') is not None else line.get('rate')
        quantity   = line.get('quantity')

        tds_gl       = svc.get('sap_tds_gl') or config_defaults.get('tds_gl') or ''
        tcs_gl       = svc.get('sap_tcs_gl') or config_defaults.get('tcs_gl') or ''
        round_off_gl = config_defaults.get('round_off_gl') or ''

        tax_code = (
            line.get('sap_tax_code')
            or svc.get('sap_tax_code')
            or config_defaults.get('tax_code')
            or ''
        )
        profit_center = (
            line.get('profit_center')
            or svc.get('sap_profit_center')
            or config_defaults.get('profit_center')
            or ''
        )
        cost_center = (
            line.get('cost_center')
            or svc.get('sap_cost_center')
            or ''
        )

        # Round-off sign: INV/DN (Debit-side doc) → negate; CRN → keep sign.
        rounding = line.get('rounding_off')
        if rounding and doc_type in ('INV', 'DN'):
            rounding = -float(rounding)

        items.append({
            'Reference':        (reference or '')[:16],
            'GL_account':       gl_account[:10],
            'Amount':           _fmt_amount_required(taxable),
            'Tax_Code':         tax_code,
            'Cost_Center':      cost_center,
            'Plant':            plant,
            'Text':             (line.get('service_name') or '')[:25],
            'Profit_Center':    profit_center,
            'HSN_SAC':          (line.get('sac_code') or line.get('hsn_sac') or '')[:16],
            'CGST_AMT':         _fmt_amount(cgst),
            'SGST_AMT':         _fmt_amount(sgst),
            'IGST_AMT':         _fmt_amount(igst),
            'IGST_GL':          igst_gl[:10] if igst_gl else '',
            'SGST_GL':          sgst_gl[:10] if sgst_gl else '',
            'CGST_GL':          cgst_gl[:10] if cgst_gl else '',
            'UOM':              uom,
            'Unit_Price':       _fmt_amount_required(unit_price) if unit_price is not None else '',
            'Quantity':         f'{float(quantity):.3f}' if quantity is not None else '',
            'TDS_GL':           tds_gl,
            'TDS_amount':       _fmt_amount(line.get('tds_amount')),
            'TCS_GL':           tcs_gl,
            'TCS_amount':       _fmt_amount(line.get('tcs_amount')),
            'Round_off_GL':     round_off_gl,
            'Round_off_Value':  _fmt_amount(rounding),
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
# Common header builder
# ---------------------------------------------------------------------------

def _build_header_base(config, customer_code, company, inv_date,
                       reference, header_text, short_text, currency,
                       invoice_credit, document_type, customer_gstin,
                       service_sale, invoice_amount):
    """Construct the PORTBIRD header dict (shared by invoice/DN/CN/reversal)."""
    payment_term         = (config.get('default_payment_term') or config.get('payment_term') or '')[:4]
    business_place       = config.get('business_place') or company
    section_code         = config.get('section_code')   or company
    credit_control_area  = config.get('credit_control_area') or company

    return {
        'Invoice_Credit':        invoice_credit,
        'Company_code':          company,
        'Invoice_date':          inv_date,
        'Posting_Date':          inv_date,
        'Reference':             (reference or '')[:16],
        'Document_type':         document_type,
        'Customer_Code':         (customer_code or '')[:10],
        'Invoice_Amount':        _fmt_amount_required(invoice_amount),
        'Business_place':        business_place,
        'Section_code':          section_code,
        'Text':                  (short_text or '')[:25],
        'Document_Header_Text':  (header_text or '')[:25],
        'Payment_Term':          payment_term,
        'Credit_Control_Area':   credit_control_area,
        'Cancellation_Flag':     '',
        'Nature_of_transaction': _nature_of_transaction(customer_gstin),
        'Service_Sale':          service_sale,
        'Currency':              currency or 'INR',
        'Payment_term':          payment_term,
        'Baseline_Date':         inv_date,
    }


# ---------------------------------------------------------------------------
# Invoice builder  (Invoice_Credit = I, Document_type = INV)
# ---------------------------------------------------------------------------

def build_invoice_payload(invoice_header, invoice_lines):
    config = get_active_config()
    if not config:
        raise ValueError('No active SAP configuration found')

    default_company = config.get('company_code', '5171')

    cust_info = _get_customer_sap_info(
        invoice_header.get('customer_type'),
        invoice_header.get('customer_id'),
    )
    company       = cust_info.get('company_code') or default_company
    customer_code = cust_info.get('sap_customer_code') or invoice_header.get('customer_gl_code') or ''
    inv_date      = _fmt_date(invoice_header.get('invoice_date'))
    invoice_no    = invoice_header.get('invoice_number') or ''

    svc_codes = {l.get('service_code') for l in invoice_lines if l.get('service_code')}
    svc_map   = _get_service_gl_map(svc_codes)

    record = _build_header_base(
        config=config,
        customer_code=customer_code,
        company=company,
        inv_date=inv_date,
        reference=invoice_no,
        header_text=invoice_no,
        short_text=invoice_no,
        currency=invoice_header.get('currency_code'),
        invoice_credit='I',
        document_type='INV',
        customer_gstin=invoice_header.get('customer_gstin'),
        service_sale=_service_sale_flag(invoice_lines, svc_map),
        invoice_amount=_total_invoice_amount(invoice_header, invoice_lines),
    )
    record['ITEM'] = _build_items(
        invoice_lines, invoice_no,
        config_defaults=config, svc_map=svc_map, doc_type='INV',
    )
    return {'Record_Header': [record]}


# ---------------------------------------------------------------------------
# FDCN01 Debit / Credit Note builder
# ---------------------------------------------------------------------------

def build_fdcn_payload(fdcn_header, fdcn_lines):
    config = get_active_config()
    if not config:
        raise ValueError('No active SAP configuration found')

    default_company = config.get('company_code', '5171')

    cust_info = _get_customer_sap_info(
        fdcn_header.get('customer_type'),
        fdcn_header.get('customer_id'),
    )
    company       = cust_info.get('company_code') or default_company
    customer_code = cust_info.get('sap_customer_code') or fdcn_header.get('customer_gl_code') or ''
    doc_date      = _fmt_date(fdcn_header.get('doc_date'))
    doc_number    = fdcn_header.get('doc_number') or ''
    doc_type      = fdcn_header.get('doc_type', 'CN')   # 'DN' or 'CN'

    # PORTBIRD Document_type: DN → 'DN', CN → 'CRN'
    # Invoice_Credit: DN → 'I' (adds to receivable), CN → 'C' (reduces receivable)
    document_type  = 'DN' if doc_type == 'DN' else 'CRN'
    invoice_credit = 'I' if doc_type == 'DN' else 'C'

    # fdcn_lines store service_type_id (integer FK), not service_code (string).
    type_ids = {l.get('service_type_id') for l in fdcn_lines if l.get('service_type_id')}
    type_map = _get_service_type_map_by_ids(type_ids)
    enriched_lines = []
    for line in fdcn_lines:
        l = dict(line)
        if not l.get('service_code'):
            tid = l.get('service_type_id')
            svc_type = type_map.get(tid, {})
            l['service_code'] = svc_type.get('service_code') or l.get('gl_code') or ''
        enriched_lines.append(l)

    svc_codes = {l.get('service_code') for l in enriched_lines if l.get('service_code')}
    svc_map   = _get_service_gl_map(svc_codes)

    record = _build_header_base(
        config=config,
        customer_code=customer_code,
        company=company,
        inv_date=doc_date,
        reference=doc_number,
        header_text=doc_number,
        short_text=doc_number,
        currency='INR',
        invoice_credit=invoice_credit,
        document_type=document_type,
        customer_gstin=fdcn_header.get('customer_gstin'),
        service_sale=_service_sale_flag(enriched_lines, svc_map),
        invoice_amount=_total_invoice_amount(fdcn_header, enriched_lines),
    )
    record['ITEM'] = _build_items(
        enriched_lines, doc_number,
        config_defaults=config, svc_map=svc_map, doc_type=doc_type,
    )
    return {'Record_Header': [record]}


# ---------------------------------------------------------------------------
# Invoice reversal builder  (Cancellation_Flag = 'F', no ITEM array)
# ---------------------------------------------------------------------------

def build_invoice_reversal_payload(invoice_header, invoice_lines):
    """
    Per PORTBIRD spec: reversals send ONLY header fields — no ITEM array.
    Reference is the original SAP Document_Number (not the PMS invoice number).
    """
    config = get_active_config()
    if not config:
        raise ValueError('No active SAP configuration found')

    default_company = config.get('company_code', '5171')

    cust_info = _get_customer_sap_info(
        invoice_header.get('customer_type'),
        invoice_header.get('customer_id'),
    )
    company       = cust_info.get('company_code') or default_company
    customer_code = cust_info.get('sap_customer_code') or invoice_header.get('customer_gl_code') or ''
    inv_date      = _fmt_date(invoice_header.get('invoice_date'))

    original_ref = (
        invoice_header.get('sap_document_number')
        or invoice_header.get('invoice_number')
        or ''
    )

    svc_codes = {l.get('service_code') for l in invoice_lines if l.get('service_code')}
    svc_map   = _get_service_gl_map(svc_codes)

    record = _build_header_base(
        config=config,
        customer_code=customer_code,
        company=company,
        inv_date=inv_date,
        reference=original_ref,
        header_text=original_ref,
        short_text=original_ref,
        currency=invoice_header.get('currency_code'),
        invoice_credit='I',
        document_type='INV',
        customer_gstin=invoice_header.get('customer_gstin'),
        service_sale=_service_sale_flag(invoice_lines, svc_map),
        invoice_amount=_total_invoice_amount(invoice_header, invoice_lines),
    )
    record['Cancellation_Flag'] = 'F'
    # NOTE: no ITEM array for reversals per PORTBIRD spec
    return {'Record_Header': [record]}
