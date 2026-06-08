"""Pure-function unit tests for daily-ops FY cutoff helpers (no DB)."""
from modules.RP01.RP01.daily_ops import model


# ── fy_label ─────────────────────────────────────────────────────────────────
def test_fy_label_basic():
    assert model.fy_label(2012) == '2012-2013'

def test_fy_label_cutoff_year():
    assert model.fy_label(2026) == '2026-2027'


# ── build_fy_throughput ──────────────────────────────────────────────────────
def test_build_fy_throughput_nests_by_fy_and_cargo_type():
    rows = [
        {'fy_start': 2012, 'cargo_type': 'IBRM',   'qty': 100},
        {'fy_start': 2012, 'cargo_type': 'Fluxes', 'qty': 50},
        {'fy_start': 2026, 'cargo_type': 'IBRM',   'qty': 5},
    ]
    out = model.build_fy_throughput(rows)
    assert out == {
        '2012-2013': {'IBRM': 100.0, 'Fluxes': 50.0},
        '2026-2027': {'IBRM': 5.0},
    }

def test_build_fy_throughput_coerces_to_float():
    out = model.build_fy_throughput([{'fy_start': 2020, 'cargo_type': 'CBRM', 'qty': '12'}])
    assert out == {'2020-2021': {'CBRM': 12.0}}

def test_build_fy_throughput_skips_zero_and_none():
    rows = [
        {'fy_start': 2020, 'cargo_type': 'CBRM', 'qty': 0},
        {'fy_start': 2020, 'cargo_type': 'IBRM', 'qty': None},
        {'fy_start': 2020, 'cargo_type': 'Clinker', 'qty': 7},
    ]
    assert model.build_fy_throughput(rows) == {'2020-2021': {'Clinker': 7.0}}

def test_build_fy_throughput_null_cargo_type_becomes_others():
    out = model.build_fy_throughput([{'fy_start': 2019, 'cargo_type': None, 'qty': 3}])
    assert out == {'2019-2020': {'OTHERS': 3.0}}

def test_build_fy_throughput_empty_rows():
    assert model.build_fy_throughput([]) == {}
