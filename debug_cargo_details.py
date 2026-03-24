"""
Diagnostic script for cargo handling details.
Run on production: python debug_cargo_details.py <invoice_id>
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from database import get_db, get_cursor

def debug(invoice_id):
    conn = get_db()
    cur = get_cursor(conn)

    print(f'\n{"="*80}')
    print(f'DEBUGGING CARGO DETAILS FOR INVOICE ID: {invoice_id}')
    print(f'{"="*80}')

    # Step 1: Invoice exists?
    cur.execute('SELECT id, invoice_number FROM invoice_header WHERE id = %s', [invoice_id])
    inv = cur.fetchone()
    if not inv:
        print(f'[FAIL] Invoice {invoice_id} NOT FOUND in invoice_header')
        return
    print(f'\n[1] Invoice: id={inv["id"]}, number={inv["invoice_number"]}')

    # Step 2: invoice_bill_mapping
    cur.execute('SELECT * FROM invoice_bill_mapping WHERE invoice_id = %s', [invoice_id])
    mappings = cur.fetchall()
    print(f'\n[2] invoice_bill_mapping: {len(mappings)} rows')
    if not mappings:
        print('   [FAIL] No bill mappings found for this invoice!')
        return
    for m in mappings:
        print(f'   bill_id={m["bill_id"]}')

    # Step 3: bill_lines with eu_line_id
    for m in mappings:
        bill_id = m['bill_id']
        cur.execute('SELECT id, eu_line_id, service_name, quantity FROM bill_lines WHERE bill_id = %s', [bill_id])
        blines = cur.fetchall()
        print(f'\n[3] bill_lines for bill_id={bill_id}: {len(blines)} rows')
        for bl in blines:
            print(f'   bill_line id={bl["id"]}, eu_line_id={bl["eu_line_id"]}, qty={bl["quantity"]}, svc={bl["service_name"][:60] if bl["service_name"] else "None"}')

    # Step 4: lueu_lines for those eu_line_ids
    cur.execute('''
        SELECT DISTINCT bl.eu_line_id
        FROM invoice_bill_mapping ibm
        JOIN bill_lines bl ON bl.bill_id = ibm.bill_id
        WHERE ibm.invoice_id = %s AND bl.eu_line_id IS NOT NULL
    ''', [invoice_id])
    eu_ids = [r['eu_line_id'] for r in cur.fetchall()]
    print(f'\n[4] Distinct eu_line_ids: {eu_ids}')

    if not eu_ids:
        print('   [FAIL] No bill_lines have eu_line_id set! This is why cargo details is empty.')
        print('   Checking if bill_lines exist without eu_line_id...')
        cur.execute('''
            SELECT bl.id, bl.eu_line_id, bl.service_name
            FROM invoice_bill_mapping ibm
            JOIN bill_lines bl ON bl.bill_id = ibm.bill_id
            WHERE ibm.invoice_id = %s
        ''', [invoice_id])
        for r in cur.fetchall():
            print(f'   bill_line id={r["id"]}, eu_line_id={r["eu_line_id"]}, svc={r["service_name"][:60] if r["service_name"] else "None"}')
        return

    for eu_id in eu_ids:
        cur.execute('SELECT id, source_type, source_id, source_display, cargo_name, quantity FROM lueu_lines WHERE id = %s', [eu_id])
        eu = cur.fetchone()
        if not eu:
            print(f'   [FAIL] lueu_lines id={eu_id} NOT FOUND')
            continue
        print(f'\n[5] lueu_lines id={eu_id}: source_type={eu["source_type"]}, source_id={eu["source_id"]}, display={eu["source_display"]}, cargo={eu["cargo_name"]}, qty={eu["quantity"]}')

        stype = (eu['source_type'] or '').upper()
        sid = eu['source_id']

        if stype == 'LDUD' and sid:
            # Step 6a: ldud_header
            cur.execute('SELECT id, vcn_id, vessel_name, discharge_commenced, discharge_completed FROM ldud_header WHERE id = %s', [sid])
            ldud = cur.fetchone()
            if not ldud:
                print(f'   [FAIL] ldud_header id={sid} NOT FOUND')
                continue
            print(f'   [6a] ldud_header: vcn_id={ldud["vcn_id"]}, vessel={ldud["vessel_name"]}, dc_start={ldud["discharge_commenced"]}, dc_end={ldud["discharge_completed"]}')

            # Step 7a: vcn_cargo_declaration
            vcn_id = ldud['vcn_id']
            cur.execute('SELECT id, cargo_name, customer_name, bl_quantity FROM vcn_cargo_declaration WHERE vcn_id = %s', [vcn_id])
            cargos = cur.fetchall()
            print(f'   [7a] vcn_cargo_declaration for vcn_id={vcn_id}: {len(cargos)} rows')
            for c in cargos:
                print(f'       cargo={c["cargo_name"]}, customer={c["customer_name"]}, bl_qty={c["bl_quantity"]}')

            if not cargos:
                # Check vcn_header as fallback
                cur.execute('SELECT vessel_name, importer_exporter_name FROM vcn_header WHERE id = %s', [vcn_id])
                vcn = cur.fetchone()
                print(f'   [7a-fallback] vcn_header: {dict(vcn) if vcn else "NOT FOUND"}')

        elif stype == 'MBC' and sid:
            # Step 6b: mbc_header
            cur.execute('SELECT id, mbc_name, cargo_name, bl_quantity FROM mbc_header WHERE id = %s', [sid])
            mbc = cur.fetchone()
            if not mbc:
                print(f'   [FAIL] mbc_header id={sid} NOT FOUND')
                continue
            print(f'   [6b] mbc_header: name={mbc["mbc_name"]}, cargo={mbc["cargo_name"]}, bl_qty={mbc["bl_quantity"]}')

            # Step 7b: mbc_customer_details
            cur.execute('SELECT customer_name, cargo_name, quantity FROM mbc_customer_details WHERE mbc_id = %s', [sid])
            custs = cur.fetchall()
            print(f'   [7b] mbc_customer_details: {len(custs)} rows')
            for c in custs:
                print(f'       customer={c["customer_name"]}, cargo={c["cargo_name"]}, qty={c["quantity"]}')

            # Step 8b: mbc_discharge_port_lines
            cur.execute('SELECT unloading_commenced, unloading_completed FROM mbc_discharge_port_lines WHERE mbc_id = %s', [sid])
            dlines = cur.fetchall()
            print(f'   [8b] mbc_discharge_port_lines: {len(dlines)} rows')
            for d in dlines:
                print(f'       commenced={d["unloading_commenced"]}, completed={d["unloading_completed"]}')
        else:
            print(f'   [WARN] Unknown source_type={stype} or source_id={sid} is None')

    # Also check bill_header source info
    print(f'\n{"="*80}')
    print('BONUS: bill_header source info')
    for m in mappings:
        cur.execute('SELECT id, bill_number, source_type, source_id, customer_name FROM bill_header WHERE id = %s', [m['bill_id']])
        bh = cur.fetchone()
        if bh:
            print(f'  bill_header id={bh["id"]}: number={bh["bill_number"]}, source_type={bh["source_type"]}, source_id={bh["source_id"]}, customer={bh["customer_name"]}')

    conn.close()
    print(f'\n{"="*80}')
    print('DONE')


if __name__ == '__main__':
    if len(sys.argv) < 2:
        print('Usage: python debug_cargo_details.py <invoice_id>')
        sys.exit(1)
    debug(int(sys.argv[1]))
