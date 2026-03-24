"""
Reset all bills and invoices, restoring EU lines and service records to unbilled state.
Does NOT touch operations data (LUEU lines, VCN, LDUD, MBC, etc.).

Usage: python reset_billing.py
       python reset_billing.py --yes   (skip confirmation)
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from database import get_db, get_cursor


def reset_billing(skip_confirm=False):
    conn = get_db()
    cur = get_cursor(conn)

    # Show current counts
    tables = {
        'invoice_header': 'Invoices',
        'invoice_lines': 'Invoice Lines',
        'invoice_bill_mapping': 'Invoice-Bill Mappings',
        'bill_header': 'Bills',
        'bill_lines': 'Bill Lines',
        'fdcn_header': 'Debit/Credit Notes',
        'fdcn_lines': 'Debit/Credit Note Lines',
    }

    print('\n=== Current Record Counts ===')
    for tbl, label in tables.items():
        try:
            cur.execute(f'SELECT COUNT(*) as cnt FROM {tbl}')
            cnt = cur.fetchone()['cnt']
            print(f'  {label:30s}: {cnt}')
        except Exception:
            conn.rollback()
            print(f'  {label:30s}: (table not found)')

    # Count affected EU lines and service records
    cur.execute('SELECT COUNT(*) as cnt FROM lueu_lines WHERE is_billed = 1 OR bill_id IS NOT NULL OR COALESCE(billed_quantity, 0) > 0')
    eu_billed = cur.fetchone()['cnt']
    print(f'  {"EU Lines (billed)":30s}: {eu_billed}')

    try:
        cur.execute('SELECT COUNT(*) as cnt FROM service_records WHERE is_billed = 1 OR bill_id IS NOT NULL')
        srv_billed = cur.fetchone()['cnt']
        print(f'  {"Service Records (billed)":30s}: {srv_billed}')
    except Exception:
        conn.rollback()
        srv_billed = 0

    if not skip_confirm:
        print('\nThis will DELETE all bills, invoices, and debit/credit notes.')
        print('EU lines and service records will be reset to unbilled state.')
        print('Operations data (LUEU, VCN, LDUD, MBC) will NOT be touched.')
        resp = input('\nType YES to confirm: ')
        if resp.strip() != 'YES':
            print('Aborted.')
            return

    print('\n=== Resetting Billing Data ===')

    # Step 1: Reset lueu_lines to unbilled
    cur.execute('''
        UPDATE lueu_lines
        SET is_billed = 0,
            bill_id = NULL,
            billed_quantity = 0
        WHERE is_billed = 1 OR bill_id IS NOT NULL OR COALESCE(billed_quantity, 0) > 0
    ''')
    print(f'  Reset {cur.rowcount} EU lines to unbilled')

    # Step 2: Reset service_records to unbilled
    try:
        cur.execute('''
            UPDATE service_records
            SET is_billed = 0,
                bill_id = NULL
            WHERE is_billed = 1 OR bill_id IS NOT NULL
        ''')
        print(f'  Reset {cur.rowcount} service records to unbilled')
    except Exception:
        conn.rollback()
        print('  (service_records table not found, skipping)')

    # Step 3: Delete debit/credit notes (child tables cascade)
    for tbl in ['fdcn_lines', 'fdcn_header']:
        try:
            cur.execute(f'DELETE FROM {tbl}')
            print(f'  Deleted {cur.rowcount} rows from {tbl}')
        except Exception:
            conn.rollback()
            print(f'  ({tbl} not found, skipping)')

    # Step 4: Delete invoice data (order matters for FK constraints)
    cur.execute('DELETE FROM invoice_lines')
    print(f'  Deleted {cur.rowcount} rows from invoice_lines')

    cur.execute('DELETE FROM invoice_bill_mapping')
    print(f'  Deleted {cur.rowcount} rows from invoice_bill_mapping')

    cur.execute('DELETE FROM invoice_header')
    print(f'  Deleted {cur.rowcount} rows from invoice_header')

    # Step 5: Delete bill data
    cur.execute('DELETE FROM bill_lines')
    print(f'  Deleted {cur.rowcount} rows from bill_lines')

    cur.execute('DELETE FROM bill_header')
    print(f'  Deleted {cur.rowcount} rows from bill_header')

    # Step 6: Reset sequences
    sequences = {
        'bill_header_id_seq': 1,
        'bill_lines_id_seq': 1,
        'invoice_header_id_seq': 1,
        'invoice_lines_id_seq': 1,
        'invoice_bill_mapping_id_seq': 1,
        'fdcn_header_id_seq': 1,
        'fdcn_lines_id_seq': 1,
    }
    print('\n=== Resetting Sequences ===')
    for seq, val in sequences.items():
        try:
            cur.execute(f"SELECT setval('{seq}', {val}, false)")
            print(f'  {seq} reset to {val}')
        except Exception:
            conn.rollback()
            print(f'  ({seq} not found, skipping)')

    conn.commit()
    conn.close()

    print('\n=== Done ===')
    print('All bills and invoices deleted. EU lines and service records restored to unbilled state.')
    print('Sequences reset - next bill/invoice will start from ID 1.')


if __name__ == '__main__':
    skip = '--yes' in sys.argv
    reset_billing(skip_confirm=skip)
