"""
SAP DynaportInvoice JSON Payload Builder.

Builds the payload that mirrors the SAP PI/PO XML interface:
  - MT_INV_IPORTMANtoECC_Req   → Invoices     (Document_Type = Y1)
  - MT_CNDN_IPORTMANtoECC_Req  → CN / DN      (Document_Type = Y2)

JSON structure (mirrors XML field-for-field):
{
  "Record": {
    "Company_Code":          "5000",
    "Document_Date":         "24.04.2025",     ← DD.MM.YYYY
    "Posting_Date":          "24.04.2025",
    "Document_Type":         "Y1" or "Y2",
    "Reference_Text":        "INV/001",
    "Doc_Header_Text":       "INV/001",
    "Currency":              "INR",
    "Customer_Code":         "5100001",
    "Invoice_Amount":        "118000.00",      ← total incl. GST
    "IRN_No":                "",
    "Ack_No":                "",
    "IRN_Date":              "",
    "Nature_of_transaction": "B2B" or "B2C",
    "Cancellation_Flag":     "",               ← "X" for reversals
    "TDS_Amount":            "500.00",         ← header-level total TDS
    "TCS_Amount":            "",               ← header-level total TCS
    "Item": [
      {
        "Service_Code":   "OT0051",
        "CGST_AMT":       "4500.00",
        "SGST_AMT":       "4500.00",
        "IGST_AMT":       "",
        "Amount":         "50000.00",          ← taxable amount
        "Text":           "MOORING CHARGES",
        "Plant":          "5001",
        "Business_Place": "5001",
        "Section_Code":   "5001",
        "Tax_Code":       "50",
        "Profit_Center":  "500000",
        "HSN_SAC":        "996759",
        "TDS_Amount":     "",
        "TCS_Amount":     "",
        "Rounding_off":   ""
      }
    ]
  }
}

Company code logic:
  If customer has a company_code set (inter-company), that value overrides
  Company_Code / Plant / Business_Place / Section_Code.
  Otherwise falls back to active SAP config company_code.
"""
from datetime import datetime
from database import get_db, get_cursor
from modules.SAPCFG.model import get_active_config


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------

def _fmt_date(date_str):
    """Convert 'YYYY-MM-DD' or datetime → 'DD.MM.YYYY' (SAP format)."""
    if not date_str:
        return datetime.now().strftime('%d.%m.%Y')
    if isinstance(date_str, datetime):
        return date_str.strftime('%d.%m.%Y')
    date_str = str(date_str)[:10]
    try:
        return datetime.strptime(date_str, '%Y-%m-%d').strftime('%d.%m.%Y')
    except ValueError:
        return date_str  # return as-is if unparseable


def _fmt_amount(amount):
    """Format amount as string with 2 decimal places; empty string if zero/None."""
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
        'Agent':             'vessel_agents',
        'Customer':          'vessel_customers',
        'ImporterExporter':  'vessel_importer_exporters',
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
    """B2B if customer has a GSTIN, else B2C."""
    return 'B2B' if customer_gstin and customer_gstin.strip() else 'B2C'


# ---------------------------------------------------------------------------
# Item builder (shared by all document types)
# ---------------------------------------------------------------------------

def _build_items(lines, company, amount_field='line_amount', config_defaults=None):
    """
    Build the Item list from service lines.

    Each line → one Item with CGST_AMT / SGST_AMT / IGST_AMT inline.
    Plant, Business_Place, Section_Code come from the line (if set) or
    fall back to SAP config defaults, then company code.
    """
    config_defaults = config_defaults or {}
    items = []
    for line in lines:
        taxable = float(line.get(amount_field) or 0)
        cgst    = float(line.get('cgst_amount') or 0)
        sgst    = float(line.get('sgst_amount') or 0)
        igst    = float(line.get('igst_amount') or 0)

        plant   = line.get('plant')          or config_defaults.get('plant_code')          or company
        bp      = line.get('business_place') or config_defaults.get('business_place')      or company
        sc      = line.get('section_code')   or config_defaults.get('section_code')        or company

        items.append({
            'Service_Code':   line.get('service_code') or line.get('gl_code') or '',
            'CGST_AMT':       _fmt_amount(cgst),
            'SGST_AMT':       _fmt_amount(sgst),
            'IGST_AMT':       _fmt_amount(igst),
            'Amount':         _fmt_amount_required(taxable),
            'Text':           (line.get('service_name') or '')[:50],
            'Plant':          plant,
            'Business_Place': bp,
            'Section_Code':   sc,
            'Tax_Code':       line.get('sap_tax_code') or config_defaults.get('tax_code') or '',
            'Profit_Center':  line.get('profit_center') or config_defaults.get('profit_center') or '',
            'HSN_SAC':        line.get('sac_code') or line.get('hsn_sac') or '',
            'TDS_Amount':     _fmt_amount(line.get('tds_amount')),
            'TCS_Amount':     _fmt_amount(line.get('tcs_amount')),
            'Rounding_off':   _fmt_amount(line.get('rounding_off')),
        })
    return items


def _total_invoice_amount(header, lines, amount_field='line_amount'):
    """Return net invoice value (taxable + GST - TDS + TCS)."""
    total = float(header.get('total_amount') or 0)
    if not total:
        # Sum from lines
        total = sum(float(l.get(amount_field) or 0) for l in lines)
        total += sum(float(l.get('cgst_amount') or 0) for l in lines)
        total += sum(float(l.get('sgst_amount') or 0) for l in lines)
        total += sum(float(l.get('igst_amount') or 0) for l in lines)

    # Adjust for TDS (deducted) and TCS (collected)
    tds = float(header.get('tds_amount') or 0)
    if not tds:
        tds = sum(float(l.get('tds_amount') or 0) for l in lines)
    tcs = float(header.get('tcs_amount') or 0)
    if not tcs:
        tcs = sum(float(l.get('tcs_amount') or 0) for l in lines)

    return total - tds + tcs


# ---------------------------------------------------------------------------
# Invoice builder  (Y1)
# ---------------------------------------------------------------------------

def build_invoice_payload(invoice_header, invoice_lines):
    """
    Build MT_INV_IPORTMANtoECC_Req payload from an invoice_header dict
    and list of invoice_lines dicts.
    """
    config = get_active_config()
    if not config:
        raise ValueError('No active SAP configuration found')

    default_company = config.get('company_code', '5171')
    payment_term    = config.get('default_payment_term') or config.get('payment_term') or ''

    cust_company = _get_customer_company_code(
        invoice_header.get('customer_type'),
        invoice_header.get('customer_id'),
    )
    company  = cust_company or default_company
    inv_date = _fmt_date(invoice_header.get('invoice_date'))

    record = {
        'Company_Code':          company,
        'Document_Date':         inv_date,
        'Posting_Date':          inv_date,
        'Document_Type':         'Y1',
        'Reference_Text':        (invoice_header.get('invoice_number') or '')[:16],
        'Doc_Header_Text':       (invoice_header.get('invoice_number') or '')[:25],
        'Currency':              invoice_header.get('currency_code') or 'INR',
        'Customer_Code':         invoice_header.get('customer_gl_code') or '',
        'Payment_Term':          payment_term,
        'Baseline_Date':         inv_date,
        'Invoice_Amount':        _fmt_amount_required(
                                     _total_invoice_amount(invoice_header, invoice_lines)
                                 ),
        'IRN_No':                invoice_header.get('irn') or '',
        'Ack_No':                str(invoice_header.get('ack_number') or ''),
        'IRN_Date':              _fmt_date(invoice_header.get('irn_date')) if invoice_header.get('irn_date') else '',
        'Nature_of_transaction': _nature_of_transaction(invoice_header.get('customer_gstin')),
        'Cancellation_Flag':     '',
        'TDS_Amount':            _fmt_amount(invoice_header.get('tds_amount')),
        'TCS_Amount':            _fmt_amount(invoice_header.get('tcs_amount')),
        'Item':                  _build_items(invoice_lines, company, config_defaults=config),
    }

    return {'Record': record}


# ---------------------------------------------------------------------------
# Credit Note builder  (Y2)
# ---------------------------------------------------------------------------

def build_credit_note_payload(cn_header, cn_lines):
    """
    Build MT_CNDN_IPORTMANtoECC_Req payload for a Credit Note.
    Document_Type = Y2.
    """
    config = get_active_config()
    if not config:
        raise ValueError('No active SAP configuration found')

    default_company = config.get('company_code', '5171')
    payment_term    = config.get('default_payment_term') or config.get('payment_term') or ''

    cust_company = _get_customer_company_code(
        cn_header.get('customer_type'),
        cn_header.get('customer_id'),
    )
    company = cust_company or default_company
    cn_date = _fmt_date(cn_header.get('credit_note_date'))

    record = {
        'Company_Code':          company,
        'Document_Date':         cn_date,
        'Posting_Date':          cn_date,
        'Document_Type':         'Y2',
        'Reference_Text':        (cn_header.get('credit_note_number') or '')[:16],
        'Doc_Header_Text':       (cn_header.get('credit_note_number') or '')[:25],
        'Currency':              cn_header.get('currency_code') or 'INR',
        'Customer_Code':         cn_header.get('customer_gl_code') or '',
        'Payment_Term':          payment_term,
        'Baseline_Date':         cn_date,
        'Invoice_Amount':        _fmt_amount_required(
                                     _total_invoice_amount(cn_header, cn_lines)
                                 ),
        'IRN_No':                cn_header.get('irn') or '',
        'Ack_No':                str(cn_header.get('ack_number') or ''),
        'IRN_Date':              _fmt_date(cn_header.get('irn_date')) if cn_header.get('irn_date') else '',
        'Nature_of_transaction': _nature_of_transaction(cn_header.get('customer_gstin')),
        'Cancellation_Flag':     '',
        'TDS_Amount':            _fmt_amount(cn_header.get('tds_amount')),
        'TCS_Amount':            _fmt_amount(cn_header.get('tcs_amount')),
        'Item':                  _build_items(cn_lines, company, config_defaults=config),
    }

    return {'Record': record}


# ---------------------------------------------------------------------------
# FDCN01 Debit / Credit Note builder  (Y2)
# ---------------------------------------------------------------------------

def build_fdcn_payload(fdcn_header, fdcn_lines):
    """
    Build SAP payload for a Debit Note or Credit Note from FDCN01.

    DN  → Document_Type = Y1  (same interface as invoice, MT_INV_IPORTMANtoECC_Req)
    CN  → Document_Type = Y2  (MT_CNDN_IPORTMANtoECC_Req)

    Both carry Original_Invoice_No so SAP can link back to the source invoice.
    IRN fields read from fdcn_header columns: gst_irn, gst_ack_number, gst_ack_date.
    """
    config = get_active_config()
    if not config:
        raise ValueError('No active SAP configuration found')

    default_company = config.get('company_code', '5171')
    payment_term    = config.get('default_payment_term') or config.get('payment_term') or ''

    cust_company = _get_customer_company_code(
        fdcn_header.get('customer_type'),
        fdcn_header.get('customer_id'),
    )
    company  = cust_company or default_company
    doc_date = _fmt_date(fdcn_header.get('doc_date'))
    doc_type = fdcn_header.get('doc_type', 'CN')   # 'DN' or 'CN'

    # DN uses Y1 (same document type as invoice); CN uses Y2
    sap_doc_type = 'Y1' if doc_type == 'DN' else 'Y2'

    irn_date_raw = fdcn_header.get('gst_ack_date') or fdcn_header.get('irn_date')

    record = {
        'Company_Code':          company,
        'Document_Date':         doc_date,
        'Posting_Date':          doc_date,
        'Document_Type':         sap_doc_type,
        'Reference_Text':        (fdcn_header.get('doc_number') or '')[:16],
        'Doc_Header_Text':       (fdcn_header.get('doc_number') or '')[:25],
        'Currency':              'INR',
        'Customer_Code':         fdcn_header.get('customer_gl_code') or '',
        'Payment_Term':          payment_term,
        'Baseline_Date':         doc_date,
        'Invoice_Amount':        _fmt_amount_required(
                                     _total_invoice_amount(fdcn_header, fdcn_lines)
                                 ),
        'IRN_No':                fdcn_header.get('gst_irn') or '',
        'Ack_No':                str(fdcn_header.get('gst_ack_number') or ''),
        'IRN_Date':              _fmt_date(irn_date_raw) if irn_date_raw else '',
        'Nature_of_transaction': _nature_of_transaction(fdcn_header.get('customer_gstin')),
        'Cancellation_Flag':     '',
        'Original_Invoice_No':   fdcn_header.get('original_invoice_number') or '',
        'TDS_Amount':            _fmt_amount(fdcn_header.get('tds_amount')),
        'TCS_Amount':            _fmt_amount(fdcn_header.get('tcs_amount')),
        'Item':                  _build_items(fdcn_lines, company, config_defaults=config),
    }

    return {'Record': record}


# ---------------------------------------------------------------------------
# Invoice reversal builder  (Y1 with Cancellation_Flag = 'X')
# ---------------------------------------------------------------------------

def build_invoice_reversal_payload(invoice_header, invoice_lines):
    """
    Build reversal payload for invoice cancellation.

    Same staging table as invoice (Y1) but with Cancellation_Flag = 'X'.
    SAP handles the actual reversal logic on their side.
    """
    payload = build_invoice_payload(invoice_header, invoice_lines)

    original_ref = (
        invoice_header.get('sap_document_number')
        or invoice_header.get('invoice_number')
        or ''
    )
    payload['Record']['Reference_Text']      = original_ref[:16]
    payload['Record']['Doc_Header_Text']     = f"REV {original_ref}"[:25]
    payload['Record']['Cancellation_Flag']   = 'X'

    return payload
