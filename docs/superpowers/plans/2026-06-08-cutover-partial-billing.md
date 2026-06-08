# Cutover Partial Billing + Tab UI Refresh Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the Admin → Cutover tab mark a *partial* quantity of a cargo line as already-billed (leaving the balance billable in FIN01), and refresh the tab UI into a clean cargo line-items table.

**Architecture:** Add a pure, DB-free helper `compute_partial_billed` to `modules/ADMIN/cutover.py` that mirrors FIN01's `_mark_cargo_source_billed` math (accumulate `billed_quantity`, set `is_billed=1` only when fully covered). Wire it into the existing `_apply_billed` so cargo items can carry an optional `bill_quantity`. Rebuild the Cutover tab in `templates/admin.html` as three cards with a proper cargo table; Rate/Amount are display-only helpers. No new tables, no bill records, no SAP, no GST.

**Tech Stack:** Python 3 / Flask, psycopg (dict cursor), Jinja2 templates, vanilla JS, pytest.

**Spec:** [docs/superpowers/specs/2026-06-08-cutover-partial-billing-design.md](../specs/2026-06-08-cutover-partial-billing-design.md)

---

### Task 1: Pure helper `compute_partial_billed` (TDD)

**Files:**
- Modify: `modules/ADMIN/cutover.py` (add helper near the other pure helpers, after `validate_start_seq`, around line 28)
- Test: `test_cutover.py` (project root — append to existing pure-helper tests)

- [ ] **Step 1: Write the failing tests**

Append to `test_cutover.py`:

```python
# --- Partial cutover billing math ------------------------------------------

def test_compute_partial_billed_partial_below_total():
    # 50 of 100, nothing billed yet -> stays open (is_billed=0)
    assert cutover.compute_partial_billed(100, 0, 50) == (50.0, 0)


def test_compute_partial_billed_accumulates_onto_existing():
    # 20 already billed + 30 now = 50 of 100 -> still open
    assert cutover.compute_partial_billed(100, 20, 30) == (50.0, 0)


def test_compute_partial_billed_reaches_total_sets_flag():
    # 50 already + 50 now = 100 of 100 -> fully billed
    assert cutover.compute_partial_billed(100, 50, 50) == (100.0, 1)


def test_compute_partial_billed_caps_over_balance():
    # only 20 left, asking for 50 -> capped at 20, fully billed
    assert cutover.compute_partial_billed(100, 80, 50) == (100.0, 1)


def test_compute_partial_billed_defaults_to_full_balance_when_missing():
    # bill_qty None or 0 -> mark the whole remaining balance (back-compat)
    assert cutover.compute_partial_billed(100, 30, None) == (100.0, 1)
    assert cutover.compute_partial_billed(100, 30, 0) == (100.0, 1)


def test_compute_partial_billed_rounds_to_three_decimals():
    assert cutover.compute_partial_billed(10, 0, 3.3335) == (3.334, 0)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest test_cutover.py -k compute_partial_billed -v`
Expected: FAIL — `AttributeError: module 'modules.ADMIN.cutover' has no attribute 'compute_partial_billed'`

- [ ] **Step 3: Write the minimal implementation**

In `modules/ADMIN/cutover.py`, add immediately after the `validate_start_seq` function (before the `# ===== Lock state + audit =====` section):

```python
def compute_partial_billed(total, already, bill_qty):
    """New (billed_quantity, is_billed) for a partial cutover mark.

    total    -- declared total quantity on the row
    already  -- quantity already billed
    bill_qty -- quantity to mark billed now; None or <= 0 means "mark the whole
                remaining balance" (preserves the original all-or-nothing
                behaviour). Capped so we never bill past the total.

    Mirrors FIN01's _mark_cargo_source_billed: is_billed flips to 1 only once the
    accumulated billed quantity reaches the total."""
    total = float(total or 0)
    already = float(already or 0)
    balance = max(total - already, 0)
    if bill_qty is None or float(bill_qty) <= 0:
        bill_qty = balance
    else:
        bill_qty = min(float(bill_qty), balance)
    new_billed = round(already + bill_qty, 3)
    is_billed = 1 if new_billed >= total else 0
    return new_billed, is_billed
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest test_cutover.py -k compute_partial_billed -v`
Expected: PASS — 6 passed

- [ ] **Step 5: Commit**

```bash
git add modules/ADMIN/cutover.py test_cutover.py
git commit -m "feat(cutover): add compute_partial_billed pure helper"
```

---

### Task 2: Wire partial logic into `_apply_billed`

**Files:**
- Modify: `modules/ADMIN/cutover.py:129-156` (the `_apply_billed` function)

- [ ] **Step 1: Replace the cargo branch of `_apply_billed`**

Replace the existing `_apply_billed` function (currently lines 129-156) with:

```python
def _apply_billed(cur, cargo_items, service_ids, billed):
    """Flip billed flags. cargo_items: list of {'source_type','id','bill_quantity'}.
    billed=True  -> billed_quantity += bill_quantity (capped at total),
                    is_billed=1 only once fully covered. A missing/<=0
                    bill_quantity marks the whole remaining balance.
    billed=False -> is_billed=0, billed_quantity=0 (full reset).
    Returns counts dict. Raises ValueError on unknown source_type."""
    cargo_done, svc_done = 0, 0
    for item in cargo_items or []:
        mapping = cargo_source(item.get('source_type'))
        if not mapping:
            raise ValueError(f"Unknown cargo source_type: {item.get('source_type')}")
        table, qty_col = mapping
        if billed:
            # qty_col and table are trusted constants from CARGO_SOURCES (never
            # user input). Read current totals to support partial marking.
            cur.execute(
                f"SELECT {qty_col} AS total, COALESCE(billed_quantity, 0) AS already "
                f"FROM {table} WHERE id=%s",
                [item.get('id')])
            row = cur.fetchone()
            if not row:
                continue
            new_billed, is_billed = compute_partial_billed(
                row['total'], row['already'], item.get('bill_quantity'))
            cur.execute(
                f"UPDATE {table} SET is_billed=%s, billed_quantity=%s WHERE id=%s",
                [is_billed, new_billed, item.get('id')])
        else:
            cur.execute(
                f"UPDATE {table} SET is_billed=0, billed_quantity=0 WHERE id=%s",
                [item.get('id')])
        cargo_done += cur.rowcount
    for sid in service_ids or []:
        if billed:
            cur.execute("UPDATE service_records SET is_billed=1 WHERE id=%s", [sid])
        else:
            cur.execute("UPDATE service_records SET is_billed=0, bill_id=NULL WHERE id=%s", [sid])
        svc_done += cur.rowcount
    return {'cargo': cargo_done, 'services': svc_done}
```

> Note: `modules/ADMIN/views.py` needs **no change** — the `/api/cutover/mark-billed` route (line 1007) already forwards `cargo_items` verbatim, so the new `bill_quantity` key passes straight through.

- [ ] **Step 2: Run the full cutover test suite to confirm nothing regressed**

Run: `python -m pytest test_cutover.py -v`
Expected: PASS — all existing tests + the 6 new helper tests pass.

- [ ] **Step 3: Commit**

```bash
git add modules/ADMIN/cutover.py
git commit -m "feat(cutover): partial-quantity marking in _apply_billed"
```

---

### Task 3: Rebuild the Cutover tab markup + styles

**Files:**
- Modify: `templates/admin.html:597-632` (the `#cutover-tab` block)
- Modify: `templates/admin.html` styles (append new rules inside the existing `<style>` block, after the dark-theme rules near line 666)

- [ ] **Step 1: Replace the `#cutover-tab` block**

Replace lines 597-632 (the whole `<div id="cutover-tab" ...> ... </div>`) with:

```html
<div id="cutover-tab" class="tab-content">
    <div id="cutover-locked-banner" class="cut-banner" style="display:none">
        🔒 Cutover is LOCKED. Unlock to make changes.
    </div>

    <!-- Card 1: Document numbers -->
    <div class="admin-section cut-card">
        <h3 class="cut-card-title">Document Numbers</h3>
        <p class="cut-help">Set the first number the new system should issue after go-live. The seed acts as a floor — once real documents pass it, normal incrementing takes over.</p>
        <div class="cut-form-row">
            <div class="cut-field"><label>Invoice series</label><select id="cut-inv-series"></select></div>
            <div class="cut-field"><label>Financial year</label><input id="cut-inv-fy" placeholder="e.g. 2026-27"></div>
            <div class="cut-field"><label>Start at</label><input id="cut-inv-start" type="number" min="1"></div>
            <button class="btn" onclick="cutSetInvoiceSeed()">Set invoice start</button>
        </div>
        <div class="cut-form-row">
            <div class="cut-field"><label>Bill start at</label><input id="cut-bill-start" type="number" min="1"></div>
            <button class="btn" onclick="cutSetBillSeed()">Set bill start</button>
        </div>
        <table class="admin-table" id="cutSeedTable">
            <thead><tr><th>Type</th><th>Series</th><th>FY</th><th>Start</th><th>By</th></tr></thead>
            <tbody></tbody>
        </table>
    </div>

    <!-- Card 2: Mark items billed (partial) -->
    <div class="admin-section cut-card">
        <h3 class="cut-card-title">Mark Items Billed <span class="cut-pill">no invoice · no SAP · no GST</span></h3>
        <p class="cut-help">Record what was already billed in the legacy system. Enter a <b>Bill Qty</b> per cargo line for partial billing — the remaining balance stays billable in FIN01. Rate &amp; Amount are for your reference only and are not saved.</p>
        <div class="cut-form-row">
            <div class="cut-field"><label>Type</label>
                <select id="cut-cust-type" onchange="cutLoadCustomers()"><option>Customer</option><option>Agent</option></select>
            </div>
            <div class="cut-field"><label>Customer / Agent</label><select id="cut-cust-id"></select></div>
            <button class="btn" onclick="cutLoadBillables()">Load items</button>
        </div>
        <div id="cut-billables" class="cut-billables"><p class="cut-muted">Select a customer/agent and click Load items.</p></div>
        <div class="cut-actions">
            <button class="btn" onclick="cutMarkBilled(true)">Mark selected billed</button>
            <button class="btn btn-danger" onclick="cutMarkBilled(false)">Unmark selected</button>
        </div>
    </div>

    <!-- Card 3: Lock -->
    <div class="admin-section cut-card">
        <h3 class="cut-card-title">Lock</h3>
        <p class="cut-help">Locking blocks all cutover writes (seeds and mark-billed). Do this once go-live data is finalized.</p>
        <button class="btn" onclick="cutToggleLock()" id="cut-lock-btn">Mark cutover complete (lock)</button>
    </div>
</div>
```

- [ ] **Step 2: Append the new styles**

Inside the existing `<style>` block in `templates/admin.html`, immediately **after** the line `body.dark-theme #banksTable input[type="text"] { ... }` (around line 666), add:

```css
/* ===== Cutover tab ===== */
.cut-banner { background:#b00020; color:#fff; padding:10px 14px; border-radius:6px; margin-bottom:14px; font-weight:600; }
.cut-card { margin-bottom:16px; }
.cut-card-title { margin:0 0 4px 0; display:flex; align-items:center; gap:10px; }
.cut-help { color:#718096; font-size:13px; margin:0 0 14px 0; max-width:900px; }
.cut-pill { font-size:11px; font-weight:600; color:#2b6cb0; background:#ebf8ff; border:1px solid #bee3f8; padding:2px 8px; border-radius:999px; }
.cut-form-row { display:flex; flex-wrap:wrap; align-items:flex-end; gap:12px; margin-bottom:12px; }
.cut-field { display:flex; flex-direction:column; gap:4px; }
.cut-field label { font-size:12px; font-weight:600; color:#4a5568; }
.cut-field input, .cut-field select { padding:7px 9px; border:1px solid #e2e8f0; border-radius:4px; min-width:160px; }
.cut-billables { margin:4px 0 12px 0; }
.cut-table-wrap { overflow-x:auto; border:1px solid #e2e8f0; border-radius:6px; }
.cut-cargo-table th.num, .cut-cargo-table td.num { text-align:right; white-space:nowrap; }
.cut-cargo-table input[type="number"] { width:90px; padding:5px 6px; border:1px solid #cbd5e0; border-radius:4px; text-align:right; }
.cut-subhead { margin:16px 0 6px 0; font-size:14px; color:#4a5568; }
.cut-muted { color:#a0aec0; font-style:italic; }
.cut-tag { font-size:10px; font-weight:700; color:#4a5568; background:#edf2f7; padding:1px 6px; border-radius:4px; vertical-align:middle; }
.cut-chip { font-size:11px; font-weight:600; padding:2px 8px; border-radius:999px; white-space:nowrap; }
.cut-chip-ok { color:#22543d; background:#c6f6d5; }
.cut-chip-block { color:#742a2a; background:#fed7d7; }
.cut-proof-link { color:#3182ce; text-decoration:none; }
.cut-proof-list a { display:inline-block; color:#3182ce; }
.cut-actions { display:flex; gap:10px; margin-top:6px; }

body.dark-theme .cut-help, body.dark-theme .cut-field label, body.dark-theme .cut-subhead { color:#a0aec0; }
body.dark-theme .cut-field input, body.dark-theme .cut-field select,
body.dark-theme .cut-cargo-table input[type="number"] { background:#1a202c; border-color:#4a5568; color:#e2e8f0; }
body.dark-theme .cut-table-wrap { border-color:#4a5568; }
body.dark-theme .cut-pill { background:#1a365d; border-color:#2c5282; color:#bee3f8; }
body.dark-theme .cut-tag { background:#4a5568; color:#e2e8f0; }
```

- [ ] **Step 3: Visual check (no automated test for templates)**

Run the app, open Admin → Cutover. Confirm three cards render, inputs align, and the (still old) JS hasn't broken the page. The cargo table is wired in Task 4. Expected: no console errors except possibly an empty `#cut-billables` until "Load items" is clicked.

- [ ] **Step 4: Commit**

```bash
git add templates/admin.html
git commit -m "feat(cutover): refresh tab into three cards"
```

---

### Task 4: Cargo line-items table + partial-quantity JS

**Files:**
- Modify: `templates/admin.html` — replace `cutLoadBillables` (lines 2134-2152) and `cutMarkBilled` (lines 2159-2166); add `cutProofCell`, `cutShowProofs`, `cutCalcAmount`. Keep `cutToggleAll` (lines 2154-2157) as-is.

- [ ] **Step 1: Replace `cutLoadBillables` and add the table helpers**

Replace the existing `cutLoadBillables` function (lines 2134-2152) with the following block (this defines `cutLoadBillables`, `cutProofCell`, `cutShowProofs`, and `cutCalcAmount`):

```javascript
async function cutLoadBillables() {
    const type = document.getElementById('cut-cust-type').value;
    const id = document.getElementById('cut-cust-id').value;
    const host = document.getElementById('cut-billables');
    if (!id) { host.innerHTML = '<p class="cut-muted">Select a customer/agent and click Load items.</p>'; return; }
    host.innerHTML = '<p class="cut-muted">Loading…</p>';
    let data;
    try {
        data = await (await fetch(`/api/module/FIN01/customer-billables/${type}/${id}`)).json();
    } catch (e) { host.innerHTML = '<p class="cut-muted">Failed to load items.</p>'; return; }
    const cargo = data.cargo_handling || [];
    const svcs = data.other_services || [];

    let html = '<h4 class="cut-subhead">Cargo</h4>';
    if (!cargo.length) {
        html += '<p class="cut-muted">No unbilled cargo for this customer.</p>';
    } else {
        html += '<div class="cut-table-wrap"><table class="admin-table cut-cargo-table"><thead><tr>'
            + '<th><input type="checkbox" id="cut-select-all" onclick="cutToggleAll(this)"></th>'
            + '<th>Source</th><th>Status</th><th>Service</th><th>Cargo</th><th>Proof Docs</th>'
            + '<th>BL Date</th><th class="num">BL Qty</th><th class="num">Billed</th>'
            + '<th class="num">Balance</th><th class="num">Bill Qty</th><th class="num">Rate</th>'
            + '<th class="num">Amount</th></tr></thead><tbody>';
        cargo.forEach(c => {
            const total = Number(c.total_quantity || 0);
            const billed = Number(c.billed_quantity || 0);
            const balance = Number(c.billable_quantity || 0);
            const uom = c.uom || '';
            const tag = c.cargo_source_type === 'VCN_IMPORT' ? 'Import'
                      : c.cargo_source_type === 'VCN_EXPORT' ? 'Export' : 'MBC';
            const chip = c.is_billable ? 'cut-chip-ok' : 'cut-chip-block';
            html += `<tr>
                <td><input type="checkbox" class="cut-cargo" data-st="${c.cargo_source_type}" data-id="${c.cargo_source_id}"></td>
                <td>${c.doc_label || ''} <span class="cut-tag">${tag}</span></td>
                <td><span class="cut-chip ${chip}">${c.doc_status || '—'}</span></td>
                <td>${c.service_name || ''}</td>
                <td>${c.cargo_name || ''}</td>
                <td>${cutProofCell(c)}</td>
                <td>${c.bl_date || ''}</td>
                <td class="num">${total} ${uom}</td>
                <td class="num">${billed}</td>
                <td class="num">${balance}</td>
                <td class="num"><input type="number" class="cut-billqty" min="0" max="${balance}" step="any" value="${balance}" oninput="cutCalcAmount(this)"></td>
                <td class="num"><input type="number" class="cut-rate" min="0" step="any" placeholder="0" oninput="cutCalcAmount(this)"></td>
                <td class="num cut-amount">0.00</td>
            </tr>`;
        });
        html += '</tbody></table></div>';
    }

    html += '<h4 class="cut-subhead">Other Services</h4>';
    if (!svcs.length) {
        html += '<p class="cut-muted">No unbilled services for this customer.</p>';
    } else {
        html += '<div class="cut-table-wrap"><table class="admin-table"><thead><tr>'
            + '<th></th><th>Service</th><th>Source</th></tr></thead><tbody>';
        html += svcs.map(s =>
            `<tr><td><input type="checkbox" class="cut-svc" data-id="${s.id}"></td>`
            + `<td>${s.service_name || ''}</td>`
            + `<td>${(s.source_type || '')} #${(s.source_id || '')}</td></tr>`).join('');
        html += '</tbody></table></div>';
    }
    host.innerHTML = html;
}

function cutProofCell(c) {
    // VCN cargo -> LDUD proof docs (needs ldud_id); MBC -> MBC docs (source_id = mbc_id)
    let mod = '', sid = '';
    if (c.cargo_source_type === 'MBC') { mod = 'MBC'; sid = c.source_id; }
    else if (c.ldud_id) { mod = 'LDUD'; sid = c.ldud_id; }
    if (!mod || !sid) return '<span class="cut-muted">—</span>';
    return `<a href="#" class="cut-proof-link" onclick="cutShowProofs(event, '${mod}', ${sid})">📎 View</a>`;
}

async function cutShowProofs(ev, mod, sid) {
    ev.preventDefault();
    const link = ev.currentTarget;
    link.textContent = 'Loading…';
    try {
        const j = await (await fetch(`/api/module/FIN01/proof_docs/by_source/${mod}/${sid}`)).json();
        const docs = j.docs || [];
        if (!docs.length) { link.textContent = 'No docs'; return; }
        const wrap = document.createElement('span');
        wrap.className = 'cut-proof-list';
        wrap.innerHTML = docs.map(d => `<a href="${d.file_url}" target="_blank">${d.original_filename}</a>`).join('<br>');
        link.replaceWith(wrap);
    } catch (e) { link.textContent = 'Error'; }
}

function cutCalcAmount(el) {
    const tr = el.closest('tr');
    const qtyEl = tr.querySelector('.cut-billqty');
    const rateEl = tr.querySelector('.cut-rate');
    let qty = Number(qtyEl.value || 0);
    const bal = Number(qtyEl.max || 0);
    if (qty > bal) { qty = bal; qtyEl.value = bal; }   // clamp to balance
    if (qty < 0) { qty = 0; qtyEl.value = 0; }
    const rate = Number(rateEl.value || 0);
    tr.querySelector('.cut-amount').textContent = (qty * rate).toFixed(2);
}
```

- [ ] **Step 2: Replace `cutMarkBilled` to send per-row Bill Qty**

Replace the existing `cutMarkBilled` function (lines 2159-2166) with:

```javascript
async function cutMarkBilled(billed) {
    const cargo_items = [...document.querySelectorAll('.cut-cargo:checked')].map(e => {
        const item = { source_type: e.dataset.st, id: parseInt(e.dataset.id, 10) };
        if (billed) {
            const q = e.closest('tr').querySelector('.cut-billqty');
            item.bill_quantity = q ? Number(q.value || 0) : 0;
        }
        return item;
    });
    const service_ids = [...document.querySelectorAll('.cut-svc:checked')].map(e => parseInt(e.dataset.id, 10));
    if (!cargo_items.length && !service_ids.length) { alert('Select at least one item'); return; }
    if (billed && cargo_items.some(c => !(c.bill_quantity > 0))) {
        alert('Enter a Bill Qty greater than 0 for each selected cargo line.');
        return;
    }
    if (!confirm(`${billed ? 'Mark' : 'Unmark'} ${cargo_items.length} cargo + ${service_ids.length} services?`)) return;
    const ok = await _cutPost('/admin/api/cutover/mark-billed', { cargo_items, service_ids, billed });
    if (ok) { alert('Done'); cutLoadBillables(); }
}
```

- [ ] **Step 3: Manual verification**

Run the app and test the full flow:
1. Admin → Cutover → Mark Items Billed → pick a Customer with cargo → **Load items**. Confirm the table shows Source/Status/Service/Cargo/Proof Docs/BL Date/BL Qty/Billed/Balance/Bill Qty/Rate/Amount.
2. Type a Rate → Amount updates to `Bill Qty × Rate`. Set Bill Qty above Balance → it clamps to Balance.
3. Set Bill Qty to **less than** Balance on a row, check it, **Mark selected billed** → confirm "Done".
4. Reload items → the same row reappears with **Billed** increased and **Balance** reduced (partial worked).
5. Open FIN01 → Generate Bill → same customer → confirm the row appears with the reduced billable quantity.
6. Click 📎 View on a row with proof docs → file links appear.

- [ ] **Step 4: Commit**

```bash
git add templates/admin.html
git commit -m "feat(cutover): cargo line-items table with partial Bill Qty"
```

---

### Task 5: Final verification + wrap-up

- [ ] **Step 1: Run the full test suite**

Run: `python -m pytest test_cutover.py -v`
Expected: PASS — all tests green.

- [ ] **Step 2: Confirm back-compat**

Re-confirm that marking a row with Bill Qty == Balance (the default) sets `is_billed=1` and the row disappears from billables on reload — i.e. the original all-or-nothing behaviour still works when the admin doesn't reduce the quantity.

- [ ] **Step 3: Push the branch (only if the user asks)**

```bash
git push -u origin feat/cutover-partial-billing
```

---

## Self-Review Notes

- **Spec coverage:** partial logic (Task 1+2), existing storage unchanged / no views.py change (Task 2 note), balance stays billable in FIN01 (verified Task 4 step 5 / Task 5 step 2), UI three-card refresh (Task 3), cargo table with exact columns incl. Rate/Amount helper-only (Task 4), services flag list (Task 4), proof docs lazy load (Task 4), unit tests in test_cutover.py (Task 1). All spec sections mapped.
- **Type/name consistency:** helper `compute_partial_billed(total, already, bill_qty) -> (new_billed, is_billed)` is used identically in Task 1 and Task 2. JS classes `.cut-cargo`, `.cut-svc`, `.cut-billqty`, `.cut-rate`, `.cut-amount`, `.cut-balance` and functions `cutCalcAmount`/`cutProofCell`/`cutShowProofs` are consistent across Task 3 (CSS) and Task 4 (JS). `cutToggleAll` is preserved and still targets `.cut-cargo`/`.cut-svc`.
- **No placeholders:** every code/command step is complete.
