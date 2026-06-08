"""Pure helpers for the daily-ops FY cutoff snapshot. No DB, no Flask."""


def fy_label(start_year):
    """Financial-year label for an April-start FY, e.g. 2012 -> '2012-2013'."""
    start_year = int(start_year)
    return f"{start_year}-{start_year + 1}"


def build_fy_throughput(rows):
    """Nest aggregated rows into {fy_label: {cargo_type: float_qty}}.

    rows: iterable of mappings with keys 'fy_start' (int April-start year),
    'cargo_type' (str or None) and 'qty' (number-ish). Zero/None quantities
    are skipped; a missing cargo_type becomes 'OTHERS'.
    """
    out = {}
    for r in rows:
        qty = float(r.get('qty') or 0)
        if qty == 0:
            continue
        cargo_type = r.get('cargo_type') or 'OTHERS'
        label = fy_label(r['fy_start'])
        bucket = out.setdefault(label, {})
        bucket[cargo_type] = bucket.get(cargo_type, 0.0) + qty
    return out
