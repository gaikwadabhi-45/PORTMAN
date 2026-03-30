# EU Soft-Delete + Auto-CN + Manual DN/CN + UI Fix Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Replace hard-delete on EU (LUEU01) lines with a permission-gated soft-delete flag that automatically generates Credit Notes in FDCN01 when invoiced lines are deleted, and extend FDCN01 to support manual DN/CN entry in addition to rate-revision-only flow.

**Architecture:** Four changes in parallel domains — (1) LUEU01 UI/model/views get soft-delete with `is_deleted` flag, (2) FDCN01 model gets auto-CN creation triggered from the delete endpoint, (3) FIN01 billing queries gain `is_deleted` filter, (4) FDCN01 entry form gains a Manual Entry mode alongside the existing Rate Revision mode. No migration framework exists; DB changes are direct `ALTER TABLE` SQL run once.

**Tech Stack:** Flask/Python, PostgreSQL (psycopg2), Tabulator 5 (remote pagination), Jinja2 templates, vanilla JS.

---

## Database Changes (Run Once)

Connect to the PostgreSQL database and run this SQL **before starting any task**:

```sql
-- LUEU01: soft-delete columns
ALTER TABLE lueu_lines ADD COLUMN IF NOT EXISTS is_deleted BOOLEAN DEFAULT FALSE;
ALTER TABLE lueu_lines ADD COLUMN IF NOT EXISTS deleted_by VARCHAR;
ALTER TABLE lueu_lines ADD COLUMN IF NOT EXISTS deleted_date DATE;

-- FDCN01: track how the document was created
ALTER TABLE fdcn_header ADD COLUMN IF NOT EXISTS creation_type VARCHAR DEFAULT 'rate_revision';
```

---

## Task 1: Fix LUEU01 — New Row Ordering Bug

**Problem:** When user is on page 2+ of the remote-paginated table and clicks "+ Add Row", the row is added visually to the top of the current page, but after the next server fetch it lands on page 1 (ORDER BY id DESC). Feels like it "appeared at the end."

**Root cause:** `addRow()` in [modules/LUEU01/lueu01.html:641](modules/LUEU01/lueu01.html) calls `table.addRow(newData, true)` without first navigating to page 1.

**Files:**
- Modify: `modules/LUEU01/lueu01.html:614-642`

**Step 1: Find the addRow function**

In [modules/LUEU01/lueu01.html](modules/LUEU01/lueu01.html) around line 614:

```javascript
async function addRow() {
    // Save any dirty rows before adding a new one
    if (dirtyRowIds.size > 0) {
        cancelAutoSave();
        await saveAll();
    }
    var rows = table.getRows();
    ...
    table.addRow(newData, true);  // line 641
}
```

**Step 2: Replace with page-1-first version**

Replace the entire `addRow` function (lines 614–642):

```javascript
async function addRow() {
    // Save any dirty rows before adding a new one
    if (dirtyRowIds.size > 0) {
        cancelAutoSave();
        await saveAll();
    }

    // Always navigate to page 1 first so the new row appears at the top
    // (server returns ORDER BY id DESC, so newest = page 1 top)
    if (table.getPage() !== 1) {
        await table.setPage(1);
    }

    var rows = table.getRows();
    var newData = {
        _is_new: true,
        created_by: currentUser,
        entry_date: getTodayDate(),
        equipment_name: currentEquipment,
        quantity_uom: defaultUom || undefined
    };
    if (rows.length > 0) {
        var topData = rows[0].getData();
        var copyFields = [
            'source_type', 'source_id', 'source_display', 'barge_name', 'cargo_name',
            'delay_name', 'operation_type', 'quantity_uom', 'route_name',
            'entry_date', 'shift', 'system_name', 'berth_name', 'shift_incharge', 'operator_name', 'remarks'
        ];
        copyFields.forEach(function(f) {
            if (topData[f] != null && topData[f] !== '') newData[f] = topData[f];
        });
        // from_time of new row = to_time of previous top row
        if (topData.to_time) newData.from_time = topData.to_time;
    }
    table.addRow(newData, true);
}
```

**Step 3: Manual test**
1. Open LUEU01 for any equipment that has >20 rows
2. Navigate to page 2
3. Click "+ Add Row"
4. Verify the view jumps to page 1 and the new blank row is at the very top with yellow highlight

**Step 4: Commit**
```bash
git add modules/LUEU01/lueu01.html
git commit -m "fix(LUEU01): navigate to page 1 before adding new row so it always appears at top"
```

---

## Task 2: LUEU01 Model — Soft-Delete Function

**Files:**
- Modify: `modules/LUEU01/model.py`

**Step 1: Update `get_all_lines` to filter deleted rows**

In [modules/LUEU01/model.py:13](modules/LUEU01/model.py), the initial `where_clauses` block — add a default filter:

```python
# After line: where_clauses, params = [], []
where_clauses.append('(l.is_deleted IS NOT TRUE)')
```

Wait — the table alias is not used in the current query. The query is:
```sql
SELECT * FROM lueu_lines {where_sql} ORDER BY id DESC LIMIT %s OFFSET %s
```

No alias. So add to `where_clauses` right after initialisation:

```python
where_clauses, params = [], []
where_clauses.append('(is_deleted IS NOT TRUE)')   # ← add this line
```

**Step 2: Replace `delete_lines` with `soft_delete_lines`**

Replace the entire `delete_lines` function (lines 151–157):

```python
def soft_delete_lines(ids, username=None):
    """Soft-delete EU lines. Returns list of dicts for lines that were billed+invoiced,
    so the caller can trigger auto-CN creation.
    Each dict: {eu_line_id, bill_line_id, bill_id, invoice_id, invoice_number}
    """
    from datetime import datetime
    conn = get_db()
    cur = get_cursor(conn)
    today = datetime.now().strftime('%Y-%m-%d')

    invoiced_lines = []

    for line_id in ids:
        # Fetch the line first to check billing status
        cur.execute('SELECT * FROM lueu_lines WHERE id = %s', [line_id])
        line = cur.fetchone()
        if not line:
            continue

        # Check if this line is referenced by any bill_lines that are in an invoice
        cur.execute('''
            SELECT
                bl.id AS bill_line_id,
                bl.bill_id,
                ibm.invoice_id,
                ih.invoice_number
            FROM bill_lines bl
            JOIN invoice_bill_mapping ibm ON ibm.bill_id = bl.bill_id
            JOIN invoice_header ih ON ih.id = ibm.invoice_id
            WHERE bl.eu_line_id = %s
              AND ih.invoice_status NOT IN ('Cancelled')
        ''', [line_id])
        invoice_refs = [dict(r) for r in cur.fetchall()]

        for ref in invoice_refs:
            ref['eu_line_id'] = line_id
            ref['eu_line'] = dict(line)
            invoiced_lines.append(ref)

        # Soft-delete regardless
        cur.execute('''
            UPDATE lueu_lines
            SET is_deleted = TRUE, deleted_by = %s, deleted_date = %s
            WHERE id = %s
        ''', [username, today, line_id])

    conn.commit()
    conn.close()
    return invoiced_lines
```

**Step 3: Verify model imports compile**
```bash
cd d:/PORTMAN && python -c "from modules.LUEU01 import model; print('OK')"
```
Expected: `OK`

**Step 4: Commit**
```bash
git add modules/LUEU01/model.py
git commit -m "feat(LUEU01): replace hard-delete with soft_delete_lines, filter is_deleted from get_all_lines"
```

---

## Task 3: FDCN01 Model — Auto-CN from EU Line Deletion

**Files:**
- Modify: `modules/FDCN01/model.py`

**Step 1: Add `create_eu_deletion_cn` function**

Add this new function at the end of [modules/FDCN01/model.py](modules/FDCN01/model.py), after `update_gst_details`:

```python
def create_eu_deletion_cn(invoiced_line_refs, username=None):
    """
    Create Credit Note(s) when EU lines that are already invoiced get soft-deleted.

    invoiced_line_refs: list of dicts from soft_delete_lines(), each containing:
        eu_line_id, eu_line (full row dict), bill_line_id, bill_id, invoice_id, invoice_number

    Groups by invoice_id and creates one CN per affected invoice.
    CN goes to Draft status (requires approver to approve).
    Returns list of (fdcn_id, doc_number) tuples.
    """
    from datetime import datetime
    from database import get_db as _gdb, get_cursor as _gc

    if not invoiced_line_refs:
        return []

    conn = _gdb()
    cur = _gc(conn)
    now = datetime.now().strftime('%Y-%m-%d')
    results = []

    # Group refs by invoice_id
    from collections import defaultdict
    by_invoice = defaultdict(list)
    for ref in invoiced_line_refs:
        by_invoice[ref['invoice_id']].append(ref)

    for invoice_id, refs in by_invoice.items():
        # Fetch invoice header for customer details
        cur.execute('SELECT * FROM invoice_header WHERE id = %s', [invoice_id])
        invoice = cur.fetchone()
        if not invoice:
            continue

        cn_lines = []
        subtotal = cgst_total = sgst_total = igst_total = 0.0

        for ref in refs:
            bill_line_id = ref['bill_line_id']
            bill_id = ref['bill_id']
            eu_line = ref['eu_line']

            # Find the corresponding invoice_line
            # Match: invoice_id + bill_id (via invoice_lines.bill_id) + service matching bill_line
            cur.execute('''
                SELECT il.*
                FROM invoice_lines il
                WHERE il.invoice_id = %s AND il.bill_id = %s
                LIMIT 1
            ''', [invoice_id, bill_id])
            inv_line = cur.fetchone()
            if not inv_line:
                continue

            qty   = float(inv_line.get('quantity') or 0)
            rate  = float(inv_line.get('rate') or 0)
            la    = round(qty * rate, 2)
            cgst  = float(inv_line.get('cgst_amount') or 0)
            sgst  = float(inv_line.get('sgst_amount') or 0)
            igst  = float(inv_line.get('igst_amount') or 0)
            lt    = round(la + cgst + sgst + igst, 2)

            subtotal   += la
            cgst_total += cgst
            sgst_total += sgst
            igst_total += igst

            eu_desc = (
                f"EU Line #{eu_line.get('id')} deleted — "
                f"{eu_line.get('cargo_name', '')} / "
                f"{eu_line.get('source_display', '')} / "
                f"Ref: {ref.get('invoice_number', '')}"
            )

            cn_lines.append({
                'invoice_line_id': inv_line['id'],
                'service_type_id': inv_line.get('service_type_id'),
                'service_name':    inv_line.get('service_name'),
                'service_description': eu_desc,
                'quantity':        qty,
                'uom':             inv_line.get('uom'),
                'original_rate':   rate,
                'revised_rate':    0,
                'rate_difference': -rate,
                'line_amount':     la,
                'gst_rate_id':     inv_line.get('gst_rate_id'),
                'cgst_rate':       float(inv_line.get('cgst_rate') or 0),
                'sgst_rate':       float(inv_line.get('sgst_rate') or 0),
                'igst_rate':       float(inv_line.get('igst_rate') or 0),
                'cgst_amount':     cgst,
                'sgst_amount':     sgst,
                'igst_amount':     igst,
                'line_total':      lt,
                'gl_code':         inv_line.get('gl_code'),
                'sac_code':        inv_line.get('sac_code'),
                'remarks':         eu_desc,
            })

        if not cn_lines:
            continue

        total_amount = round(subtotal + cgst_total + sgst_total + igst_total, 2)
        invoice_number = invoice.get('invoice_number', '')
        doc_number, prefix, seq, fy = get_next_doc_number('CN', now)

        cur.execute('''
            INSERT INTO fdcn_header
            (doc_number, doc_type, doc_date, doc_series, doc_series_seq, financial_year,
             original_invoice_id, original_invoice_number,
             customer_id, customer_type, customer_name,
             customer_gstin, customer_gst_state_code, customer_gl_code,
             subtotal, cgst_amount, sgst_amount, igst_amount, total_amount,
             doc_status, creation_type, remarks, created_by, created_date)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            RETURNING id
        ''', [
            doc_number, 'CN', now, prefix, seq, fy,
            invoice_id, invoice_number,
            invoice.get('customer_id'), invoice.get('customer_type'),
            invoice.get('customer_name'),
            invoice.get('customer_gstin'), invoice.get('customer_gst_state_code'),
            invoice.get('customer_gl_code'),
            subtotal, cgst_total, sgst_total, igst_total, total_amount,
            'Draft', 'eu_deletion',
            f'Auto CN: EU lines deleted — Ref Invoice {invoice_number}',
            username, now
        ])
        fdcn_id = cur.fetchone()['id']

        for line in cn_lines:
            cur.execute('''
                INSERT INTO fdcn_lines
                (fdcn_id, invoice_line_id, service_type_id, service_name, service_description,
                 quantity, uom, original_rate, revised_rate, rate_difference, line_amount,
                 gst_rate_id, cgst_rate, sgst_rate, igst_rate,
                 cgst_amount, sgst_amount, igst_amount, line_total,
                 gl_code, sac_code, remarks)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ''', [
                fdcn_id,
                line['invoice_line_id'], line['service_type_id'],
                line['service_name'], line['service_description'],
                line['quantity'], line['uom'],
                line['original_rate'], line['revised_rate'],
                line['rate_difference'], line['line_amount'],
                line['gst_rate_id'], line['cgst_rate'],
                line['sgst_rate'], line['igst_rate'],
                line['cgst_amount'], line['sgst_amount'],
                line['igst_amount'], line['line_total'],
                line['gl_code'], line['sac_code'], line['remarks']
            ])

        results.append((fdcn_id, doc_number))

    conn.commit()
    conn.close()
    return results
```

**Step 2: Verify**
```bash
cd d:/PORTMAN && python -c "from modules.FDCN01 import model; print('OK')"
```
Expected: `OK`

**Step 3: Commit**
```bash
git add modules/FDCN01/model.py
git commit -m "feat(FDCN01): add create_eu_deletion_cn for auto-CN when invoiced EU lines are soft-deleted"
```

---

## Task 4: LUEU01 Views — Wire Soft-Delete + CN Trigger

**Files:**
- Modify: `modules/LUEU01/views.py`

**Step 1: Find current delete endpoint**

In [modules/LUEU01/views.py](modules/LUEU01/views.py), find the `/api/module/LUEU01/delete` endpoint. It calls `model.delete_lines(ids)`.

**Step 2: Replace the delete endpoint entirely**

Replace the entire delete route function with:

```python
@bp.route('/api/module/LUEU01/delete', methods=['POST'])
@login_required
def delete_data():
    perms = get_perms()
    if not perms.get('can_delete'):
        return jsonify({'error': 'No permission to delete'}), 403

    ids = request.json.get('ids', [])
    if not ids:
        return jsonify({'error': 'No IDs provided'}), 400

    username = session.get('username')

    # Soft-delete; returns refs for any lines that are billed+invoiced
    invoiced_refs = model.soft_delete_lines(ids, username)

    # Auto-create CNs for invoiced lines
    cn_results = []
    if invoiced_refs:
        from modules.FDCN01 import model as fdcn_model
        created = fdcn_model.create_eu_deletion_cn(invoiced_refs, username)
        cn_results = [{'fdcn_id': fid, 'doc_number': dnum} for fid, dnum in created]

    return jsonify({
        'success': True,
        'deleted_count': len(ids),
        'auto_cn_created': cn_results   # frontend shows notification if non-empty
    })
```

**Step 3: Verify**
```bash
cd d:/PORTMAN && python -c "from modules.LUEU01 import views; print('OK')"
```
Expected: `OK`

**Step 4: Commit**
```bash
git add modules/LUEU01/views.py
git commit -m "feat(LUEU01): soft-delete endpoint triggers auto-CN in FDCN01 for invoiced EU lines"
```

---

## Task 5: FIN01 — Filter Deleted EU Lines from Billing

**Files:**
- Modify: `modules/FIN01/views.py`

There are two places `lueu_lines` is queried without filtering `is_deleted`.

**Step 1: Fix the `eu-lines` endpoint (line ~299)**

Find:
```python
cur.execute('''
    SELECT el.*, st.service_name
    FROM lueu_lines el
    LEFT JOIN finance_service_types st ON el.service_type_id = st.id
    WHERE el.source_type = %s AND el.source_id = %s
    ORDER BY el.is_billed ASC, el.id
''', [source_type, source_id])
```

Add `AND (el.is_deleted IS NOT TRUE)` to the WHERE clause:
```python
cur.execute('''
    SELECT el.*, st.service_name
    FROM lueu_lines el
    LEFT JOIN finance_service_types st ON el.service_type_id = st.id
    WHERE el.source_type = %s AND el.source_id = %s
      AND (el.is_deleted IS NOT TRUE)
    ORDER BY el.is_billed ASC, el.id
''', [source_type, source_id])
```

**Step 2: Fix the `customer-billables` endpoint (line ~578)**

Find:
```python
cur.execute("""
    SELECT el.*
    FROM lueu_lines el
    WHERE (el.is_billed = 0 OR el.is_billed IS NULL)
       OR (COALESCE(el.billed_quantity, 0) < el.quantity)
""")
```

Add the filter:
```python
cur.execute("""
    SELECT el.*
    FROM lueu_lines el
    WHERE ((el.is_billed = 0 OR el.is_billed IS NULL)
        OR (COALESCE(el.billed_quantity, 0) < el.quantity))
      AND (el.is_deleted IS NOT TRUE)
""")
```

**Step 3: Verify**
```bash
cd d:/PORTMAN && python -c "from modules.FIN01 import views; print('OK')"
```
Expected: `OK`

**Step 4: Commit**
```bash
git add modules/FIN01/views.py
git commit -m "fix(FIN01): exclude is_deleted EU lines from billing screens"
```

---

## Task 6: LUEU01 HTML — Replace Delete Button with Soft-Delete Checkbox Column

**Files:**
- Modify: `modules/LUEU01/lueu01.html`

This task has the most UI changes. Work through them one at a time.

### 6a — Remove the toolbar Delete button

Find (line ~146):
```html
{% if permissions.can_delete %}<button class="btn btn-delete" onclick="deleteSelected()">Delete Selected</button>{% endif %}
```
**Delete that entire line.**

### 6b — Add `is_deleted` column to table definition

In the `columns` array inside `initTable` (around line 489), add a new column **at the end** of the array, just before the closing `]`:

```javascript
// Only render the delete checkbox column if user has can_delete
...(permissions.can_delete ? [{
    title: "Del",
    field: "is_deleted",
    width: 50,
    hozAlign: "center",
    headerSort: false,
    formatter: function(cell) {
        const data = cell.getRow().getData();
        if (!data.id) return '';  // unsaved new row — no delete option yet
        if (data.is_deleted) {
            return '<span style="color:#e74c3c;font-size:10px;font-weight:700;" title="Deleted by ' + (data.deleted_by||'') + ' on ' + (data.deleted_date||'') + '">DELETED</span>';
        }
        return '<input type="checkbox" class="eu-del-cb" data-id="' + data.id + '" title="Mark for deletion" onclick="event.stopPropagation()">';
    },
    cellClick: function(e, cell) {
        // Handled by the checkbox onclick above — prevent row selection
    }
}] : [])
```

### 6c — Remove the old `deleteSelected` function

Find and **delete** the entire `deleteSelected` function (lines 644–658):
```javascript
function deleteSelected() {
    const selected = table.getSelectedRows();
    ...
    selected.forEach(row => row.delete());
}
```

### 6d — Add new `handleSoftDelete` function

Add this new function where `deleteSelected` was:

```javascript
function handleSoftDelete(checkbox) {
    const euLineId = checkbox.getAttribute('data-id');
    if (!confirm('Mark this EU line for deletion? If it has been invoiced, a Credit Note will be automatically created in FDCN01.')) {
        checkbox.checked = false;
        return;
    }

    fetch('/api/module/LUEU01/delete', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({ids: [parseInt(euLineId)]})
    })
    .then(r => r.json())
    .then(result => {
        if (!result.success) {
            alert('Error: ' + (result.error || 'Unknown error'));
            checkbox.checked = false;
            return;
        }
        // Refresh the table to show DELETED badge
        table.replaceData();

        if (result.auto_cn_created && result.auto_cn_created.length > 0) {
            const cnNums = result.auto_cn_created.map(cn => cn.doc_number).join(', ');
            alert('EU line deleted. Credit Note(s) automatically created in FDCN01: ' + cnNums + '\n\nThese require approver sign-off before posting to SAP.');
        }
    })
    .catch(() => {
        alert('Network error during delete.');
        checkbox.checked = false;
    });
}
```

### 6e — Wire up the checkbox delegation

After the `table.on("cellEdited", ...)` blocks (around line 607), add a delegated click handler that fires when a `.eu-del-cb` checkbox is checked:

```javascript
document.getElementById('table').addEventListener('change', function(e) {
    if (e.target && e.target.classList.contains('eu-del-cb') && e.target.checked) {
        handleSoftDelete(e.target);
    }
});
```

### 6f — Row formatter: grey out deleted rows

In the `rowFormatter` callback (lines 553–560), extend it:

```javascript
rowFormatter: function(row) {
    var el = row.getElement();
    var data = row.getData();
    if (data._is_new) {
        el.classList.add('new-row-highlight');
    } else {
        el.classList.remove('new-row-highlight');
    }
    if (data.is_deleted) {
        el.style.opacity = '0.45';
        el.style.textDecoration = 'line-through';
    } else {
        el.style.opacity = '';
        el.style.textDecoration = '';
    }
},
```

**Wait** — but `get_all_lines` now filters `is_deleted IS NOT TRUE`, so deleted rows won't appear in the normal view. The "DELETED" badge + strikethrough is moot if the server never returns them. That is the intended behavior: deleted rows are simply hidden from the table.

So the formatter change for strikethrough is NOT needed. The `is_deleted` column formatter already handles showing "DELETED" badge if, for any reason, a deleted row is returned — but normally deleted rows won't appear.

**Skip 6f — the `rowFormatter` change is not needed.**

**Step 7: Manual test**
1. Open LUEU01 for any equipment
2. Confirm the "Delete Selected" button is gone
3. If `can_delete = true`: a "Del" column appears at the far right with checkboxes
4. Tick a checkbox on an **unbilled** row → confirm dialog → row disappears from table (soft-deleted, filtered out on refresh)
5. Tick a checkbox on a **billed+invoiced** row → confirm dialog → row disappears + alert shows CN doc number created in FDCN01
6. Navigate to FDCN01 list → verify the auto-created CN appears with status "Draft" and `creation_type = eu_deletion`

**Step 8: Commit**
```bash
git add modules/LUEU01/lueu01.html
git commit -m "feat(LUEU01): replace delete button with per-row soft-delete checkbox; show auto-CN notification"
```

---

## Task 7: FDCN01 Entry — Add Manual DN/CN Mode

Currently the entry form only supports Rate Revision (load invoice lines, enter revised rates). Add a "Manual Entry" mode where the user freely adds line items.

**Files:**
- Modify: `modules/FDCN01/fdcn01_entry.html`

### 7a — Add Entry Type selector and Doc Type selector to Document Details section

After the existing `<div class="form-grid">` opening in the "Document Details" section (around line 65), add two new fields **at the beginning** of the grid:

```html
<div class="form-field">
    <label>Entry Type</label>
    <select id="entryType" onchange="onEntryTypeChange()">
        <option value="rate_revision">Rate Revision</option>
        <option value="manual">Manual Entry</option>
    </select>
</div>
<div class="form-field" id="docTypeField" style="display:none">
    <label>Document Type</label>
    <select id="docTypeSel">
        <option value="DN">Debit Note (DN)</option>
        <option value="CN">Credit Note (CN)</option>
    </select>
</div>
```

### 7b — Rename the lines section header conditionally

Change the `<h3>Rate Revision Lines</h3>` to have an `id`:
```html
<h3 id="linesSectionTitle">Rate Revision Lines</h3>
```

Change the subtitle paragraph to have an `id`:
```html
<p id="linesSectionSubtitle" style="font-size:11px;color:var(--text-muted,#7f8c8d);margin:0 0 8px 0;">
    Revised rates are auto-filled from the selected agreement. Check lines to include.
</p>
```

### 7c — Add the "Add Manual Line" button (hidden by default)

After the table closing `</table>` tag and before `<div id="directionNote"...>`, add:

```html
<div id="manualAddRow" style="display:none;margin-top:8px;">
    <button class="btn" style="background:#27ae60;color:white;padding:4px 12px;font-size:11px;" onclick="addManualLine()">+ Add Line</button>
</div>
```

### 7d — Update the lines table header to support both modes

Replace the current `<thead>` of the lines table with a mode-aware version:

```html
<thead id="linesTableHead">
    <!-- Rendered by JS based on entry type -->
</thead>
```

### 7e — Add JS: `onEntryTypeChange`, `addManualLine`, and head-render functions

Add these new functions to the `<script>` block (add before the closing `</script>`):

```javascript
function onEntryTypeChange() {
    const type = document.getElementById('entryType').value;
    const manualAdd = document.getElementById('manualAddRow');
    const docTypeField = document.getElementById('docTypeField');
    const sectionTitle = document.getElementById('linesSectionTitle');
    const sectionSubtitle = document.getElementById('linesSectionSubtitle');
    const thead = document.getElementById('linesTableHead');
    const selectAll = document.getElementById('selectAll');

    // Clear existing lines when switching mode
    document.getElementById('linesBody').innerHTML = '';
    updateTotals();

    if (type === 'manual') {
        manualAdd.style.display = '';
        docTypeField.style.display = '';
        sectionTitle.textContent = 'Manual Line Items';
        sectionSubtitle.textContent = 'Add line items manually. Doc type (DN/CN) is set above.';
        thead.innerHTML = `<tr>
            <th class="cb-cell"></th>
            <th>Service Type</th>
            <th>Description</th>
            <th style="width:70px">Qty</th>
            <th style="width:50px">UOM</th>
            <th style="width:90px">Rate</th>
            <th style="width:100px">Amount</th>
            <th style="width:60px">GST %</th>
            <th style="width:80px">CGST</th>
            <th style="width:80px">SGST</th>
            <th style="width:80px">IGST</th>
            <th style="width:100px">Line Total</th>
            <th style="width:40px"></th>
        </tr>`;
        if (selectAll) selectAll.style.display = 'none';
    } else {
        manualAdd.style.display = 'none';
        docTypeField.style.display = 'none';
        sectionTitle.textContent = 'Rate Revision Lines';
        sectionSubtitle.textContent = 'Revised rates are auto-filled from the selected agreement. Check lines to include.';
        thead.innerHTML = `<tr>
            <th class="cb-cell"><input type="checkbox" id="selectAll" onchange="toggleSelectAll()"></th>
            <th>Invoice</th>
            <th>Service</th>
            <th style="width:70px">Qty</th>
            <th style="width:50px">UOM</th>
            <th style="width:90px">Orig. Rate</th>
            <th style="width:90px">Revised Rate</th>
            <th style="width:90px">Difference</th>
            <th style="width:100px">Amount</th>
            <th style="width:60px">GST %</th>
            <th style="width:80px">CGST</th>
            <th style="width:80px">SGST</th>
            <th style="width:80px">IGST</th>
            <th style="width:100px">Line Total</th>
        </tr>`;
    }
}

// Fetch service types for manual line dropdown (cached)
let serviceTypesList = [];
async function getServiceTypes() {
    if (serviceTypesList.length) return serviceTypesList;
    const res = await fetch('/api/module/FIN01/service-types');
    const data = await res.json();
    serviceTypesList = data;
    return serviceTypesList;
}

async function addManualLine() {
    const services = await getServiceTypes();
    const tbody = document.getElementById('linesBody');
    const rowId = 'ml_' + Date.now();

    // Build service options
    const opts = services.map(s =>
        `<option value="${s.id}" data-cgst="${s.cgst_rate||0}" data-sgst="${s.sgst_rate||0}" data-igst="${s.igst_rate||0}" data-gst-rate-id="${s.gst_rate_id||''}" data-gl="${s.gl_code||''}" data-sac="${s.sac_code||''}" data-uom="${s.uom||''}">${s.service_name}</option>`
    ).join('');

    const tr = document.createElement('tr');
    tr.id = rowId;
    tr.innerHTML = `
        <td></td>
        <td>
            <select class="ml-service" style="width:100%;font-size:11px;" onchange="onManualServiceChange(this, '${rowId}')">
                <option value="">-- Select --</option>
                ${opts}
            </select>
        </td>
        <td><input class="ml-desc" type="text" style="width:100%;font-size:11px;" placeholder="Description"></td>
        <td><input class="ml-qty" type="number" step="0.01" min="0" style="width:100%;font-size:11px;text-align:right;" oninput="recalcManualLine('${rowId}')" value="0"></td>
        <td><input class="ml-uom" type="text" style="width:100%;font-size:11px;" placeholder="UOM"></td>
        <td><input class="ml-rate" type="number" step="0.01" min="0" style="width:100%;font-size:11px;text-align:right;" oninput="recalcManualLine('${rowId}')" value="0"></td>
        <td class="ml-amount" style="text-align:right;font-weight:600">0.00</td>
        <td class="ml-gstpct" style="text-align:right">0%</td>
        <td class="ml-cgst" style="text-align:right">0.00</td>
        <td class="ml-sgst" style="text-align:right">0.00</td>
        <td class="ml-igst" style="text-align:right">0.00</td>
        <td class="ml-linetotal" style="text-align:right;font-weight:700">0.00</td>
        <td style="text-align:center"><button onclick="document.getElementById('${rowId}').remove(); updateTotals();" style="background:#e74c3c;color:white;border:none;padding:2px 6px;border-radius:3px;cursor:pointer;font-size:10px;">✕</button></td>
    `;
    tbody.appendChild(tr);
}

function onManualServiceChange(sel, rowId) {
    const opt = sel.options[sel.selectedIndex];
    const tr = document.getElementById(rowId);
    tr.querySelector('.ml-uom').value = opt.getAttribute('data-uom') || '';
    recalcManualLine(rowId);
}

function getGstRates(tr) {
    // For manual lines, read from the selected service option
    const sel = tr.querySelector('.ml-service');
    if (!sel) return {cgst: 0, sgst: 0, igst: 0, gstRateId: '', glCode: '', sacCode: ''};
    const opt = sel.options[sel.selectedIndex];
    return {
        cgst:      parseFloat(opt.getAttribute('data-cgst') || 0),
        sgst:      parseFloat(opt.getAttribute('data-sgst') || 0),
        igst:      parseFloat(opt.getAttribute('data-igst') || 0),
        gstRateId: opt.getAttribute('data-gst-rate-id') || '',
        glCode:    opt.getAttribute('data-gl') || '',
        sacCode:   opt.getAttribute('data-sac') || '',
    };
}

function recalcManualLine(rowId) {
    const tr = document.getElementById(rowId);
    if (!tr) return;
    const qty  = parseFloat(tr.querySelector('.ml-qty').value) || 0;
    const rate = parseFloat(tr.querySelector('.ml-rate').value) || 0;
    const la   = round2(qty * rate);
    const gst  = getGstRates(tr);
    const cgst = round2(la * gst.cgst / 100);
    const sgst = round2(la * gst.sgst / 100);
    const igst = round2(la * gst.igst / 100);
    const lt   = round2(la + cgst + sgst + igst);
    const pct  = gst.cgst + gst.sgst + gst.igst;

    tr.querySelector('.ml-amount').textContent   = la.toFixed(2);
    tr.querySelector('.ml-gstpct').textContent   = pct + '%';
    tr.querySelector('.ml-cgst').textContent     = cgst.toFixed(2);
    tr.querySelector('.ml-sgst').textContent     = sgst.toFixed(2);
    tr.querySelector('.ml-igst').textContent     = igst.toFixed(2);
    tr.querySelector('.ml-linetotal').textContent = lt.toFixed(2);
    updateTotals();
}

function round2(n) { return Math.round((n + Number.EPSILON) * 100) / 100; }
```

### 7f — Update `saveDocument()` to handle manual mode

Find the existing `saveDocument` function. Add logic at the top to collect manual lines:

```javascript
async function saveDocument() {
    const entryType = document.getElementById('entryType').value;
    let lines = [];

    if (entryType === 'manual') {
        // Collect manual line rows
        const rows = document.querySelectorAll('#linesBody tr[id^="ml_"]');
        rows.forEach(tr => {
            const sel = tr.querySelector('.ml-service');
            const opt = sel ? sel.options[sel.selectedIndex] : null;
            if (!opt || !opt.value) return;

            const qty  = parseFloat(tr.querySelector('.ml-qty').value)  || 0;
            const rate = parseFloat(tr.querySelector('.ml-rate').value) || 0;
            const la   = round2(qty * rate);
            const gst  = getGstRates(tr);
            const cgst = round2(la * gst.cgst / 100);
            const sgst = round2(la * gst.sgst / 100);
            const igst = round2(la * gst.igst / 100);
            const lt   = round2(la + cgst + sgst + igst);

            lines.push({
                invoice_line_id:     null,
                service_type_id:     parseInt(opt.value),
                service_name:        opt.text,
                service_description: tr.querySelector('.ml-desc').value.trim(),
                quantity:            qty,
                uom:                 tr.querySelector('.ml-uom').value.trim(),
                original_rate:       0,
                revised_rate:        rate,
                rate_difference:     rate,
                line_amount:         la,
                gst_rate_id:         gst.gstRateId || null,
                cgst_rate:           gst.cgst,
                sgst_rate:           gst.sgst,
                igst_rate:           gst.igst,
                cgst_amount:         cgst,
                sgst_amount:         sgst,
                igst_amount:         igst,
                line_total:          lt,
                gl_code:             gst.glCode,
                sac_code:            gst.sacCode,
                remarks:             '',
            });
        });

        if (!lines.length) { alert('Add at least one line item.'); return; }

        // For manual mode, doc_type comes from the selector
        const docType = document.getElementById('docTypeSel').value;

        // Build subtotals
        let subtotal = 0, cgstAmt = 0, sgstAmt = 0, igstAmt = 0;
        lines.forEach(l => { subtotal += l.line_amount; cgstAmt += l.cgst_amount; sgstAmt += l.sgst_amount; igstAmt += l.igst_amount; });

        const payload = {
            header: {
                id:                     document.getElementById('fdcnId').value || null,
                doc_type:               docType,
                doc_date:               document.getElementById('docDate').value,
                original_invoice_id:    getSelectedInvoiceId(),   // see note below
                original_invoice_number: getSelectedInvoiceNumber(),
                customer_id:            document.getElementById('customerId').value,
                customer_type:          document.getElementById('customerType').value,
                customer_name:          document.getElementById('customerId').options[document.getElementById('customerId').selectedIndex]?.text || '',
                customer_gstin:         document.getElementById('customerGstin').value,
                customer_gst_state_code: document.getElementById('customerGstStateCode')?.value || '',
                customer_gl_code:       document.getElementById('customerGlCode').value,
                subtotal:               round2(subtotal),
                cgst_amount:            round2(cgstAmt),
                sgst_amount:            round2(sgstAmt),
                igst_amount:            round2(igstAmt),
                total_amount:           round2(subtotal + cgstAmt + sgstAmt + igstAmt),
                creation_type:          'manual',
                remarks:                document.getElementById('remarks').value.trim(),
            },
            lines
        };

        const res = await fetch(`/api/module/FDCN01/save`, {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify(payload)
        });
        const result = await res.json();
        if (result.success) {
            alert('Saved: ' + result.doc_number);
            location.href = '/module/FDCN01/entry?id=' + result.id;
        } else {
            alert('Error: ' + (result.error || 'Unknown'));
        }
        return;  // don't fall through to existing save logic
    }

    // ... existing rate_revision save logic continues below unchanged ...
}
```

**Note:** `getSelectedInvoiceId()` and `getSelectedInvoiceNumber()` — these are helper functions that read the first checked invoice in `#invoiceList`. Add them:

```javascript
function getSelectedInvoiceId() {
    const checked = document.querySelector('#invoiceList input[type=checkbox]:checked');
    return checked ? checked.value : null;
}
function getSelectedInvoiceNumber() {
    const checked = document.querySelector('#invoiceList input[type=checkbox]:checked');
    return checked ? checked.getAttribute('data-inv-num') : '';
}
```

Make sure the existing invoice checkboxes have `data-inv-num` attribute — if they don't, add it when rendering the invoice list.

### 7g — Add `customer_gst_state_code` hidden field

In the Customer section (if not already present), add:
```html
<input type="hidden" id="customerGstStateCode">
```
And populate it when customer changes (in the existing `onCustomerChange` function, add: `document.getElementById('customerGstStateCode').value = cust.gst_state_code || '';`).

**Step 8: Manual test**
1. Open FDCN01 → New Entry
2. Select "Manual Entry" from Entry Type
3. Verify: Agreement section hidden is irrelevant (rate revision workflow disappears), "Document Type" (DN/CN) selector appears, "Add Line" button appears
4. Select a customer, pick an invoice reference, add line items with service type + qty + rate
5. Verify GST auto-calculates per line
6. Save → document created with `creation_type = manual`
7. Switch back to "Rate Revision" → old workflow restored

**Step 9: Commit**
```bash
git add modules/FDCN01/fdcn01_entry.html
git commit -m "feat(FDCN01): add Manual Entry mode for free-form DN/CN alongside existing Rate Revision mode"
```

---

## Task 8: FDCN01 Model — Include `creation_type` in Save

The `save_fdcn_header` function in [modules/FDCN01/model.py](modules/FDCN01/model.py) needs to persist `creation_type`.

**Step 1: Update the INSERT statement**

In `save_fdcn_header`, find the INSERT statement. The column list currently ends with `..., remarks, created_by, created_date`. Add `creation_type`:

```python
cur.execute('''INSERT INTO fdcn_header
    (doc_number, doc_type, doc_date, doc_series, doc_series_seq, financial_year,
     original_invoice_id, original_invoice_number,
     customer_id, customer_type, customer_name,
     customer_gstin, customer_gst_state_code, customer_gl_code,
     subtotal, cgst_amount, sgst_amount, igst_amount, total_amount,
     doc_status, creation_type, remarks, created_by, created_date)
    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
    RETURNING id''', [
    doc_number, doc_type, doc_date, prefix, seq, fy,
    data.get('original_invoice_id'), data.get('original_invoice_number'),
    data.get('customer_id'), data.get('customer_type'), data.get('customer_name'),
    data.get('customer_gstin'), data.get('customer_gst_state_code'),
    data.get('customer_gl_code'),
    data.get('subtotal', 0), data.get('cgst_amount', 0),
    data.get('sgst_amount', 0), data.get('igst_amount', 0),
    data.get('total_amount', 0),
    data.get('doc_status', 'Draft'),
    data.get('creation_type', 'rate_revision'),   # ← new
    data.get('remarks'),
    username, now
])
```

**Step 2: Update the UPDATE statement similarly**

In the UPDATE branch, add `creation_type = %s` and include `data.get('creation_type', 'rate_revision')` in the params before `fdcn_id`.

**Step 3: Verify**
```bash
cd d:/PORTMAN && python -c "from modules.FDCN01 import model; print('OK')"
```
Expected: `OK`

**Step 4: Commit**
```bash
git add modules/FDCN01/model.py
git commit -m "feat(FDCN01): persist creation_type (rate_revision/manual/eu_deletion) on fdcn_header save"
```

---

## Task 9: FDCN01 List — Show Creation Type Badge

**Files:**
- Modify: `modules/FDCN01/fdcn01_list.html`

Add a "Type" column to the list table that shows a small badge: "Rate Rev", "Manual", "EU Del" depending on `creation_type`. This helps finance team distinguish auto-generated CNs from manual ones.

Look at the list table's column headers and add a column:
```html
<th>Created By</th>   <!-- find this or similar -->
```
Add adjacent:
```html
<th>Origin</th>
```

In the row rendering JS, add the cell:
```javascript
const originBadge = {
    'rate_revision': '<span style="background:#eaf2ff;color:#2e6da4;padding:1px 6px;border-radius:8px;font-size:9px;font-weight:700;">RATE REV</span>',
    'manual':        '<span style="background:#e8f8e8;color:#1a7a1a;padding:1px 6px;border-radius:8px;font-size:9px;font-weight:700;">MANUAL</span>',
    'eu_deletion':   '<span style="background:#fff3e0;color:#e65100;padding:1px 6px;border-radius:8px;font-size:9px;font-weight:700;">EU DEL</span>',
}[row.creation_type] || '<span style="background:#f0f0f0;color:#888;padding:1px 6px;border-radius:8px;font-size:9px;">—</span>';
```

**Commit:**
```bash
git add modules/FDCN01/fdcn01_list.html
git commit -m "feat(FDCN01): show origin badge (Rate Rev / Manual / EU Del) in list view"
```

---

## Task 10: Final Verification

**Step 1: Full flow test — Unbilled EU line deletion**
1. Open LUEU01, find a line where `is_billed = 0`
2. Tick its Del checkbox → confirm
3. Line disappears from table
4. FIN01 billing screen → that line no longer appears in "Load Billables"
5. FDCN01 list → no new CN created

**Step 2: Full flow test — Invoiced EU line deletion**
1. Find a line where `is_billed = 1` AND its bill_id is in an invoice
2. Tick its Del checkbox → confirm
3. Alert appears showing "Auto CN: CN/25-26/XXXX created"
4. FDCN01 list → new CN with status "Draft" and "EU DEL" badge
5. Approve the CN → status changes to "Approved"
6. Post to SAP

**Step 3: Manual DN/CN test**
1. FDCN01 → New Entry → Entry Type: "Manual Entry"
2. Set type to "Credit Note (CN)"
3. Pick customer + invoice reference
4. Add 2 service line items with qty + rate
5. GST auto-calculates
6. Save → CN created with `creation_type = manual`

**Step 4: New row ordering test**
1. LUEU01 with >20 rows → go to page 2
2. Click "+ Add Row" → table jumps to page 1, new row at top with yellow highlight

**Step 5: Final commit**
```bash
git add -A
git commit -m "chore: final cleanup — EU soft-delete + auto-CN + manual DN/CN + UI fix complete"
```
