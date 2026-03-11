from flask import render_template, request, session, redirect, url_for, Response, jsonify
from functools import wraps
from datetime import datetime, timedelta
import io, json

from .. import bp
from database import get_db, get_cursor

from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter

# ── Style constants ──────────────────────────────────────────────────────────
_thin = Side(style='thin', color='000000')
_bdr  = Border(left=_thin, right=_thin, top=_thin, bottom=_thin)
_ctr  = Alignment(horizontal='center', vertical='center', wrap_text=True)
_left = Alignment(horizontal='left', vertical='center', wrap_text=True)


def _fill(hex_color):
    return PatternFill('solid', fgColor=hex_color)


def _font(bold=False, size=11):
    return Font(name='Calibri', bold=bold, size=size)


def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated


# ── Page ─────────────────────────────────────────────────────────────────────

@bp.route('/module/RP01/shift-report/')
@login_required
def shift_report_index():
    return render_template('shift_report/shift_report.html',
                           username=session.get('username'))


# ── Preview API ──────────────────────────────────────────────────────────────

@bp.route('/api/module/RP01/shift-report/preview')
@login_required
def shift_report_preview():
    entry_date = request.args.get('entry_date', '')
    shift = request.args.get('shift', '')
    if not entry_date or not shift:
        return jsonify({'error': 'entry_date and shift are required'}), 400

    cargo_pivot = _fetch_cargo_pivot(entry_date, shift)
    delays = _fetch_delays(entry_date, shift)
    return jsonify({'cargo_pivot': cargo_pivot, 'delays': delays})


# ── Download API ─────────────────────────────────────────────────────────────

@bp.route('/api/module/RP01/shift-report/download')
@login_required
def shift_report_download():
    entry_date = request.args.get('entry_date', '')
    shift = request.args.get('shift', '')
    if not entry_date or not shift:
        return Response('entry_date and shift are required', status=400)

    cargo_pivot = _fetch_cargo_pivot(entry_date, shift)
    delays = _fetch_delays(entry_date, shift)
    buf = _build_excel(entry_date, shift, cargo_pivot, delays)
    fname = f'ShiftReport_{entry_date}_Shift{shift}.xlsx'
    return Response(
        buf.getvalue(),
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        headers={'Content-Disposition': f'attachment; filename="{fname}"'},
    )


# ── Data fetchers ────────────────────────────────────────────────────────────

def _fetch_cargo_pivot(entry_date, shift):
    """Return cargo pivot data:
    { cargo_name: { equipment_name: { route_name: qty, ... }, ... }, ... }
    Also returns the list of unique equipment names and route names found.
    """
    conn = get_db()
    cur = get_cursor(conn)
    cur.execute("""
        SELECT cargo_name, equipment_name, route_name,
               COALESCE(SUM(quantity), 0) AS qty
        FROM lueu_lines
        WHERE entry_date = %s
          AND shift = %s
          AND quantity > 0
          AND cargo_name IS NOT NULL AND cargo_name != ''
        GROUP BY cargo_name, equipment_name, route_name
        ORDER BY cargo_name, equipment_name, route_name
    """, (entry_date, shift))
    rows = cur.fetchall()
    conn.close()

    # Collect unique equipment and route names
    equipment_set = set()
    route_set = set()
    pivot = {}

    for r in rows:
        cargo = r['cargo_name'] or 'Unknown'
        equip = r['equipment_name'] or 'Unknown'
        route = r['route_name'] or 'Unknown'
        qty = float(r['qty'])

        equipment_set.add(equip)
        route_set.add(route)

        if cargo not in pivot:
            pivot[cargo] = {}
        if equip not in pivot[cargo]:
            pivot[cargo][equip] = {}
        pivot[cargo][equip][route] = pivot[cargo][equip].get(route, 0) + qty

    equipments = sorted(equipment_set)
    routes = sorted(route_set)

    return {
        'data': pivot,
        'equipments': equipments,
        'routes': routes,
    }


def _fetch_delays(entry_date, shift):
    """Return delay data grouped by delay_type > delay_name > equipment > system > route.
    Each leaf has from_time, to_time, total_minutes.
    """
    conn = get_db()
    cur = get_cursor(conn)
    cur.execute("""
        SELECT l.delay_name, l.equipment_name, l.system_name, l.route_name,
               l.from_time, l.to_time,
               COALESCE(d.type, 'Other') AS delay_type
        FROM lueu_lines l
        LEFT JOIN port_delay_types d ON d.name = l.delay_name
        WHERE l.entry_date = %s
          AND l.shift = %s
          AND l.delay_name IS NOT NULL AND l.delay_name != ''
        ORDER BY d.type, l.delay_name, l.equipment_name, l.system_name, l.route_name
    """, (entry_date, shift))
    rows = cur.fetchall()
    conn.close()

    delays = []
    for r in rows:
        from_t = r['from_time'] or ''
        to_t = r['to_time'] or ''
        total_min = _calc_minutes(from_t, to_t)
        delays.append({
            'delay_type': r['delay_type'],
            'delay_name': r['delay_name'] or '',
            'equipment_name': r['equipment_name'] or '',
            'system_name': r['system_name'] or '',
            'route_name': r['route_name'] or '',
            'from_time': from_t,
            'to_time': to_t,
            'total_minutes': total_min,
        })

    return delays


def _calc_minutes(from_t, to_t):
    """Calculate difference in minutes between two HH:MM time strings."""
    try:
        fmt = '%H:%M'
        f = datetime.strptime(from_t.strip(), fmt)
        t = datetime.strptime(to_t.strip(), fmt)
        diff = (t - f).total_seconds() / 60
        if diff < 0:
            diff += 24 * 60  # handle overnight
        return round(diff)
    except Exception:
        return 0


def _fmt_minutes(minutes):
    """Format minutes as HH:MM string."""
    if not minutes:
        return ''
    h = int(minutes) // 60
    m = int(minutes) % 60
    return f'{h:02d}:{m:02d}'


# ── Excel builder ────────────────────────────────────────────────────────────

def _build_excel(entry_date, shift, cargo_pivot, delays):
    wb = Workbook()

    _build_cargo_sheet(wb, entry_date, shift, cargo_pivot)
    _build_delay_sheet(wb, entry_date, shift, delays)

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf


def _cell(ws, r, c, val='', bold=False, fill_color='FFFFFF', align=_ctr):
    cell = ws.cell(r, c, val)
    cell.font = _font(bold=bold)
    cell.fill = _fill(fill_color)
    cell.alignment = align
    cell.border = _bdr
    return cell


def _build_cargo_sheet(wb, entry_date, shift, cargo_pivot):
    ws = wb.active
    ws.title = 'Cargo Handled'

    data = cargo_pivot['data']
    equipments = cargo_pivot['equipments']
    routes = cargo_pivot['routes']
    cargo_names = sorted(data.keys())

    if not equipments or not routes:
        _cell(ws, 1, 1, 'No cargo data found for this shift.', align=_left)
        return

    # Pre-compute aggregates for the simpler tables
    # cargo -> equipment -> total across routes
    equip_totals = {}
    # cargo -> route -> total across equipments
    route_totals = {}
    for cargo in cargo_names:
        equip_totals[cargo] = {}
        route_totals[cargo] = {}
        for equip in equipments:
            et = sum(data.get(cargo, {}).get(equip, {}).get(rt, 0) for rt in routes)
            equip_totals[cargo][equip] = et
        for route in routes:
            rt_val = sum(data.get(cargo, {}).get(eq, {}).get(route, 0) for eq in equipments)
            route_totals[cargo][route] = rt_val

    row = 1

    # ── Helper: write a title bar ────────────────────────────────────────
    def _title_bar(start_row, ncols, title):
        ws.merge_cells(start_row=start_row, start_column=1,
                       end_row=start_row, end_column=ncols)
        _cell(ws, start_row, 1, title, bold=True, fill_color='4472C4', align=_ctr)
        ws.cell(start_row, 1).font = Font(name='Calibri', bold=True, size=12, color='FFFFFF')
        for ci in range(2, ncols + 1):
            c = ws.cell(start_row, ci)
            c.fill = _fill('4472C4')
            c.border = _bdr

    def _ival(v):
        return int(round(v)) if v else ''

    # ══════════════════════════════════════════════════════════════════════
    # TABLE 1 — Cargo × Equipment (no route breakdown)
    # ══════════════════════════════════════════════════════════════════════
    t1_cols = 1 + len(equipments) + 1  # cargo + equipments + grand total
    _title_bar(row, t1_cols,
               f'Cargo by Equipment | Date: {entry_date} | Shift: {shift}')
    row += 1

    _cell(ws, row, 1, 'Cargo Name', bold=True, fill_color='D9E2F3', align=_ctr)
    for i, eq in enumerate(equipments):
        _cell(ws, row, 2 + i, eq, bold=True, fill_color='D9E2F3', align=_ctr)
    _cell(ws, row, t1_cols, 'Grand Total', bold=True, fill_color='D9E2F3', align=_ctr)
    row += 1

    col_sums_t1 = {}
    for cargo in cargo_names:
        _cell(ws, row, 1, cargo, align=_left)
        grand = 0
        for i, eq in enumerate(equipments):
            v = equip_totals[cargo].get(eq, 0)
            ci = 2 + i
            _cell(ws, row, ci, _ival(v), align=_ctr)
            col_sums_t1[ci] = col_sums_t1.get(ci, 0) + v
            grand += v
        _cell(ws, row, t1_cols, _ival(grand), bold=True, align=_ctr)
        col_sums_t1[t1_cols] = col_sums_t1.get(t1_cols, 0) + grand
        row += 1

    _cell(ws, row, 1, 'Grand Total', bold=True, fill_color='D9E2F3', align=_left)
    for ci in range(2, t1_cols + 1):
        _cell(ws, row, ci, _ival(col_sums_t1.get(ci, 0)),
              bold=True, fill_color='D9E2F3', align=_ctr)
    row += 2  # blank row gap

    # ══════════════════════════════════════════════════════════════════════
    # TABLE 2 — Cargo × Route (no equipment breakdown)
    # ══════════════════════════════════════════════════════════════════════
    t2_cols = 1 + len(routes) + 1
    _title_bar(row, t2_cols,
               f'Cargo by Route | Date: {entry_date} | Shift: {shift}')
    row += 1

    _cell(ws, row, 1, 'Cargo Name', bold=True, fill_color='D9E2F3', align=_ctr)
    for i, rt in enumerate(routes):
        _cell(ws, row, 2 + i, rt, bold=True, fill_color='D9E2F3', align=_ctr)
    _cell(ws, row, t2_cols, 'Grand Total', bold=True, fill_color='D9E2F3', align=_ctr)
    row += 1

    col_sums_t2 = {}
    for cargo in cargo_names:
        _cell(ws, row, 1, cargo, align=_left)
        grand = 0
        for i, rt in enumerate(routes):
            v = route_totals[cargo].get(rt, 0)
            ci = 2 + i
            _cell(ws, row, ci, _ival(v), align=_ctr)
            col_sums_t2[ci] = col_sums_t2.get(ci, 0) + v
            grand += v
        _cell(ws, row, t2_cols, _ival(grand), bold=True, align=_ctr)
        col_sums_t2[t2_cols] = col_sums_t2.get(t2_cols, 0) + grand
        row += 1

    _cell(ws, row, 1, 'Grand Total', bold=True, fill_color='D9E2F3', align=_left)
    for ci in range(2, t2_cols + 1):
        _cell(ws, row, ci, _ival(col_sums_t2.get(ci, 0)),
              bold=True, fill_color='D9E2F3', align=_ctr)
    row += 2  # blank row gap

    # ══════════════════════════════════════════════════════════════════════
    # TABLE 3 — Full Cargo × Equipment × Route pivot (merged headers)
    # ══════════════════════════════════════════════════════════════════════
    n_routes = len(routes)
    cols_per_equip = n_routes + 1  # routes + equipment total
    t3_cols = 1 + len(equipments) * cols_per_equip + 1  # cargo col + data + grand total

    _title_bar(row, t3_cols,
               f'Cargo by Equipment & Route | Date: {entry_date} | Shift: {shift}')
    row += 1

    # Equipment group header row (merged across route sub-columns)
    _cell(ws, row, 1, 'Cargo Name', bold=True, fill_color='D9E2F3', align=_ctr)
    ws.merge_cells(start_row=row, start_column=1, end_row=row + 1, end_column=1)
    col = 2
    for equip in equipments:
        ws.merge_cells(start_row=row, start_column=col,
                       end_row=row, end_column=col + n_routes)
        _cell(ws, row, col, equip, bold=True, fill_color='D9E2F3', align=_ctr)
        for ci in range(col + 1, col + n_routes + 1):
            c = ws.cell(row, ci)
            c.fill = _fill('D9E2F3')
            c.border = _bdr
        col += cols_per_equip
    ws.merge_cells(start_row=row, start_column=col, end_row=row + 1, end_column=col)
    _cell(ws, row, col, 'Grand Total', bold=True, fill_color='D9E2F3', align=_ctr)
    row += 1

    # Route sub-header row
    col = 2
    for _eq in equipments:
        for rt in routes:
            _cell(ws, row, col, rt, bold=True, fill_color='E2EFDA', align=_ctr)
            col += 1
        _cell(ws, row, col, 'Total', bold=True, fill_color='E2EFDA', align=_ctr)
        col += 1
    row += 1

    # Data rows
    grand_totals_by_col = {}
    for cargo in cargo_names:
        _cell(ws, row, 1, cargo, align=_left)
        col = 2
        cargo_grand = 0
        for equip in equipments:
            equip_total = 0
            for rt in routes:
                qty = data.get(cargo, {}).get(equip, {}).get(rt, 0)
                _cell(ws, row, col, _ival(qty), align=_ctr)
                grand_totals_by_col[col] = grand_totals_by_col.get(col, 0) + qty
                equip_total += qty
                col += 1
            _cell(ws, row, col, _ival(equip_total), bold=True, align=_ctr)
            grand_totals_by_col[col] = grand_totals_by_col.get(col, 0) + equip_total
            cargo_grand += equip_total
            col += 1
        _cell(ws, row, col, _ival(cargo_grand), bold=True, align=_ctr)
        grand_totals_by_col[col] = grand_totals_by_col.get(col, 0) + cargo_grand
        row += 1

    # Grand Total row
    _cell(ws, row, 1, 'Grand Total', bold=True, fill_color='D9E2F3', align=_left)
    col = 2
    for _eq in equipments:
        for _rt in routes:
            _cell(ws, row, col, _ival(grand_totals_by_col.get(col, 0)),
                  bold=True, fill_color='D9E2F3', align=_ctr)
            col += 1
        _cell(ws, row, col, _ival(grand_totals_by_col.get(col, 0)),
              bold=True, fill_color='D9E2F3', align=_ctr)
        col += 1
    _cell(ws, row, col, _ival(grand_totals_by_col.get(col, 0)),
          bold=True, fill_color='D9E2F3', align=_ctr)

    # Column widths
    ws.column_dimensions['A'].width = 25
    max_col = max(t1_cols, t2_cols, t3_cols)
    for ci in range(2, max_col + 1):
        ws.column_dimensions[get_column_letter(ci)].width = 14


def _build_delay_sheet(wb, entry_date, shift, delays):
    ws = wb.create_sheet('Delay Report')

    if not delays:
        _cell(ws, 1, 1, 'No delay data found for this shift.', align=_left)
        return

    # Title row
    headers = ['Delay Type', 'Activity', 'Equipment', 'System', 'Receiving Route',
               'From', 'To', 'Total']
    total_cols = len(headers)

    ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=total_cols)
    _cell(ws, 1, 1, f'Shift Report - Delay Breakdown | Date: {entry_date} | Shift: {shift}',
          bold=True, fill_color='4472C4', align=_ctr)
    ws.cell(1, 1).font = Font(name='Calibri', bold=True, size=12, color='FFFFFF')
    for ci in range(2, total_cols + 1):
        c = ws.cell(1, ci)
        c.fill = _fill('4472C4')
        c.border = _bdr

    # Header row
    for ci, h in enumerate(headers, 1):
        _cell(ws, 2, ci, h, bold=True, fill_color='D9E2F3', align=_ctr)

    # Group delays by type
    grouped = {}
    for d in delays:
        dt = d['delay_type']
        if dt not in grouped:
            grouped[dt] = []
        grouped[dt].append(d)

    row = 3
    type_colors = {
        'RMHS Delays': 'FFF2CC',
        'MaintenanceDelays': 'FCE4D6',
        'ProcessRequirement': 'D6E4F0',
        'ProcessDelays': 'E2EFDA',
    }

    for delay_type in sorted(grouped.keys()):
        items = grouped[delay_type]
        fill_color = type_colors.get(delay_type, 'F2F2F2')
        type_start = row
        type_total_min = 0

        # Group within type by delay_name (activity)
        by_activity = {}
        for d in items:
            dn = d['delay_name']
            if dn not in by_activity:
                by_activity[dn] = []
            by_activity[dn].append(d)

        for activity in sorted(by_activity.keys()):
            act_items = by_activity[activity]
            act_start = row

            for d in act_items:
                _cell(ws, row, 1, '', fill_color=fill_color, align=_left)
                _cell(ws, row, 2, '', fill_color='FFFFFF', align=_left)
                _cell(ws, row, 3, d['equipment_name'], align=_left)
                _cell(ws, row, 4, d['system_name'], align=_left)
                _cell(ws, row, 5, d['route_name'], align=_left)
                _cell(ws, row, 6, d['from_time'], align=_ctr)
                _cell(ws, row, 7, d['to_time'], align=_ctr)
                total_min = d['total_minutes']
                type_total_min += total_min
                _cell(ws, row, 8, _fmt_minutes(total_min), align=_ctr)
                row += 1

            # Merge activity name cells
            if len(act_items) > 1:
                ws.merge_cells(start_row=act_start, start_column=2,
                               end_row=row - 1, end_column=2)
            ws.cell(act_start, 2).value = activity
            ws.cell(act_start, 2).font = _font(bold=False)
            ws.cell(act_start, 2).alignment = _left
            ws.cell(act_start, 2).border = _bdr

        # Merge delay_type cells
        if row > type_start:
            if row - type_start > 1:
                ws.merge_cells(start_row=type_start, start_column=1,
                               end_row=row - 1, end_column=1)
            ws.cell(type_start, 1).value = delay_type
            ws.cell(type_start, 1).font = _font(bold=True)
            ws.cell(type_start, 1).alignment = _left
            ws.cell(type_start, 1).fill = _fill(fill_color)
            ws.cell(type_start, 1).border = _bdr

        # Type subtotal row
        _cell(ws, row, 1, f'{delay_type} Total', bold=True, fill_color=fill_color, align=_left)
        for ci in range(2, 8):
            _cell(ws, row, ci, '', fill_color=fill_color)
        _cell(ws, row, 8, _fmt_minutes(type_total_min), bold=True,
              fill_color=fill_color, align=_ctr)
        row += 1

    # Grand total
    grand_total = sum(d['total_minutes'] for d in delays)
    _cell(ws, row, 1, 'Grand Total', bold=True, fill_color='D9E2F3', align=_left)
    for ci in range(2, 8):
        _cell(ws, row, ci, '', fill_color='D9E2F3')
    _cell(ws, row, 8, _fmt_minutes(grand_total), bold=True,
          fill_color='D9E2F3', align=_ctr)

    # Column widths
    col_widths = {'A': 22, 'B': 28, 'C': 20, 'D': 18, 'E': 20, 'F': 10, 'G': 10, 'H': 10}
    for letter, w in col_widths.items():
        ws.column_dimensions[letter].width = w
