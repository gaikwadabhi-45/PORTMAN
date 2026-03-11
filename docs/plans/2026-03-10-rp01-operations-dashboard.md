# RP01 Operations Dashboard Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add an Operations Dashboard page to RP01 with a WebDataRocks pivot table and 2 auto-connected Highcharts chart panels.

**Architecture:** New `dashboard/` submodule under `modules/RP01/RP01/`. Template loads WebDataRocks + Highcharts from CDN. Toolbar (source, date column, from/to datetime, load button) fetches data from the existing `/api/module/RP01/pivot/data/<source>` endpoint. Data is fed into WebDataRocks; on `reportcomplete` event, both chart panels call `pivot.highcharts.getData()` and re-render. No new backend API needed.

**Tech Stack:** Flask/Jinja2, WebDataRocks (CDN), Highcharts (CDN), webdatarocks.highcharts.js connector

---

### Task 1: Create the dashboard submodule (backend)

**Files:**
- Create: `modules/RP01/RP01/dashboard/__init__.py`
- Create: `modules/RP01/RP01/dashboard/views.py`
- Modify: `modules/RP01/RP01/views.py` (add import)

**Step 1: Create empty `__init__.py`**

```python
# modules/RP01/RP01/dashboard/__init__.py
# (empty)
```

**Step 2: Create `views.py` with page route**

```python
# modules/RP01/RP01/dashboard/views.py
from flask import render_template, session, redirect, url_for
from functools import wraps

from .. import bp


def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated


@bp.route('/module/RP01/dashboard/')
@login_required
def dashboard_index():
    return render_template('dashboard/dashboard.html',
                           username=session.get('username'))
```

**Step 3: Register the import in `modules/RP01/RP01/views.py`**

Add this line after the existing custom_report import (line 9):

```python
from .dashboard      import views as _dashboard_views           # noqa: registers dashboard routes on bp
```

**Step 4: Verify — run the Flask app, navigate to `/module/RP01/dashboard/`**

Expected: TemplateNotFound error (template not created yet — confirms route is registered).

**Step 5: Commit**

```bash
git add modules/RP01/RP01/dashboard/__init__.py modules/RP01/RP01/dashboard/views.py modules/RP01/RP01/views.py
git commit -m "feat(RP01): add dashboard submodule route"
```

---

### Task 2: Create the dashboard template — toolbar + WebDataRocks pivot

**Files:**
- Create: `modules/RP01/RP01/dashboard/dashboard.html`

**Step 1: Create the full template with CDN includes, toolbar, and WebDataRocks container**

The template extends `base.html` and includes:

**CDN scripts (in `{% block head %}`):**
```html
<link href="https://cdn.webdatarocks.com/latest/webdatarocks.min.css" rel="stylesheet">
<script src="https://cdn.webdatarocks.com/latest/webdatarocks.toolbar.min.js"></script>
<script src="https://cdn.webdatarocks.com/latest/webdatarocks.js"></script>
<script src="https://code.highcharts.com/highcharts.js"></script>
<script src="https://code.highcharts.com/highcharts-3d.js"></script>
<script src="https://code.highcharts.com/modules/exporting.js"></script>
<script src="https://cdn.webdatarocks.com/latest/webdatarocks.highcharts.js"></script>
```

**Toolbar HTML (same pattern as custom_report.html):**
```html
<div class="toolbar">
    <div class="toolbar-group">
        <label for="src-sel">Data Source</label>
        <select id="src-sel" onchange="onSourceChange()">
            <option value="mbc-ops">MBC Operation</option>
            <option value="vessel-ops">Vessel Operation</option>
            <option value="vessel-barge">Vessel - Barge Lines</option>
            <option value="lueu-equipment">LUEU - Equipment Utilization</option>
            <option value="mbc-tat">MBC TAT Analysis</option>
        </select>
    </div>
    <div class="toolbar-group">
        <label for="date-col-sel">Date Column</label>
        <select id="date-col-sel" style="min-width:170px"></select>
    </div>
    <div class="toolbar-group">
        <label for="f-from">From</label>
        <input type="datetime-local" id="f-from" style="min-width:155px">
    </div>
    <div class="toolbar-group">
        <label for="f-to">To</label>
        <input type="datetime-local" id="f-to" style="min-width:155px">
    </div>
    <button type="button" class="tb-btn primary" id="load-btn" onclick="loadData()">&#9654; Load Data</button>
</div>
```

**WebDataRocks container:**
```html
<div id="wdr-component" style="margin-bottom:16px;"></div>
```

**DATE_COLS JS object** — copy exactly from custom_report.html (lines 465-502).

**Init logic** — same as custom_report.html:
- Set default from/to datetime values
- Call `onSourceChange()` to populate date column dropdown
- Initialize WebDataRocks with empty data:

```javascript
var pivot = new WebDataRocks({
    container: '#wdr-component',
    toolbar: true,
    report: {
        dataSource: { data: [{ '': '' }] }
    },
    reportcomplete: function() {
        updateCharts();
    }
});
```

**`loadData()` function:**
```javascript
function loadData() {
    const src     = document.getElementById('src-sel').value;
    const dateCol = document.getElementById('date-col-sel').value;
    const from    = document.getElementById('f-from').value;
    const to      = document.getElementById('f-to').value;
    const btn     = document.getElementById('load-btn');

    btn.disabled = true;
    btn.textContent = 'Loading\u2026';
    setStatus('Fetching data\u2026', false);

    fetch(`/api/module/RP01/pivot/data/${src}?from_date=${from}&to_date=${to}&date_col=${dateCol}`)
        .then(r => { if (!r.ok) throw new Error('Server error ' + r.status); return r.json(); })
        .then(rows => {
            _source = src;
            pivot.updateData({ data: rows });
            setStatus(rows.length.toLocaleString() + ' rows loaded \u2014 ' + srcLabel(src) + '.', false);
        })
        .catch(err => { console.error(err); setStatus('Error: ' + err.message, true); })
        .finally(() => { btn.disabled = false; btn.innerHTML = '&#9654; Load Data'; });
}
```

**Step 2: Verify — run app, go to `/module/RP01/dashboard/`, confirm toolbar renders and WebDataRocks pivot appears (empty).**

**Step 3: Commit**

```bash
git add modules/RP01/RP01/dashboard/dashboard.html
git commit -m "feat(RP01): dashboard template with toolbar + WebDataRocks pivot"
```

---

### Task 3: Add the 2 Highcharts chart panels

**Files:**
- Modify: `modules/RP01/RP01/dashboard/dashboard.html`

**Step 1: Add chart panel HTML below the WebDataRocks container**

```html
<div class="chart-panels">
    <div class="chart-panel">
        <div class="chart-panel-header">
            <span class="chart-panel-title">Chart 1</span>
            <select id="chart-type-1" class="chart-type-sel" onchange="updateChart(1)">
                <option value="column" selected>Column</option>
                <option value="bar">Bar</option>
                <option value="line">Line</option>
                <option value="area">Area</option>
                <option value="spline">Spline</option>
                <option value="pie">Pie</option>
                <option value="scatter">Scatter</option>
                <option value="areaspline">Area Spline</option>
            </select>
        </div>
        <div id="chart-container-1" class="chart-container"></div>
    </div>
    <div class="chart-panel">
        <div class="chart-panel-header">
            <span class="chart-panel-title">Chart 2</span>
            <select id="chart-type-2" class="chart-type-sel" onchange="updateChart(2)">
                <option value="column">Column</option>
                <option value="bar">Bar</option>
                <option value="line">Line</option>
                <option value="area">Area</option>
                <option value="spline">Spline</option>
                <option value="pie" selected>Pie</option>
                <option value="scatter">Scatter</option>
                <option value="areaspline">Area Spline</option>
            </select>
        </div>
        <div id="chart-container-2" class="chart-container"></div>
    </div>
</div>
```

**Step 2: Add CSS for the chart panels**

```css
.chart-panels {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 14px;
    margin-top: 16px;
}
.chart-panel {
    background: var(--bg-secondary);
    border: 1px solid var(--border-color);
    border-radius: 6px;
    overflow: hidden;
}
.chart-panel-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 8px 12px;
    border-bottom: 1px solid var(--border-color);
    background: var(--bg-primary);
}
.chart-panel-title {
    font-size: 11px;
    font-weight: 600;
    color: var(--text-secondary);
    text-transform: uppercase;
    letter-spacing: 0.4px;
}
.chart-type-sel {
    padding: 3px 8px;
    border: 1px solid var(--border-color);
    border-radius: 4px;
    font-size: 11px;
    background: var(--bg-primary);
    color: var(--text-primary);
    cursor: pointer;
}
.chart-container {
    height: 350px;
    padding: 4px;
}
```

**Step 3: Add the `updateCharts()` and `updateChart(n)` JS functions**

```javascript
var CHART_COLORS = ['#2E75B6','#ED7D31','#A9D18E','#FFC000','#70AD47','#5B9BD5','#FF6384','#C9B2D6'];

function updateCharts() {
    updateChart(1);
    updateChart(2);
}

function updateChart(n) {
    var type = document.getElementById('chart-type-' + n).value;
    var containerId = 'chart-container-' + n;

    pivot.highcharts.getData({ type: type }, function(data) {
        data.chart = {
            type: type,
            renderTo: containerId,
            height: 340,
            style: { fontFamily: 'Calibri, Arial, sans-serif' }
        };
        data.colors = CHART_COLORS;
        data.credits = { enabled: false };
        data.exporting = { enabled: true };
        data.legend = { verticalAlign: 'bottom', layout: 'horizontal' };
        data.tooltip = { shared: true };
        data.title = { text: '', style: { fontSize: '13px' } };

        Highcharts.chart(containerId, data);
    }, function(data) {
        Highcharts.chart(containerId, data);
    });
}
```

The `pivot.highcharts.getData()` connector automatically:
- Reads the current pivot aggregation (rows, columns, values)
- Formats it into Highcharts-compatible series data
- Calls the callback with a ready-to-render config object
- We just override `chart`, `colors`, `credits`, etc. for our styling

**Step 4: Verify — load data, drag fields in the pivot. Both chart panels should render and auto-update.**

**Step 5: Commit**

```bash
git add modules/RP01/RP01/dashboard/dashboard.html
git commit -m "feat(RP01): add 2 Highcharts chart panels with auto-update"
```

---

### Task 4: Add the dashboard card to the RP01 index page

**Files:**
- Modify: `modules/RP01/RP01/rp01.html`

**Step 1: Add a new card after the Custom Report Designer card (before `</div>` closing `report-cards`)**

```html
<!-- Operations Dashboard -->
<a class="report-card" href="/module/RP01/dashboard/">
    <div class="card-icon">&#128200;</div>
    <div class="card-title">Operations Dashboard</div>
    <div class="card-desc">
        Interactive dashboard with a pivot table and live charts.
        Choose a data source, drag fields, and see charts update
        instantly. Powered by WebDataRocks and Highcharts.
    </div>
    <div class="card-arrow">Open &rarr;</div>
</a>
```

**Step 2: Verify — go to `/module/RP01/`, confirm new card appears in the grid.**

**Step 3: Commit**

```bash
git add modules/RP01/RP01/rp01.html
git commit -m "feat(RP01): add Operations Dashboard card to reports index"
```

---

### Task 5: Polish — styling, status bar, helper functions

**Files:**
- Modify: `modules/RP01/RP01/dashboard/dashboard.html`

**Step 1: Add remaining CSS & JS that was referenced but not yet written**

- `.page-header`, `.module-badge`, `.breadcrumb`, `.toolbar`, `.toolbar-group`, `.tb-btn` — copy from custom_report.html (lines 23-97)
- `.status-bar` — copy from custom_report.html (lines 100-106)
- Status bar HTML: `<div class="status-bar" id="status-bar">Select a data source and date range, then click Load Data.</div>`

**Step 2: Add helper JS functions**

```javascript
function setStatus(msg, isErr) {
    var el = document.getElementById('status-bar');
    el.textContent = msg;
    el.className = 'status-bar' + (isErr ? ' error' : '');
}

function srcLabel(s) {
    return {
        'mbc-ops':        'MBC Operation',
        'vessel-ops':     'Vessel Operation',
        'vessel-barge':   'Vessel - Barge Lines',
        'lueu-equipment': 'LUEU - Equipment Utilization',
        'mbc-tat':        'MBC TAT Analysis',
    }[s] || s;
}
```

**Step 3: Verify — full end-to-end flow works: select source → set date range → load → drag fields → charts update.**

**Step 4: Commit**

```bash
git add modules/RP01/RP01/dashboard/dashboard.html
git commit -m "feat(RP01): dashboard styling and helpers"
```

---

## File Summary

| File | Action | Purpose |
|---|---|---|
| `modules/RP01/RP01/dashboard/__init__.py` | Create | Empty init |
| `modules/RP01/RP01/dashboard/views.py` | Create | Page route (7 lines) |
| `modules/RP01/RP01/views.py` | Modify | Add 1 import line |
| `modules/RP01/RP01/dashboard/dashboard.html` | Create | Full template (~280 lines) |
| `modules/RP01/RP01/rp01.html` | Modify | Add 1 card (~10 lines) |

**New backend API:** None — reuses existing `/api/module/RP01/pivot/data/<source>`.
