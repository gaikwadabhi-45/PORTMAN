# FIN01 Cargo Declaration Billing — Replace lueu_lines Dependencies

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Replace every `lueu_lines` dependency in FIN01 billing with direct reads/writes against `vcn_cargo_declaration`, `vcn_export_cargo_declaration`, and `mbc_customer_details`. This is the **final rework of finance modules** — no legacy fallbacks, clean break.

**Architecture:** Billing targets BL-level cargo declaration rows instead of barge-trip rows. Each declaration table gains `is_billed`, `bill_id`, `billed_quantity` columns. `bill_lines` gains `cargo_source_type` + `cargo_source_id` and drops `eu_line_id`. Service type is hardcoded by source: VCN import → CHGU01 (Cargo Handling Unloading), VCN export → CHGL01 (Cargo Handling Loading), MBC → CHGU01. Partial billing tracks `billed_quantity` vs `bl_quantity`/`quantity` on the declaration row. LDUD01 closure uses `ldud_vessel_operations` total. FINV01 invoice cargo appendix sources from declaration tables + `ldud_anchorage` timing.

**Tech Stack:** Python/Flask, PostgreSQL, Alembic, Jinja2.

---

## Background: What Changes and Why

### Current flow (being replaced)
```
lueu_lines (barge trips) ← FIN01 reads/marks as billed
    bill_lines.eu_line_id → lueu_lines.id
    lueu_lines.is_billed / billed_quantity updated on bill save
    LDUD closure: lueu_lines total vs vcn_cargo_declaration.bl_quantity
    FINV01 appendix: bill_lines → lueu_lines → timing fields
```

### New flow
```
vcn_cargo_declaration      (import, customer_name, bl_quantity, is_billed)
vcn_export_cargo_declaration (export, customer_name, bl_quantity, is_billed)
mbc_customer_details        (MBC, customer_name, quantity, is_billed)
    bill_lines.cargo_source_type + cargo_source_id
    declaration.is_billed / billed_quantity updated on bill save
    LDUD closure: ldud_vessel_operations total vs vcn_cargo_declaration.bl_quantity
    FINV01 appendix: bill_lines → cargo declaration → ldud_anchorage timing
```

### Service type hardcoding rule
| Source | Operation | Service Code | Service Name |
|---|---|---|---|
| `vcn_cargo_declaration` | Import / Unload | `CHGU01` | Cargo Handling Unloading |
| `vcn_export_cargo_declaration` | Export / Load | `CHGL01` | Cargo Handling Loading |
| `mbc_customer_details` | Discharge | `CHGU01` | Cargo Handling Unloading |

No user selection required — service type is auto-assigned from source table.

### Partial billing
- `billable_quantity = bl_quantity - billed_quantity` exposed in API
- User can bill any quantity ≤ `billable_quantity`
- `billed_quantity` increments on bill save; decrements on bill delete
- `is_billed = 1` only when `billed_quantity >= bl_quantity`
- Partial Close LDUD stays billable until `is_billed = 1`

### Table schema before migration
**`bill_lines`** (relevant): `eu_line_id` (FK → lueu_lines — being dropped)
**`lueu_lines`** (billing cols being dropped): `is_billed`, `bill_id`, `billed_quantity`, `service_type_id`

---

## Migration File

**Already written at:** `alembic/versions/ee4ff5aa6bb7_cargo_decl_billing_final.py`

Summary of what it does:
1. `vcn_cargo_declaration` → ADD `is_billed`, `bill_id`, `billed_quantity`
2. `vcn_export_cargo_declaration` → ADD `is_billed`, `bill_id`, `billed_quantity`
3. `mbc_customer_details` → ADD `is_billed`, `bill_id`, `billed_quantity`
4. `bill_lines` → ADD `cargo_source_type`, `cargo_source_id`; DROP FK + DROP `eu_line_id`
5. `lueu_lines` → DROP `is_billed`, `bill_id`, `billed_quantity`, `service_type_id`

---

## Task 1: Run the Migration

### Step 1: Run migration

```bash
cd d:/PORTMAN
alembic upgrade head
```
Expected: `Running upgrade dd3ee4ff5aa6 -> ee4ff5aa6bb7`

### Step 2: Verify columns

```bash
python -c "
from database import get_db, get_cursor
conn = get_db()
cur = get_cursor(conn)
for t in ['vcn_cargo_declaration','vcn_export_cargo_declaration','mbc_customer_details']:
    cur.execute('SELECT column_name FROM information_schema.columns WHERE table_name=%s AND column_name IN (\'is_billed\',\'bill_id\',\'billed_quantity\')', (t,))
    print(t, [r['column_name'] for r in cur.fetchall()])
cur.execute('SELECT column_name FROM information_schema.columns WHERE table_name=\'bill_lines\' AND column_name IN (\'cargo_source_type\',\'cargo_source_id\',\'eu_line_id\')', ())
print('bill_lines', [r['column_name'] for r in cur.fetchall()])
conn.close()
"
```
Expected: 3 cols on each decl table, `cargo_source_type` + `cargo_source_id` on bill_lines, NO `eu_line_id`.

### Step 3: Commit

```bash
git add alembic/versions/ee4ff5aa6bb7_cargo_decl_billing_final.py
git commit -m "feat: migration - cargo decl billing columns, drop lueu billing cols and eu_line_id"
```

---

## Task 2: FIN01 model.py — Billed Tracking Helpers

**Files:**
- Modify: `modules/FIN01/model.py`

Add two helpers at the top of the file, after the imports:

### Step 1: Add helpers

```python
def _mark_cargo_source_billed(cur, cargo_source_type, cargo_source_id, bill_qty, bill_id):
    """Increment billed_quantity on the correct declaration row."""
    if not cargo_source_type or not cargo_source_id:
        return
    bill_qty = float(bill_qty or 0)
    if cargo_source_type == 'VCN_IMPORT':
        cur.execute('''
            UPDATE vcn_cargo_declaration
            SET billed_quantity = COALESCE(billed_quantity, 0) + %s,
                bill_id = %s,
                is_billed = CASE
                    WHEN COALESCE(billed_quantity, 0) + %s >= bl_quantity THEN 1
                    ELSE is_billed
                END
            WHERE id = %s
        ''', [bill_qty, bill_id, bill_qty, cargo_source_id])
    elif cargo_source_type == 'VCN_EXPORT':
        cur.execute('''
            UPDATE vcn_export_cargo_declaration
            SET billed_quantity = COALESCE(billed_quantity, 0) + %s,
                bill_id = %s,
                is_billed = CASE
                    WHEN COALESCE(billed_quantity, 0) + %s >= bl_quantity THEN 1
                    ELSE is_billed
                END
            WHERE id = %s
        ''', [bill_qty, bill_id, bill_qty, cargo_source_id])
    elif cargo_source_type == 'MBC':
        cur.execute('''
            UPDATE mbc_customer_details
            SET billed_quantity = COALESCE(billed_quantity, 0) + %s,
                bill_id = %s,
                is_billed = CASE
                    WHEN COALESCE(billed_quantity, 0) + %s >= quantity THEN 1
                    ELSE is_billed
                END
            WHERE id = %s
        ''', [bill_qty, bill_id, bill_qty, cargo_source_id])


def _unmark_cargo_source_billed(cur, cargo_source_type, cargo_source_id, bill_qty):
    """Decrement billed_quantity on the correct declaration row (bill delete/reversal)."""
    if not cargo_source_type or not cargo_source_id:
        return
    bill_qty = float(bill_qty or 0)
    sql = '''
        UPDATE {table}
        SET billed_quantity = GREATEST(COALESCE(billed_quantity, 0) - %s, 0),
            is_billed = CASE
                WHEN GREATEST(COALESCE(billed_quantity, 0) - %s, 0) <= 0 THEN 0
                ELSE is_billed
            END,
            bill_id = CASE
                WHEN GREATEST(COALESCE(billed_quantity, 0) - %s, 0) <= 0 THEN NULL
                ELSE bill_id
            END
        WHERE id = %s
    '''
    table_map = {
        'VCN_IMPORT': 'vcn_cargo_declaration',
        'VCN_EXPORT': 'vcn_export_cargo_declaration',
        'MBC':        'mbc_customer_details',
    }
    table = table_map.get(cargo_source_type)
    if table:
        cur.execute(sql.format(table=table), [bill_qty, bill_qty, bill_qty, cargo_source_id])
```

### Step 2: Verify syntax

```bash
python -c "from modules.FIN01 import model; print('OK')"
```

### Step 3: Commit

```bash
git add modules/FIN01/model.py
git commit -m "feat: FIN01 model - billed tracking helpers for cargo declaration tables"
```

---

## Task 3: FIN01 model.py — Update save_bill_line()

**Files:**
- Modify: `modules/FIN01/model.py` — `save_bill_line()`

### Step 1: Add `cargo_source_type`, `cargo_source_id` to INSERT

Replace the INSERT statement's column list and values:
```python
cur.execute('''INSERT INTO bill_lines
    (bill_id, service_record_id, service_type_id, service_name,
     service_description, quantity, uom, rate, line_amount, gst_rate_id,
     cgst_rate, sgst_rate, igst_rate, cgst_amount, sgst_amount, igst_amount,
     line_total, gl_code, sac_code, remarks,
     service_code, tds_applicable, tds_percent, tds_amount,
     tcs_applicable, tcs_percent, tcs_amount,
     cargo_source_type, cargo_source_id)
    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
    RETURNING id''',
    [data['bill_id'], data.get('service_record_id'),
     data.get('service_type_id'), data.get('service_name'),
     data.get('service_description'),
     data.get('quantity'), data.get('uom'), data.get('rate'), data.get('line_amount'),
     data.get('gst_rate_id'), data.get('cgst_rate'), data.get('sgst_rate'),
     data.get('igst_rate'), data.get('cgst_amount'), data.get('sgst_amount'),
     data.get('igst_amount'), data.get('line_total'), data.get('gl_code'),
     data.get('sac_code'), data.get('remarks'),
     service_code, tds_applicable, tds_percent, tds_amount,
     tcs_applicable, tcs_percent, tcs_amount,
     data.get('cargo_source_type'), data.get('cargo_source_id')])
```

Note: `eu_line_id` is **removed** from the column list.

### Step 2: Add `cargo_source_type`, `cargo_source_id` to UPDATE

In the UPDATE statement, replace:
```python
SET eu_line_id=%s, service_record_id=%s, ...
```
with:
```python
SET service_record_id=%s, ...,
    cargo_source_type=%s, cargo_source_id=%s
WHERE id=%s
```
Remove `data.get('eu_line_id')` from the params list.

### Step 3: Replace the lueu_lines update block with helper call

Remove:
```python
if data.get('eu_line_id'):
    bill_qty = float(data.get('quantity') or 0)
    cur.execute('''UPDATE lueu_lines SET billed_quantity = ...''')
```

Replace with:
```python
bill_qty = float(data.get('quantity') or 0)
_mark_cargo_source_billed(
    cur,
    data.get('cargo_source_type'),
    data.get('cargo_source_id'),
    bill_qty,
    data.get('bill_id')
)
```

### Step 4: Verify

```bash
python -c "from modules.FIN01 import model; print('OK')"
```

### Step 5: Commit

```bash
git add modules/FIN01/model.py
git commit -m "feat: FIN01 save_bill_line uses cargo_source_type/id, drops eu_line_id"
```

---

## Task 4: FIN01 model.py — Update delete_bill() and delete_bill_line()

**Files:**
- Modify: `modules/FIN01/model.py`

### Step 1: Update delete_bill() — remove lueu_lines reversal

Remove:
```python
cur.execute('''UPDATE lueu_lines el
    SET billed_quantity = GREATEST(...) ...
    FROM bill_lines bl
    WHERE bl.bill_id = %s AND bl.eu_line_id = el.id''', (bill_id,))
```

Replace with:
```python
# Reverse billed tracking on cargo declaration tables
cur.execute('''
    SELECT cargo_source_type, cargo_source_id, quantity
    FROM bill_lines
    WHERE bill_id = %s AND cargo_source_type IS NOT NULL AND cargo_source_id IS NOT NULL
''', (bill_id,))
for row in cur.fetchall():
    _unmark_cargo_source_billed(
        cur,
        row['cargo_source_type'],
        row['cargo_source_id'],
        float(row['quantity'] or 0)
    )
```

### Step 2: Update delete_bill_line() — reverse source tracking before delete

Replace the current single-line delete with:
```python
def delete_bill_line(row_id):
    """Delete bill line and reverse billed tracking on cargo source."""
    conn = get_db()
    cur = get_cursor(conn)
    cur.execute(
        'SELECT cargo_source_type, cargo_source_id, quantity, service_record_id FROM bill_lines WHERE id=%s',
        (row_id,)
    )
    bl = cur.fetchone()
    if bl:
        _unmark_cargo_source_billed(
            cur,
            bl['cargo_source_type'],
            bl['cargo_source_id'],
            float(bl['quantity'] or 0)
        )
        if bl.get('service_record_id'):
            cur.execute(
                'UPDATE service_records SET is_billed=0, bill_id=NULL WHERE id=%s',
                [bl['service_record_id']]
            )
    cur.execute('DELETE FROM bill_lines WHERE id=%s', (row_id,))
    conn.commit()
    conn.close()
```

### Step 3: Verify

```bash
python -c "from modules.FIN01 import model; print('OK')"
```

### Step 4: Commit

```bash
git add modules/FIN01/model.py
git commit -m "feat: FIN01 delete_bill/delete_bill_line reverses cargo decl billed tracking"
```

---

## Task 5: FIN01 views.py — Rewrite get_customer_billables() Cargo Section

**Files:**
- Modify: `modules/FIN01/views.py` — `get_customer_billables()` route (~line 515)

Replace the entire `# --- A. Cargo Handling` section (everything from that comment to `cargo_handling = []` assignment and the groups loop) with:

### Step 1: Replace with direct declaration queries

```python
# --- A. Cargo Handling: direct from cargo declaration tables ---
# Service types are HARDCODED by source:
#   vcn_cargo_declaration        → CHGU01 (Cargo Handling Unloading)
#   vcn_export_cargo_declaration → CHGL01 (Cargo Handling Loading)
#   mbc_customer_details         → CHGU01 (Cargo Handling Unloading)

cur.execute("""
    SELECT id, service_code, service_name, sac_code, uom,
           is_tds, tds_percent, is_tcs, tcs_percent
    FROM finance_service_types
    WHERE service_code IN ('CHGL01', 'CHGU01') AND is_active = 1
""")
cargo_st_map = {r['service_code']: dict(r) for r in cur.fetchall()}

cargo_handling = []

# A1. VCN Import declarations → CHGU01 (Unloading)
cur.execute("""
    SELECT cd.id, cd.vcn_id, cd.cargo_name, cd.bl_no, cd.bl_date,
           cd.bl_quantity, cd.quantity_uom,
           COALESCE(cd.is_billed, 0) AS is_billed,
           COALESCE(cd.billed_quantity, 0) AS billed_quantity,
           vh.vcn_doc_num, vh.vessel_name
    FROM vcn_cargo_declaration cd
    JOIN vcn_header vh ON cd.vcn_id = vh.id
    WHERE cd.customer_name = %s
      AND (COALESCE(cd.is_billed, 0) = 0
           OR COALESCE(cd.billed_quantity, 0) < cd.bl_quantity)
    ORDER BY vh.vcn_doc_num DESC, cd.id
""", [customer_name])
import_decls = [dict(r) for r in cur.fetchall()]

# A2. VCN Export declarations → CHGL01 (Loading)
cur.execute("""
    SELECT cd.id, cd.vcn_id, cd.cargo_name, cd.bl_no, cd.bl_date,
           cd.bl_quantity, cd.quantity_uom,
           COALESCE(cd.is_billed, 0) AS is_billed,
           COALESCE(cd.billed_quantity, 0) AS billed_quantity,
           vh.vcn_doc_num, vh.vessel_name
    FROM vcn_export_cargo_declaration cd
    JOIN vcn_header vh ON cd.vcn_id = vh.id
    WHERE cd.customer_name = %s
      AND (COALESCE(cd.is_billed, 0) = 0
           OR COALESCE(cd.billed_quantity, 0) < cd.bl_quantity)
    ORDER BY vh.vcn_doc_num DESC, cd.id
""", [customer_name])
export_decls = [dict(r) for r in cur.fetchall()]

# A3. MBC customer details → CHGU01 (Unloading)
cur.execute("""
    SELECT cd.id, cd.mbc_id, cd.cargo_name, cd.bill_of_coastal_goods_no,
           cd.quantity, cd.material_po,
           COALESCE(cd.is_billed, 0) AS is_billed,
           COALESCE(cd.billed_quantity, 0) AS billed_quantity,
           mh.doc_num, mh.mbc_name, mh.doc_status AS mbc_status
    FROM mbc_customer_details cd
    JOIN mbc_header mh ON cd.mbc_id = mh.id
    WHERE cd.customer_name = %s
      AND (COALESCE(cd.is_billed, 0) = 0
           OR COALESCE(cd.billed_quantity, 0) < cd.quantity)
    ORDER BY mh.doc_num DESC, cd.id
""", [customer_name])
mbc_decls = [dict(r) for r in cur.fetchall()]

# Batch-fetch LDUD closure status for all VCN sources
vcn_ids_needed = set(r['vcn_id'] for r in import_decls + export_decls)
ldud_by_vcn = {}  # vcn_id -> {doc_status, material_po_number, doc_label}
for vcn_id in vcn_ids_needed:
    cur.execute("""
        SELECT lh.doc_status, lh.material_po_number, h.vcn_doc_num, h.vessel_name
        FROM ldud_header lh
        JOIN vcn_header h ON lh.vcn_id = h.id
        WHERE lh.vcn_id = %s
        ORDER BY lh.id DESC LIMIT 1
    """, [vcn_id])
    row = cur.fetchone()
    if row:
        ldud_by_vcn[vcn_id] = {
            'doc_status':        row['doc_status'] or '',
            'material_po_number': row['material_po_number'] or '',
            'doc_label':         f"{row['vcn_doc_num']} / {row['vessel_name']}"
        }

def _build_cargo_item(decl, cargo_source_type, svc_code, bl_quantity_field='bl_quantity',
                      source_type='VCN', source_id_field='vcn_id', ldud_info=None, mbc_status=None):
    st = cargo_st_map.get(svc_code, {})
    total_qty = float(decl.get(bl_quantity_field) or 0)
    billed_qty = float(decl.get('billed_quantity') or 0)
    billable_qty = max(round(total_qty - billed_qty, 3), 0)

    if ldud_info:
        doc_status = ldud_info.get('doc_status', '')
        is_billable = doc_status in ('Closed', 'Partial Close')
        doc_label = ldud_info.get('doc_label', '')
        material_po = ldud_info.get('material_po_number', '')
    else:
        doc_status = mbc_status or ''
        is_billable = doc_status in ('Approved', 'Closed', 'Partial Close')
        doc_label = f"{decl.get('doc_num', '')} / {decl.get('mbc_name', '')}"
        material_po = decl.get('material_po') or ''

    return {
        'source_type':       source_type,
        'source_id':         decl.get(source_id_field),
        'cargo_source_type': cargo_source_type,
        'cargo_source_id':   decl['id'],
        'doc_label':         doc_label,
        'doc_status':        doc_status,
        'is_billable':       is_billable,
        'service_code':      svc_code,
        'service_type_id':   st.get('id'),
        'service_name':      st.get('service_name', ''),
        'sac_code':          st.get('sac_code', ''),
        'is_tds':            st.get('is_tds', 0),
        'tds_percent':       float(st.get('tds_percent') or 0),
        'is_tcs':            st.get('is_tcs', 0),
        'tcs_percent':       float(st.get('tcs_percent') or 0),
        'total_quantity':    total_qty,
        'billed_quantity':   billed_qty,
        'billable_quantity': billable_qty,
        'uom':               decl.get('quantity_uom') or st.get('uom') or 'MT',
        'cargo_name':        decl.get('cargo_name') or '',
        'bl_no':             decl.get('bl_no') or decl.get('bill_of_coastal_goods_no') or '',
        'bl_date':           str(decl.get('bl_date') or ''),
        'material_po':       material_po,
        'material_po_options': []
    }

for r in import_decls:
    cargo_handling.append(_build_cargo_item(
        r, 'VCN_IMPORT', 'CHGU01',
        bl_quantity_field='bl_quantity', source_type='VCN', source_id_field='vcn_id',
        ldud_info=ldud_by_vcn.get(r['vcn_id'])
    ))

for r in export_decls:
    cargo_handling.append(_build_cargo_item(
        r, 'VCN_EXPORT', 'CHGL01',
        bl_quantity_field='bl_quantity', source_type='VCN', source_id_field='vcn_id',
        ldud_info=ldud_by_vcn.get(r['vcn_id'])
    ))

for r in mbc_decls:
    cargo_handling.append(_build_cargo_item(
        r, 'MBC', 'CHGU01',
        bl_quantity_field='quantity', source_type='MBC', source_id_field='mbc_id',
        mbc_status=r.get('mbc_status')
    ))
```

### Step 2: Remove now-dead variables from the old section

Delete: `cargo_st_map` (old fetch), `vcn_cargo_map`, `mbc_cargo_map`, `allowed_sources`, `eu_rows`, `eu_groups` and their queries.

### Step 3: Remove the /api/module/FIN01/eu-lines/ route

Delete the entire `get_lueu_lines` route function (~line 287). It served old lueu_lines lookup — no longer needed.

### Step 4: Test

```bash
curl -s "http://localhost:5000/api/module/FIN01/customer-billables/Customer/1" \
  -H "Cookie: session=..." | python -m json.tool | grep -E "cargo_source_type|billable_quantity|service_code"
```
Expected: items with `cargo_source_type` in `[VCN_IMPORT, VCN_EXPORT, MBC]`, correct `service_code`.

### Step 5: Commit

```bash
git add modules/FIN01/views.py
git commit -m "feat: FIN01 get_customer_billables queries cargo declarations directly, hardcoded service codes"
```

---

## Task 6: FIN01 views.py — Clean Up save_bill() Route

**Files:**
- Modify: `modules/FIN01/views.py` — `save_bill()` route

### Step 1: Remove the `eu_line_ids` extraction block

Delete (~lines 159-162):
```python
eu_ids = line.get('eu_line_ids') or []
if eu_ids and not line.get('eu_line_id'):
    line['eu_line_id'] = eu_ids[0]
```

### Step 2: Remove the redundant lueu_lines update loop

Delete (~lines 178-192):
```python
for line in lines:
    if line.get('line_type') == 'cargo_handling':
        eu_ids = line.get('eu_line_ids') or []
        ...
        for eu_id in eu_ids:
            cur.execute('''UPDATE lueu_lines SET billed_quantity...''')
```
This is now handled inside `model.save_bill_line()`.

### Step 3: `cargo_source_type` and `cargo_source_id` pass through automatically

The frontend now sends them directly on each line object. The loop `for line in lines: ... model.save_bill_line(line)` passes them through without any mapping needed.

### Step 4: Verify

```bash
python -c "from modules.FIN01 import views; print('OK')"
```

### Step 5: Commit

```bash
git add modules/FIN01/views.py
git commit -m "feat: FIN01 save_bill removes lueu_lines update loop, cargo_source passes through"
```

---

## Task 7: LDUD01 model.py — Update get_closure_eligibility()

**Files:**
- Modify: `modules/LDUD01/model.py` — `get_closure_eligibility()`

### Step 1: Replace lueu_lines total with ldud_vessel_operations total (~line 622)

Remove:
```python
lueu_total = 0.0
if vcn_id:
    cur.execute('SELECT COALESCE(SUM(quantity), 0) AS total FROM lueu_lines WHERE source_type=%s AND source_id=%s',
                 ('VCN', vcn_id))
    lueu_total = float(cur.fetchone()['total'])
```

Replace with:
```python
ops_total = 0.0
cur.execute(
    'SELECT COALESCE(SUM(quantity), 0) AS total FROM ldud_vessel_operations WHERE ldud_id = %s',
    (ldud_id,)
)
ops_total = float(cur.fetchone()['total'])
```

### Step 2: Update `can_full_close` (~line 638)

```python
# OLD
can_full_close = eligible and bl_total > 0 and abs(lueu_total - bl_total) < 0.01
# NEW
can_full_close = eligible and bl_total > 0 and abs(ops_total - bl_total) < 0.01
```

### Step 3: Update return dict

```python
return {
    'eligible':     eligible,
    'missing':      missing,
    'ops_total':    ops_total,    # renamed from lueu_total
    'bl_total':     bl_total,
    'can_full_close': can_full_close
}
```

### Step 4: Update any LDUD template displaying lueu_total

```bash
grep -rn "lueu_total" modules/LDUD01/
```
Replace all occurrences of `lueu_total` with `ops_total` in Jinja2 templates.

### Step 5: Verify

```bash
python -c "from modules.LDUD01 import model; print('OK')"
```

### Step 6: Commit

```bash
git add modules/LDUD01/
git commit -m "feat: LDUD01 closure uses vessel_operations total instead of lueu_lines total"
```

---

## Task 8: LUEU01 model.py — Remove Dead eu_line_id Reference in soft_delete_lines()

**Files:**
- Modify: `modules/LUEU01/model.py` — `soft_delete_lines()` (~line 151)

The auto-CN trigger logic queried `bill_lines.eu_line_id` which is now dropped. Since billing no longer tracks through lueu_lines, soft-deleting a lueu_line has no billing implications.

### Step 1: Simplify soft_delete_lines()

Replace the entire function with:
```python
def soft_delete_lines(ids, username=None):
    """Soft-delete lueu lines. Returns empty list (billing no longer tracked via lueu_lines)."""
    conn = get_db()
    cur = get_cursor(conn)
    today = datetime.now().strftime('%Y-%m-%d')
    for line_id in ids:
        cur.execute('''
            UPDATE lueu_lines
            SET is_deleted = TRUE, deleted_by = %s, deleted_date = %s
            WHERE id = %s AND (is_deleted IS NOT TRUE)
        ''', [username, today, line_id])
    conn.commit()
    conn.close()
    return []   # caller checks for invoiced_lines to trigger auto-CN; none here
```

### Step 2: Verify

```bash
python -c "from modules.LUEU01 import model; print('OK')"
```

### Step 3: Commit

```bash
git add modules/LUEU01/model.py
git commit -m "feat: LUEU01 soft_delete_lines removes dead eu_line_id billing check"
```

---

## Task 9: FINV01 views.py — Rewrite _get_cargo_handling_details() for Invoice Appendix

**Files:**
- Modify: `modules/FINV01/views.py` — `_get_cargo_handling_details()` (~line 299)

This function produces the cargo appendix printed on invoices. It must now read from declaration tables + `ldud_anchorage` for timing. **No legacy lueu fallback** — this is the final change.

### Step 1: Replace the function body entirely

```python
def _get_cargo_handling_details(invoice_id):
    """
    Build cargo appendix rows for invoice print.
    Source: bill_lines.cargo_source_type / cargo_source_id
      VCN_IMPORT  -> vcn_cargo_declaration  -> ldud_anchorage for timing
      VCN_EXPORT  -> vcn_export_cargo_declaration -> ldud_anchorage for timing
      MBC         -> mbc_customer_details   -> mbc_header dates
    """
    conn = get_db()
    cur = get_cursor(conn)
    rows = []
    seen = set()
    try:
        invoice = model.get_invoice_by_id(invoice_id) or {}

        # Get all cargo source references for this invoice
        cur.execute('''
            SELECT DISTINCT bl.cargo_source_type, bl.cargo_source_id,
                            SUM(bl.quantity) OVER (
                                PARTITION BY bl.cargo_source_type, bl.cargo_source_id
                            ) AS billed_qty
            FROM invoice_bill_mapping ibm
            JOIN bill_lines bl ON bl.bill_id = ibm.bill_id
            WHERE ibm.invoice_id = %s
              AND bl.cargo_source_type IS NOT NULL
              AND bl.cargo_source_id IS NOT NULL
        ''', [invoice_id])
        sources = [dict(r) for r in cur.fetchall()]

        for src in sources:
            cstype = src['cargo_source_type']
            csid   = src['cargo_source_id']
            key    = (cstype, csid)
            if key in seen:
                continue
            seen.add(key)

            billed_qty = float(src.get('billed_qty') or 0)

            if cstype in ('VCN_IMPORT', 'VCN_EXPORT'):
                # Fetch declaration row
                table = 'vcn_cargo_declaration' if cstype == 'VCN_IMPORT' else 'vcn_export_cargo_declaration'
                cur.execute(f'''
                    SELECT cd.vcn_id, cd.cargo_name, cd.bl_no, cd.bl_date,
                           cd.bl_quantity, cd.quantity_uom,
                           vh.vcn_doc_num, vh.vessel_name
                    FROM {table} cd
                    JOIN vcn_header vh ON cd.vcn_id = vh.id
                    WHERE cd.id = %s
                ''', [csid])
                decl = cur.fetchone()
                if not decl:
                    continue

                vcn_id = decl['vcn_id']

                # Get timing from ldud_anchorage via ldud_header
                cur.execute('''
                    SELECT MIN(a.discharge_started) AS start_dt,
                           MAX(a.discharge_commenced) AS end_dt
                    FROM ldud_header lh
                    JOIN ldud_anchorage a ON a.ldud_id = lh.id
                    WHERE lh.vcn_id = %s
                ''', [vcn_id])
                timing = cur.fetchone()

                start_dt = timing['start_dt'] if timing else None
                end_dt   = timing['end_dt']   if timing else None

                rows.append({
                    'source_type':  'VCN',
                    'source_id':    vcn_id,
                    'vessel_name':  decl['vessel_name'] or '',
                    'vcn_doc_num':  decl['vcn_doc_num'] or '',
                    'cargo_name':   decl['cargo_name'] or '',
                    'bl_no':        decl['bl_no'] or '',
                    'bl_date':      str(decl['bl_date'] or ''),
                    'billed_qty':   billed_qty,
                    'uom':          decl['quantity_uom'] or 'MT',
                    'start_date':   str(start_dt)[:10]   if start_dt else '',
                    'start_time':   str(start_dt)[11:16] if start_dt else '',
                    'end_date':     str(end_dt)[:10]     if end_dt   else '',
                    'end_time':     str(end_dt)[11:16]   if end_dt   else '',
                })

            elif cstype == 'MBC':
                cur.execute('''
                    SELECT cd.mbc_id, cd.cargo_name, cd.bill_of_coastal_goods_no, cd.quantity,
                           mh.doc_num, mh.mbc_name, mh.doc_date
                    FROM mbc_customer_details cd
                    JOIN mbc_header mh ON cd.mbc_id = mh.id
                    WHERE cd.id = %s
                ''', [csid])
                decl = cur.fetchone()
                if not decl:
                    continue

                rows.append({
                    'source_type':  'MBC',
                    'source_id':    decl['mbc_id'],
                    'vessel_name':  decl['mbc_name'] or '',
                    'vcn_doc_num':  decl['doc_num'] or '',
                    'cargo_name':   decl['cargo_name'] or '',
                    'bl_no':        decl['bill_of_coastal_goods_no'] or '',
                    'bl_date':      str(decl['doc_date'] or ''),
                    'billed_qty':   billed_qty,
                    'uom':          'MT',
                    'start_date':   '',
                    'start_time':   '',
                    'end_date':     '',
                    'end_time':     '',
                })

        return rows
    finally:
        conn.close()
```

### Step 2: Remove all legacy lueu_lines code in this function

Delete everything below the `sources = [dict(r) for r in cur.fetchall()]` line in the old function — all the `eu_data_by_cargo`, `source_groups`, fallback paths, and the big source-type branching block. The new implementation above is complete.

### Step 3: Check if `_get_invoice_appendix_data()` also uses eu_line_id (~line 1157)

```bash
grep -n "eu_line_id\|lueu_lines" modules/FINV01/views.py | head -30
```

Update `_get_invoice_appendix_data()` with the same pattern: replace `JOIN lueu_lines ll ON ll.id = bl.eu_line_id` with the `cargo_source_type` + `cargo_source_id` lookup.

In the appendix function, the key query is:
```python
# OLD
cur.execute('''
    SELECT DISTINCT ll.source_type, ll.source_id, ll.cargo_name, ll.quantity
    FROM bill_lines bl
    JOIN lueu_lines ll ON ll.id = bl.eu_line_id
    WHERE bl.bill_id = %s AND bl.eu_line_id IS NOT NULL
''', [bill_id])
```

Replace with:
```python
cur.execute('''
    SELECT DISTINCT bl.cargo_source_type, bl.cargo_source_id,
                    bl.service_description AS cargo_name, bl.quantity
    FROM bill_lines bl
    WHERE bl.bill_id = %s AND bl.cargo_source_type IS NOT NULL
''', [bill_id])
# Then resolve vcn_id from cargo_source for VCN_IMPORT/VCN_EXPORT
# using vcn_cargo_declaration.vcn_id or vcn_export_cargo_declaration.vcn_id
```

### Step 4: Test invoice print

1. Create a bill from a VCN import declaration
2. Convert to invoice
3. Open `/module/FINV01/invoice/print/<id>`
4. Verify cargo appendix shows vessel name, cargo name, BL no, dates, billed quantity

### Step 5: Commit

```bash
git add modules/FINV01/views.py
git commit -m "feat: FINV01 cargo appendix reads from cargo declarations + ldud_anchorage, removes lueu fallback"
```

---

## Task 10: Frontend — generate_bill.html

**Files:**
- Modify: `modules/FIN01/generate_bill.html`

### Step 1: Find the cargo line construction

```bash
grep -n "eu_line_ids\|eu_line_id\|cargo_handling\|billable" modules/FIN01/generate_bill.html | head -30
```

### Step 2: Replace eu_line_ids with cargo_source fields

In the JavaScript where a cargo handling line object is built for the POST body, replace:
```javascript
// OLD
eu_line_ids: item.lines.map(l => l.id),
quantity: item.total_quantity,
line_type: 'cargo_handling',
```
with:
```javascript
// NEW
cargo_source_type: item.cargo_source_type,   // 'VCN_IMPORT' | 'VCN_EXPORT' | 'MBC'
cargo_source_id:   item.cargo_source_id,      // id in the declaration table
quantity: item.billable_quantity,             // BL qty minus already billed
line_type: 'cargo_handling',
```

### Step 3: Update the quantity display

The old UI showed `item.total_quantity` (total lueu trips). Now show:
- `item.total_quantity` — BL quantity (label: "BL Qty")
- `item.billed_quantity` — already billed (label: "Billed")
- `item.billable_quantity` — available to bill now (pre-fill in quantity input)

### Step 4: Remove per-trip lines display

The old response had `item.lines: [{lueu trip rows}]`. The new response does **not** have a `lines` array. Remove any UI code that renders individual trip rows (barge name, equipment, shift, etc.).

Instead display: `cargo_name`, `bl_no`, `bl_date` from the cargo_handling item directly.

### Step 5: Service name is auto-populated (no dropdown needed)

The `service_name` and `service_type_id` come from the API response as hardcoded values. The frontend should **not** show a service type dropdown for cargo handling lines — the value is fixed.

### Step 6: End-to-end test

1. Open Generate Bill page → select a customer with VCN import declarations
2. Verify items appear with correct `service_name` = "Cargo Handling Unloading"
3. Verify quantity defaults to `billable_quantity`
4. Save bill → verify `vcn_cargo_declaration.billed_quantity` updated
5. Partial bill (enter qty < billable_qty) → verify `is_billed = 0`, partial quantity tracked
6. Delete bill → verify `billed_quantity` decremented back

### Step 7: Commit

```bash
git add modules/FIN01/generate_bill.html
git commit -m "feat: bill generation UI uses cargo_source_type/id, shows billable_quantity"
```

---

## Task 11: Smoke Test — Full End-to-End

### VCN Import path
1. VCN with import `vcn_cargo_declaration` rows for a customer
2. LDUD linked to the VCN, status = "Closed"
3. FIN01 → Generate Bill → customer shows import declarations as CHGU01 (Unloading)
4. Bill saved → `vcn_cargo_declaration.billed_quantity` = billed qty, `is_billed = 1` if full
5. Approve bill → FINV01 → create invoice
6. Invoice print → cargo appendix shows: vessel, cargo, BL no, BL date, timing from `ldud_anchorage`

### VCN Export path
1. VCN with export `vcn_export_cargo_declaration` rows for a customer
2. FIN01 → Generate Bill → shows CHGL01 (Loading)
3. Same flow, verify service code is CHGL01

### MBC path
1. MBC with `mbc_customer_details` rows for a customer, status = "Approved"
2. FIN01 → shows CHGU01
3. Invoice print → appendix shows MBC doc number, cargo name

### Partial billing
1. BL qty = 1000 MT, bill only 600 MT
2. After bill: `billed_quantity = 600`, `is_billed = 0`
3. Second bill: `billable_quantity = 400` shown
4. After second bill: `billed_quantity = 1000`, `is_billed = 1`

### LDUD Partial Close
1. LDUD with status "Partial Close"
2. FIN01 → cargo item shows `is_billable = true` (Partial Close is billable)
3. Bill created successfully

### Delete bill reversal
1. Delete the bill from step 4 above
2. Verify `billed_quantity` decremented, `is_billed = 0` if fully reversed

### Final commit

```bash
git add .
git commit -m "feat: complete FIN01 cargo declaration billing - final finance module rework"
```

---

## Summary of All Changes

| File | Change |
|---|---|
| `alembic/versions/ee4ff5aa6bb7_cargo_decl_billing_final.py` | ADD billed tracking to 3 declaration tables; ADD `cargo_source_type`/`cargo_source_id` to `bill_lines`; DROP `eu_line_id` from `bill_lines`; DROP billing cols from `lueu_lines` |
| `modules/FIN01/model.py` | ADD `_mark_cargo_source_billed` / `_unmark_cargo_source_billed` helpers; REWRITE `save_bill_line` (drops eu_line_id, adds cargo_source); REWRITE `delete_bill` / `delete_bill_line` |
| `modules/FIN01/views.py` | REWRITE `get_customer_billables` cargo section (direct decl queries, hardcoded services); CLEAN `save_bill` (remove lueu loop + eu_line_ids mapping); REMOVE `get_lueu_lines` route |
| `modules/FIN01/generate_bill.html` | Replace `eu_line_ids` with `cargo_source_type`/`cargo_source_id`; bind `billable_quantity`; remove per-trip lines display |
| `modules/LDUD01/model.py` | `get_closure_eligibility` uses `ldud_vessel_operations` total; rename `lueu_total` → `ops_total` |
| `modules/LUEU01/model.py` | Simplify `soft_delete_lines` — remove dead `bill_lines.eu_line_id` query |
| `modules/FINV01/views.py` | REWRITE `_get_cargo_handling_details` (declaration tables + ldud_anchorage timing, no lueu fallback); UPDATE `_get_invoice_appendix_data` (same pattern) |

**`lueu_lines` table is kept** — LUEU01 equipment utilization tracking is unaffected. Only the billing columns are dropped.
