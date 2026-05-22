"""
Reset all bills and invoices, and optionally delete all EU (LUEU01) lines permanently.

By default (no flags): deletes all billing data and resets declaration tables to unbilled state.
With --delete-eu:      also permanently deletes all lueu_lines rows after clearing billing.

Usage: python reset_billing.py
       python reset_billing.py --yes            (skip confirmation)
       python reset_billing.py --delete-eu      (also wipe all EU lines)
       python reset_billing.py --delete-eu --yes
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from database import get_db, get_cursor


def reset_billing(skip_confirm=False, delete_eu=False):
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
        'cutover_seed': 'Cutover Seeds',
        'cutover_audit': 'Cutover Audit Entries',
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

    # Count billed declaration rows and service records
    for tbl, label in [
        ('vcn_cargo_declaration',        'VCN Import Decls (billed)'),
        ('vcn_export_cargo_declaration', 'VCN Export Decls (billed)'),
        ('mbc_customer_details',         'MBC Customer Details (billed)'),
    ]:
        try:
            cur.execute(f'SELECT COUNT(*) as cnt FROM {tbl} WHERE COALESCE(is_billed, 0) = 1 OR bill_id IS NOT NULL OR COALESCE(billed_quantity, 0) > 0')
            print(f'  {label:30s}: {cur.fetchone()["cnt"]}')
        except Exception:
            conn.rollback()
            print(f'  {label:30s}: (table not found)')

    try:
        cur.execute('SELECT COUNT(*) as cnt FROM lueu_lines')
        print(f'  {"EU Lines (total)":30s}: {cur.fetchone()["cnt"]}')
    except Exception:
        conn.rollback()
        print(f'  {"EU Lines (total)":30s}: (table not found)')

    try:
        cur.execute('SELECT COUNT(*) as cnt FROM service_records WHERE is_billed = 1 OR bill_id IS NOT NULL')
        srv_billed = cur.fetchone()['cnt']
        print(f'  {"Service Records (billed)":30s}: {srv_billed}')
    except Exception:
        conn.rollback()
        srv_billed = 0

    if not skip_confirm:
        print('\nThis will DELETE all bills, invoices, debit/credit notes, and cutover settings.')
        if delete_eu:
            print('EU lines will be PERMANENTLY DELETED (cannot be undone).')
        else:
            print('Declaration tables and service records will be reset to unbilled state.')
            print('Operations data (LUEU, VCN, LDUD, MBC) will NOT be touched.')
        resp = input('\nType YES to confirm: ')
        if resp.strip() != 'YES':
            print('Aborted.')
            return

    print('\n=== Resetting Billing Data ===')

    # Step 1: Reset cargo declaration tables to unbilled
    # Each table uses a savepoint so a failure on one doesn't roll back the others.
    for tbl in ('vcn_cargo_declaration', 'vcn_export_cargo_declaration', 'mbc_customer_details'):
        try:
            cur.execute(f'SAVEPOINT sp_{tbl}')
            cur.execute(f'''
                UPDATE {tbl}
                SET is_billed = 0,
                    bill_id = NULL,
                    billed_quantity = 0
                WHERE COALESCE(is_billed, 0) = 1 OR bill_id IS NOT NULL OR COALESCE(billed_quantity, 0) > 0
            ''')
            print(f'  Reset {cur.rowcount} rows in {tbl} to unbilled')
        except Exception as e:
            cur.execute(f'ROLLBACK TO SAVEPOINT sp_{tbl}')
            print(f'  ({tbl} reset failed: {e})')

    # Step 2: Reset service_records to unbilled
    try:
        cur.execute('SAVEPOINT sp_service_records')
        cur.execute('''
            UPDATE service_records
            SET is_billed = 0,
                bill_id = NULL
            WHERE is_billed = 1 OR bill_id IS NOT NULL
        ''')
        print(f'  Reset {cur.rowcount} service records to unbilled')
    except Exception:
        cur.execute('ROLLBACK TO SAVEPOINT sp_service_records')
        print('  (service_records table not found, skipping)')

    # Step 3a: Reset cutover settings
    for tbl in ['cutover_audit', 'cutover_seed']:
        try:
            cur.execute(f'SAVEPOINT sp_{tbl}')
            cur.execute(f'DELETE FROM {tbl}')
            print(f'  Deleted {cur.rowcount} rows from {tbl}')
        except Exception as e:
            cur.execute(f'ROLLBACK TO SAVEPOINT sp_{tbl}')
            print(f'  ({tbl} not found, skipping)')

    try:
        cur.execute("SAVEPOINT sp_cutover_lock")
        cur.execute("""
            UPDATE module_config
            SET config_json = (config_json::jsonb - 'cutover_locked')::text
            WHERE module_code = 'ADMIN'
              AND config_json::jsonb ? 'cutover_locked'
        """)
        if cur.rowcount:
            print('  Cleared cutover_locked flag from ADMIN module config')
    except Exception as e:
        cur.execute("ROLLBACK TO SAVEPOINT sp_cutover_lock")
        print(f'  (cutover_locked reset failed: {e})')

    # Step 3b: Delete debit/credit notes (child tables cascade)
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

    # Step 5b: Permanently delete all EU lines (only when --delete-eu is passed)
    if delete_eu:
        cur.execute('DELETE FROM lueu_lines')
        deleted_eu = cur.rowcount
        print(f'  Permanently deleted {deleted_eu} rows from lueu_lines')

    conn.commit()

    # Step 6: Reset sequences (each in its own try so a missing seq doesn't rollback data)
    print('\n=== Resetting Sequences ===')
    sequences = [
        'bill_header_id_seq',
        'bill_lines_id_seq',
        'invoice_header_id_seq',
        'invoice_lines_id_seq',
        'invoice_bill_mapping_id_seq',
        'fdcn_header_id_seq',
        'fdcn_lines_id_seq',
        'cutover_seed_id_seq',
        'cutover_audit_id_seq',
    ]
    if delete_eu:
        # Table may have been created as eu_lines originally; try both sequence names
        sequences += ['lueu_lines_id_seq', 'eu_lines_id_seq']

    for seq in sequences:
        try:
            cur.execute(f"SELECT setval('{seq}', 1, false)")
            conn.commit()
            print(f'  {seq} reset to 1')
        except Exception:
            conn.rollback()
            print(f'  ({seq} not found, skipping)')

    conn.close()

    print('\n=== Done ===')
    if delete_eu:
        print('All bills, invoices, EU lines, and cutover settings permanently deleted.')
    else:
        print('All bills, invoices, and cutover settings deleted. Declaration tables and service records restored to unbilled state.')
    print('Sequences reset - next bill/invoice will start from ID 1.')


if __name__ == '__main__':
    skip = '--yes' in sys.argv
    delete_eu = '--delete-eu' in sys.argv
    reset_billing(skip_confirm=skip, delete_eu=delete_eu)
