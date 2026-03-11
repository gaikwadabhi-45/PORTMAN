# Home Dashboard (GridStack.js) — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Turn the static home page into a per-user configurable dashboard with draggable/resizable chart cards (powered by GridStack.js), multi-page tabs, and a "Pin to Home" flow from the RP01 Operations Dashboard.

**Architecture:** GridStack.js manages the drag/resize grid on `home.html`. Each card stores its full chart config (data source, filters, chart type, palette, pivot fields) in a PostgreSQL table keyed by `user_id`. Cards are self-contained — on page load they fetch their own data and render a Highcharts chart independently. Multiple dashboard pages are supported as tabs, each with its own GridStack grid.

**Tech Stack:** GridStack.js 10.x (CDN), Highcharts 11.x (already loaded in RP01), existing `/api/module/RP01/pivot/data/<source>` endpoint (no new backend data queries needed).

---

## System Overview

```
┌─────────────────────────────────────────────────────────────────┐
│  home.html                                                       │
│ ┌──────────────────────────────────────────────────────────────┐ │
│ │  Tab Bar:  [Overview]  [Operations]  [+ New Page]            │ │
│ ├──────────────────────────────────────────────────────────────┤ │
│ │                                                              │ │
│ │  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐       │ │
│ │  │  KPI Card    │  │  Bar Chart   │  │  Pie Chart   │       │ │
│ │  │  "Total Qty" │  │  MBC by Cargo│  │  Equipment   │       │ │
│ │  │  12,450 MT   │  │  ▓▓▓▒▒       │  │  ◕ 42% ...   │       │ │
│ │  │  [⚙] [✕]    │  │  [⚙] [✕]    │  │  [⚙] [✕]    │       │ │
│ │  └──────────────┘  └──────────────┘  └──────────────┘       │ │
│ │  ┌─────────────────────────────┐  ┌──────────────┐          │ │
│ │  │  Line Chart (wide)          │  │  KPI Card    │          │ │
│ │  │  TAT Trend — last 30 days   │  │  "Max TAT"   │          │ │
│ │  │  📈 ────────                │  │  847 mins    │          │ │
│ │  │  [⚙] [✕]                   │  │  [⚙] [✕]    │          │ │
│ │  └─────────────────────────────┘  └──────────────┘          │ │
│ │                                                    [+ Card] │ │
│ └──────────────────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────────┘
```

---

## Database Schema

### Table: `dashboard_pages`

| Column | Type | Notes |
|---|---|---|
| id | SERIAL PK | |
| user_id | INTEGER NOT NULL | FK → users.id |
| title | VARCHAR(100) | Tab label, e.g. "Overview" |
| sort_order | INTEGER DEFAULT 0 | Tab ordering |
| created_at | TIMESTAMP DEFAULT NOW() | |

- Each user can have 0–N pages.
- If a user has 0 pages, the home page shows the original static content (backward compatible).
- Unique constraint on `(user_id, title)`.

### Table: `dashboard_cards`

| Column | Type | Notes |
|---|---|---|
| id | SERIAL PK | |
| page_id | INTEGER NOT NULL | FK → dashboard_pages.id ON DELETE CASCADE |
| card_type | VARCHAR(20) | `'chart'` or `'kpi'` |
| title | VARCHAR(150) | Card header label |
| config | JSONB NOT NULL | Full card configuration (see below) |
| gs_x | INTEGER DEFAULT 0 | GridStack column position |
| gs_y | INTEGER DEFAULT 0 | GridStack row position |
| gs_w | INTEGER DEFAULT 4 | GridStack width (out of 12) |
| gs_h | INTEGER DEFAULT 3 | GridStack height |
| created_at | TIMESTAMP DEFAULT NOW() | |

### `config` JSONB structure

```json
{
  "data_source": "mbc-ops",
  "date_col": "doc_date",
  "from_date": "2026-03-01T00:00",
  "to_date": "2026-03-10T23:59",
  "date_mode": "relative",         // "fixed" | "relative"
  "relative_range": "mtd",         // "wtd" | "mtd" | "ytd" | "last7" | "last30"

  // Chart cards:
  "chart_type": "column",          // column | bar | line | pie | area | spline | scatter
  "palette": "default",            // default | ocean | sunset | forest | berry | monochrome
  "data_labels": true,
  "pivot_rows": ["cargo_name"],    // field(s) for aggregation
  "pivot_measure": "quantity",     // field to aggregate (null = count)
  "agg_type": "sum",              // sum | average | count | min | max
  "drilldown_field": "barge_name", // optional 2nd-level drill field

  // KPI cards:
  "kpi_field": "quantity",
  "kpi_agg": "sum"                 // sum | average | count | min | max
}
```

#### `date_mode` — solving "static" card data

- **`"fixed"`**: Uses literal `from_date` / `to_date` values. Card shows data for that exact range (snapshot).
- **`"relative"`**: Ignores `from_date`/`to_date`. Instead, `relative_range` computes the range at render time:
  - `"mtd"` → 1st of current month → today
  - `"ytd"` → Jan 1 → today
  - `"last7"` → today - 7 days → today
  - `"last30"` → today - 30 days → today
  - `"wtd"` → Monday of current week → today

This means **cards auto-refresh with live data** even though the config is "static". Users set it once, and it always shows current-period data.

---

## Backend API Endpoints

All endpoints live in a **new top-level blueprint** (not inside RP01) since the home dashboard is global:

**File:** `modules/home_dashboard/views.py` (new blueprint registered in `app.py`)

### Pages CRUD

| Method | Route | Body / Params | Notes |
|---|---|---|---|
| GET | `/api/dashboard/pages` | — | Returns all pages for `session['user_id']`, ordered by `sort_order` |
| POST | `/api/dashboard/pages` | `{ title }` | Creates a new page, returns `{ id, title }` |
| PUT | `/api/dashboard/pages/<id>` | `{ title, sort_order }` | Rename or reorder a tab |
| DELETE | `/api/dashboard/pages/<id>` | — | Deletes page + all its cards (CASCADE) |

### Cards CRUD

| Method | Route | Body / Params | Notes |
|---|---|---|---|
| GET | `/api/dashboard/pages/<page_id>/cards` | — | Returns all cards for a page with grid positions |
| POST | `/api/dashboard/pages/<page_id>/cards` | `{ card_type, title, config, gs_x, gs_y, gs_w, gs_h }` | Add a card |
| PUT | `/api/dashboard/cards/<id>` | `{ title?, config?, gs_x?, gs_y?, gs_w?, gs_h? }` | Update card config or position |
| DELETE | `/api/dashboard/cards/<id>` | — | Remove a card |
| PUT | `/api/dashboard/pages/<page_id>/layout` | `{ cards: [{ id, gs_x, gs_y, gs_w, gs_h }] }` | Batch update all card positions after drag/resize (single call instead of N updates) |

### Data endpoint

No new data endpoint needed — each card calls the existing `/api/module/RP01/pivot/data/<source>?from_date=...&to_date=...&date_col=...` endpoint with its own stored config.

---

## Frontend Architecture

### home.html Changes

```
home.html (updated)
├── Tab bar (top)
│   ├── Tab buttons (one per dashboard_page)
│   ├── [+ New Page] button
│   └── [Edit ✏️] / [Done ✓] toggle (enters edit mode)
├── GridStack container (one per tab, only active tab visible)
│   ├── Card widgets (gs-item divs)
│   │   ├── Card header: title + [⚙ config] + [✕ delete] (edit mode only)
│   │   ├── Card body: Highcharts chart div OR KPI value display
│   │   └── Card footer: filter summary text (e.g. "MBC Ops · MTD · Sum of quantity")
│   └── [+ Add Card] floating button (edit mode only)
└── Original home content (shown when user has 0 dashboard pages)
```

### Card Rendering Flow

```
Page load
  → GET /api/dashboard/pages
  → Render tab bar
  → For active tab: GET /api/dashboard/pages/<id>/cards
  → For each card:
      1. Compute date range (if relative mode, calculate from today)
      2. fetch('/api/module/RP01/pivot/data/<source>?...')
      3. Aggregate raw data by pivot_rows + pivot_measure + agg_type
      4. Render Highcharts chart (or KPI value) into the gs-item div
      5. Cards load in parallel (Promise.all or individual fetches)
```

### Edit Mode vs View Mode

- **View mode** (default): Cards are locked (no drag/resize), no config/delete buttons visible. Clean dashboard view.
- **Edit mode** (toggle button): GridStack unlocked, cards show ⚙️ and ✕ buttons, [+ Add Card] button visible, tabs show rename/delete options.

### Card Config Modal

When clicking ⚙️ on a card (or [+ Add Card]), a modal opens:

```
┌─────────────────────────────────────────┐
│  Configure Card                          │
├─────────────────────────────────────────┤
│  Card Title: [___________________]       │
│  Card Type:  (●) Chart  (○) KPI         │
│                                          │
│  ── Data Source ──                        │
│  Source:      [MBC Operation     ▼]      │
│  Date Column: [Doc Date          ▼]      │
│  Date Mode:   (●) Relative  (○) Fixed   │
│  Range:       [Month to Date    ▼]       │
│                                          │
│  ── Chart Options ── (if chart type)     │
│  Chart Type:  [Column ▼]                 │
│  Group By:    [cargo_name ▼]             │
│  Measure:     [quantity   ▼]             │
│  Aggregation: [Sum ▼]                    │
│  Drilldown:   [barge_name ▼] (optional)  │
│  Palette:     [Default ▼]               │
│  ☑ Data Labels                           │
│                                          │
│  ── KPI Options ── (if kpi type)         │
│  Field:       [quantity   ▼]             │
│  Aggregation: [Sum ▼]                    │
│                                          │
│  [Preview]          [Cancel] [Save]      │
└─────────────────────────────────────────┘
```

- **Group By** dropdown is populated dynamically after data source is selected (shows all columns from a sample API call or from a static column list per source).
- **Preview** button fetches data + renders a mini chart inside the modal before saving.

---

## "Pin to Home" — from RP01 Dashboard

On the RP01 Operations Dashboard page (`dashboard.html`), add a **📌 Pin** button in each chart panel header:

```
Chart 1  [Column ▼] [Default ▼] [☑ Labels] [📌 Pin]
```

**Flow:**
1. User clicks 📌 Pin
2. Small modal appears: "Pin this chart to Home Dashboard"
   - Select target page (dropdown of user's dashboard pages, or "Create new page")
   - Card title (pre-filled with chart info like "MBC Ops — Column by cargo_name")
   - Confirm button
3. On confirm: POST to `/api/dashboard/pages/<id>/cards` with the current chart's full config auto-captured:
   - `data_source` from toolbar
   - `date_col`, `from_date`, `to_date` from toolbar
   - `chart_type`, `palette`, `data_labels` from the chart panel
   - `pivot_rows` + `pivot_measure` + `agg_type` from WebDataRocks `getReport().slice`
   - `date_mode: "fixed"` by default (user can change to relative later in home config)
4. Toast notification: "Pinned to [page name] ✓"

---

## CDN / Libraries

```html
<!-- GridStack.js (grid layout engine) -->
<link href="https://cdn.jsdelivr.net/npm/gridstack@10.3.1/dist/gridstack.min.css" rel="stylesheet">
<link href="https://cdn.jsdelivr.net/npm/gridstack@10.3.1/dist/gridstack-extra.min.css" rel="stylesheet">
<script src="https://cdn.jsdelivr.net/npm/gridstack@10.3.1/dist/gridstack-all.js"></script>

<!-- Highcharts (for chart cards) -->
<script src="https://cdnjs.cloudflare.com/ajax/libs/highcharts/11.4.1/highcharts.min.js"></script>
<script src="https://cdnjs.cloudflare.com/ajax/libs/highcharts/11.4.1/modules/drilldown.min.js"></script>
<script src="https://cdnjs.cloudflare.com/ajax/libs/highcharts/11.4.1/modules/exporting.min.js"></script>
```

These are loaded **only on home.html** when the user has dashboard pages. If no pages exist, the original static home content renders without loading these libraries.

---

## File Changes Summary

| File | Action | Description |
|---|---|---|
| `modules/home_dashboard/__init__.py` | **Create** | New blueprint for dashboard API |
| `modules/home_dashboard/views.py` | **Create** | Pages + Cards CRUD endpoints |
| `app.py` | **Modify** | Register `home_dashboard` blueprint, update `home()` route to pass page data |
| `templates/home.html` | **Modify** | Add tab bar, GridStack grid, card rendering JS, config modal, edit mode toggle |
| `modules/RP01/RP01/dashboard/dashboard.html` | **Modify** | Add 📌 Pin button to chart panel headers + pin modal |
| `database.py` | **Modify** (maybe) | Add `_ensure_dashboard_tables()` or put in blueprint init |

**No changes to:** existing data API endpoints, base.html, other modules.

---

## Task Breakdown

### Task 1: Database tables + Blueprint skeleton
- Create `modules/home_dashboard/__init__.py` + `views.py`
- `_ensure_tables()` → CREATE TABLE `dashboard_pages`, `dashboard_cards`
- Register blueprint in `app.py`
- Wire up empty routes

### Task 2: Pages CRUD API
- GET / POST / PUT / DELETE for dashboard pages
- All scoped to `session['user_id']`

### Task 3: Cards CRUD API
- GET / POST / PUT / DELETE for cards
- Batch layout update endpoint
- Ownership check (card's page must belong to current user)

### Task 4: home.html — Tab bar + GridStack skeleton
- Conditional rendering: if user has pages → show dashboard; else → show original home
- Tab bar with page buttons + [+ New Page]
- Edit/Done toggle
- GridStack container initialization
- Load cards for active tab

### Task 5: home.html — Card rendering (Chart + KPI)
- `renderCard(card)` function: fetches data, aggregates, renders Highcharts or KPI
- Relative date range computation
- Parallel card loading
- Responsive GridStack resize handling (re-render chart on resize)

### Task 6: home.html — Card config modal
- Modal HTML + JS for add/edit card
- Dynamic field population based on data source
- Preview button
- Save → POST/PUT API → re-render card

### Task 7: home.html — Edit mode (drag/resize/delete)
- GridStack enable/disable on edit toggle
- Delete card button → DELETE API
- On drag/resize stop → batch layout PUT
- Tab rename/delete in edit mode

### Task 8: RP01 Dashboard — Pin to Home button
- Add 📌 Pin button to chart panel headers
- Pin modal: select target page + title
- Auto-capture current chart config from pivot + toolbar
- POST to cards API

### Task 9: Polish + edge cases
- Empty state for pages with no cards ("Add your first card")
- Loading spinners per card while data fetches
- Error handling (card shows error state if data fetch fails)
- Tab reordering (drag tabs or sort_order arrows)

---

## Key Design Decisions

1. **Cards fetch their own data** — no server-side pre-aggregation. Each card hits the existing pivot API independently. This means zero new SQL and full reuse of existing infrastructure. Tradeoff: N cards = N API calls on page load, but these are fast queries and load in parallel.

2. **Relative date mode** solves the "static data" problem. A card configured as "MTD" always shows fresh data without the user touching it. Fixed mode is available for snapshot/comparison cards.

3. **Client-side aggregation** — same `aggregateValues()` pattern already used in RP01 dashboard. The card JS groups raw rows by `pivot_rows`, applies `agg_type` to `pivot_measure`, and feeds the result to Highcharts.

4. **GridStack 12-column grid** — standard responsive grid. Chart cards default to 4×3 (1/3 width), KPI cards to 2×2 (compact). Users can resize freely.

5. **Backward compatible** — if a user has no dashboard pages, `home.html` shows the original static content exactly as before. No migration needed.

6. **Blueprint lives outside RP01** — the home dashboard is a global feature, not specific to RP01. It lives at `/api/dashboard/...` as a top-level blueprint. This is cleaner architecturally and allows future modules to also pin cards.
