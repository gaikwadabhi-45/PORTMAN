"""
SAP DynaportInvoice JSON Payload Builder.

Builds the {"Record_Header": [...]} structure from invoice/credit-note data.

SAP JSON schema:
{
  "Record_Header": [{
    "Invoice_Credit": "I" or "C",
    "Document_type": "INV" or "CRN",
    "Company_code": "5171",
    "Business_place": "5171",
    "Section_code": "5171",
    "Credit_Control_Area": "5171",
    "Plant": "5171",
    "Customer_code": "...",
    "Payment_term": "51",
    "Document_date": "YYYYMMDD",
    "Posting_date": "YYYYMMDD",
    "Base_date": "YYYYMMDD",
    "Header_text": "...",
    "Reference_no": "...",
    "ITEM": [{
      "GL_account": "...",
      "Tax_code": "...",
      "Profit_center": "...",
      "Cost_center": "...",
      "Amount": "...",
      "Item_text": "..."
    }]
  }]
}

Company code logic:
- If the customer has a company_code set (inter-company), use it for
  Company_code / Business_place / Section_code / Credit_Control_Area / Plant.
- Otherwise, fall back to the active SAP config's company_code.
"""
from datetime import datetime
from database import get_db, get_cursor
from modules.SAPCFG.model import get_active_config


def _fmt_date(date_str):
    """Convert 'YYYY-MM-DD' or datetime to 'YYYYMMDD'."""
    if not date_str:
        return datetime.now().strftime('%Y%m%d')
    if isinstance(date_str, datetime):
        return date_str.strftime('%Y%m%d')
    # Strip time portion if present
    date_str = str(date_str)[:10]
    try:
        return datetime.strptime(date_str, '%Y-%m-%d').strftime('%Y%m%d')
    except ValueError:
        return date_str.replace('-', '')


def _fmt_amount(amount):
    """Format amount as string with 2 decimal places."""
    if amount is None:
        return '0.00'
    return f'{float(amount):.2f}'


def _get_customer_company_code(customer_type, customer_id):
    """Look up the customer's company_code for inter-company override."""
    table_map = {
        'Agent': 'vessel_agents',
        'Customer': 'vessel_customers',
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


# ---------------------------------------------------------------------------
# Invoice builder
# ---------------------------------------------------------------------------
def build_invoice_payload(invoice_header, invoice_lines):
    """
    Build DynaportInvoice JSON from an invoice_header dict and list of
    invoice_lines dicts (as returned by FIN01 model functions).

    Returns the complete payload dict ready for sap_client.post_invoice_to_sap().
    """
    config = get_active_config()
    if not config:
        raise ValueError('No active SAP configuration found')

    default_company = config.get('company_code', '5171')
    payment_term = config.get('default_payment_term') or config.get('payment_term') or '51'

    # Inter-company override
    cust_company = _get_customer_company_code(
        invoice_header.get('customer_type'),
        invoice_header.get('customer_id')
    )
    company = cust_company or default_company

    # Build line items
    items = []
    for line in invoice_lines:
        # Base taxable amount item
        items.append({
            'GL_account': line.get('gl_code') or '',
            'Tax_code': line.get('sap_tax_code') or '',
            'Profit_center': line.get('profit_center') or '',
            'Cost_center': line.get('cost_center') or '',
            'Amount': _fmt_amount(line.get('line_amount')),
            'Item_text': (line.get('service_name') or '')[:50],
        })

        # CGST item (if applicable)
        cgst = float(line.get('cgst_amount') or 0)
        if cgst > 0:
            items.append({
                'GL_account': line.get('gl_code') or '',
                'Tax_code': line.get('sap_tax_code') or '',
                'Profit_center': line.get('profit_center') or '',
                'Cost_center': line.get('cost_center') or '',
                'Amount': _fmt_amount(cgst),
                'Item_text': f"CGST @ {line.get('cgst_rate', '')}%",
            })

        # SGST item (if applicable)
        sgst = float(line.get('sgst_amount') or 0)
        if sgst > 0:
            items.append({
                'GL_account': line.get('gl_code') or '',
                'Tax_code': line.get('sap_tax_code') or '',
                'Profit_center': line.get('profit_center') or '',
                'Cost_center': line.get('cost_center') or '',
                'Amount': _fmt_amount(sgst),
                'Item_text': f"SGST @ {line.get('sgst_rate', '')}%",
            })

        # IGST item (if applicable)
        igst = float(line.get('igst_amount') or 0)
        if igst > 0:
            items.append({
                'GL_account': line.get('gl_code') or '',
                'Tax_code': line.get('sap_tax_code') or '',
                'Profit_center': line.get('profit_center') or '',
                'Cost_center': line.get('cost_center') or '',
                'Amount': _fmt_amount(igst),
                'Item_text': f"IGST @ {line.get('igst_rate', '')}%",
            })

    inv_date = _fmt_date(invoice_header.get('invoice_date'))

    header = {
        'Invoice_Credit': 'I',
        'Document_type': 'INV',
        'Company_code': company,
        'Business_place': company,
        'Section_code': company,
        'Credit_Control_Area': company,
        'Plant': company,
        'Customer_code': invoice_header.get('customer_gl_code') or '',
        'Payment_term': payment_term,
        'Document_date': inv_date,
        'Posting_date': inv_date,
        'Base_date': inv_date,
        'Header_text': (invoice_header.get('invoice_number') or '')[:25],
        'Reference_no': (invoice_header.get('invoice_number') or '')[:16],
        'ITEM': items,
    }

    return {'Record_Header': [header]}


# ---------------------------------------------------------------------------
# Credit Note builder
# ---------------------------------------------------------------------------
def build_credit_note_payload(cn_header, cn_lines):
    """
    Build DynaportInvoice JSON for a Credit Note.

    Same structure as invoice but with Invoice_Credit='C' and Document_type='CRN'.
    """
    config = get_active_config()
    if not config:
        raise ValueError('No active SAP configuration found')

    default_company = config.get('company_code', '5171')
    payment_term = config.get('default_payment_term') or config.get('payment_term') or '51'

    cust_company = _get_customer_company_code(
        cn_header.get('customer_type'),
        cn_header.get('customer_id')
    )
    company = cust_company or default_company

    items = []
    for line in cn_lines:
        items.append({
            'GL_account': line.get('gl_code') or '',
            'Tax_code': line.get('sap_tax_code') or '',
            'Profit_center': line.get('profit_center') or '',
            'Cost_center': line.get('cost_center') or '',
            'Amount': _fmt_amount(line.get('line_amount')),
            'Item_text': (line.get('service_name') or '')[:50],
        })

        cgst = float(line.get('cgst_amount') or 0)
        if cgst > 0:
            items.append({
                'GL_account': line.get('gl_code') or '',
                'Tax_code': line.get('sap_tax_code') or '',
                'Profit_center': line.get('profit_center') or '',
                'Cost_center': line.get('cost_center') or '',
                'Amount': _fmt_amount(cgst),
                'Item_text': f"CGST @ {line.get('cgst_rate', '')}%",
            })

        sgst = float(line.get('sgst_amount') or 0)
        if sgst > 0:
            items.append({
                'GL_account': line.get('gl_code') or '',
                'Tax_code': line.get('sap_tax_code') or '',
                'Profit_center': line.get('profit_center') or '',
                'Cost_center': line.get('cost_center') or '',
                'Amount': _fmt_amount(sgst),
                'Item_text': f"SGST @ {line.get('sgst_rate', '')}%",
            })

        igst = float(line.get('igst_amount') or 0)
        if igst > 0:
            items.append({
                'GL_account': line.get('gl_code') or '',
                'Tax_code': line.get('sap_tax_code') or '',
                'Profit_center': line.get('profit_center') or '',
                'Cost_center': line.get('cost_center') or '',
                'Amount': _fmt_amount(igst),
                'Item_text': f"IGST @ {line.get('igst_rate', '')}%",
            })

    cn_date = _fmt_date(cn_header.get('credit_note_date'))

    header = {
        'Invoice_Credit': 'C',
        'Document_type': 'CRN',
        'Company_code': company,
        'Business_place': company,
        'Section_code': company,
        'Credit_Control_Area': company,
        'Plant': company,
        'Customer_code': cn_header.get('customer_gl_code') or '',
        'Payment_term': payment_term,
        'Document_date': cn_date,
        'Posting_date': cn_date,
        'Base_date': cn_date,
        'Header_text': (cn_header.get('credit_note_number') or '')[:25],
        'Reference_no': (cn_header.get('credit_note_number') or '')[:16],
        'ITEM': items,
    }

    return {'Record_Header': [header]}


# ---------------------------------------------------------------------------
# FDCN01 Debit/Credit Note builder
# ---------------------------------------------------------------------------
def build_fdcn_payload(fdcn_header, fdcn_lines):
    """
    Build DynaportInvoice JSON for a Debit Note or Credit Note (FDCN01).

    Debit Note: Invoice_Credit='I', Document_type='DBN'
    Credit Note: Invoice_Credit='C', Document_type='CRN'
    """
    config = get_active_config()
    if not config:
        raise ValueError('No active SAP configuration found')

    default_company = config.get('company_code', '5171')
    payment_term = config.get('default_payment_term') or config.get('payment_term') or '51'

    cust_company = _get_customer_company_code(
        fdcn_header.get('customer_type'),
        fdcn_header.get('customer_id')
    )
    company = cust_company or default_company

    is_debit = fdcn_header.get('doc_type') == 'DN'

    items = []
    for line in fdcn_lines:
        items.append({
            'GL_account': line.get('gl_code') or '',
            'Tax_code': line.get('sap_tax_code') or '',
            'Profit_center': line.get('profit_center') or '',
            'Cost_center': line.get('cost_center') or '',
            'Amount': _fmt_amount(line.get('line_amount')),
            'Item_text': (line.get('service_name') or '')[:50],
        })

        cgst = float(line.get('cgst_amount') or 0)
        if cgst > 0:
            items.append({
                'GL_account': line.get('gl_code') or '',
                'Tax_code': line.get('sap_tax_code') or '',
                'Profit_center': line.get('profit_center') or '',
                'Cost_center': line.get('cost_center') or '',
                'Amount': _fmt_amount(cgst),
                'Item_text': f"CGST @ {line.get('cgst_rate', '')}%",
            })

        sgst = float(line.get('sgst_amount') or 0)
        if sgst > 0:
            items.append({
                'GL_account': line.get('gl_code') or '',
                'Tax_code': line.get('sap_tax_code') or '',
                'Profit_center': line.get('profit_center') or '',
                'Cost_center': line.get('cost_center') or '',
                'Amount': _fmt_amount(sgst),
                'Item_text': f"SGST @ {line.get('sgst_rate', '')}%",
            })

        igst = float(line.get('igst_amount') or 0)
        if igst > 0:
            items.append({
                'GL_account': line.get('gl_code') or '',
                'Tax_code': line.get('sap_tax_code') or '',
                'Profit_center': line.get('profit_center') or '',
                'Cost_center': line.get('cost_center') or '',
                'Amount': _fmt_amount(igst),
                'Item_text': f"IGST @ {line.get('igst_rate', '')}%",
            })

    doc_date = _fmt_date(fdcn_header.get('doc_date'))

    header = {
        'Invoice_Credit': 'I' if is_debit else 'C',
        'Document_type': 'DBN' if is_debit else 'CRN',
        'Company_code': company,
        'Business_place': company,
        'Section_code': company,
        'Credit_Control_Area': company,
        'Plant': company,
        'Customer_code': fdcn_header.get('customer_gl_code') or '',
        'Payment_term': payment_term,
        'Document_date': doc_date,
        'Posting_date': doc_date,
        'Base_date': doc_date,
        'Header_text': (fdcn_header.get('doc_number') or '')[:25],
        'Reference_no': (fdcn_header.get('doc_number') or '')[:16],
        'ITEM': items,
    }

    return {'Record_Header': [header]}


def build_invoice_reversal_payload(invoice_header, invoice_lines):
    """
    Build reversal payload for invoice cancellation.

    SAP interface uses reverse posting payload format:
    Invoice_Credit='C', Document_type='CRN', with original SAP doc in reference.
    """
    reversal_header = {
        'credit_note_date': invoice_header.get('invoice_date'),
        'credit_note_number': f"RV-{invoice_header.get('invoice_number') or ''}",
        'customer_type': invoice_header.get('customer_type'),
        'customer_id': invoice_header.get('customer_id'),
        'customer_gl_code': invoice_header.get('customer_gl_code'),
    }
    payload = build_credit_note_payload(reversal_header, invoice_lines)
    hdr = payload['Record_Header'][0]
    original_ref = invoice_header.get('sap_document_number') or invoice_header.get('invoice_number') or ''
    hdr['Header_text'] = f"REV {original_ref}"[:25]
    hdr['Reference_no'] = original_ref[:16]
    return payload
