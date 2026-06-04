"""Pure-function unit tests for RP01 historical data parsing/validation.
No DB access (mirrors test_lueu01_validation.py)."""
from modules.RP01.RP01.historical_data import model


# ── parse_date ──────────────────────────────────────────────────────────────
def test_parse_date_iso():
    assert model.parse_date('2025-04-15') == '2025-04-15'

def test_parse_date_datetime_text():
    assert model.parse_date('2025-04-15 00:00:00') == '2025-04-15'

def test_parse_date_blank_is_none():
    assert model.parse_date('') is None
    assert model.parse_date(None) is None

def test_parse_date_bad_raises():
    import pytest
    with pytest.raises(ValueError):
        model.parse_date('15/04/2025')


# ── parse_hhmm ──────────────────────────────────────────────────────────────
def test_parse_hhmm_ok():
    assert model.parse_hhmm('06:30') == '06:30'
    assert model.parse_hhmm('06:30:00') == '06:30'

def test_parse_hhmm_blank_is_none():
    assert model.parse_hhmm('') is None
    assert model.parse_hhmm(None) is None

def test_parse_hhmm_bad_raises():
    import pytest
    with pytest.raises(ValueError):
        model.parse_hhmm('25:99')


# ── parse_number ────────────────────────────────────────────────────────────
def test_parse_number_ok():
    assert model.parse_number('700') == 700.0
    assert model.parse_number(700) == 700.0
    assert model.parse_number('4.5') == 4.5

def test_parse_number_blank_is_none():
    assert model.parse_number('') is None
    assert model.parse_number(None) is None

def test_parse_number_bad_raises():
    import pytest
    with pytest.raises(ValueError):
        model.parse_number('abc')


# ── suggest_matches ───────────────────────────────────────────────────────────
def test_suggest_matches_finds_close():
    masters = ['BARGE UNLOADER 1', 'BARGE UNLOADER 2', 'BU 1 & BU 2']
    out = model.suggest_matches('BARGE UNLOADER1', masters)
    assert 'BARGE UNLOADER 1' in out

def test_suggest_matches_case_insensitive():
    out = model.suggest_matches('limestone', ['Limestone', 'Dolomite'])
    assert 'Limestone' in out

def test_suggest_matches_empty_when_nothing_close():
    out = model.suggest_matches('zzzzzz', ['Limestone', 'Dolomite'])
    assert out == []


# ── parse_rows ────────────────────────────────────────────────────────────────
def test_parse_rows_maps_headers_and_skips_blank():
    headers = ['entry_date', 'equipment_name', 'quantity']
    raw = [
        ['2025-04-01', 'BU 1', '700'],
        ['', '', ''],                       # fully blank → skipped
        ['2025-04-02', 'BU 2', ''],
    ]
    rows, errors = model.parse_rows(headers, raw)
    assert errors == []
    assert len(rows) == 2
    assert rows[0]['entry_date'] == '2025-04-01'
    assert rows[0]['equipment_name'] == 'BU 1'
    assert rows[0]['quantity'] == 700.0
    assert rows[1]['quantity'] is None

def test_parse_rows_collects_format_errors():
    headers = ['entry_date', 'equipment_name', 'quantity']
    raw = [['bad-date', 'BU 1', 'oops']]
    rows, errors = model.parse_rows(headers, raw)
    assert rows == []
    assert any('entry_date' in e['message'] for e in errors)

def test_parse_rows_requires_equipment_and_date():
    headers = ['entry_date', 'equipment_name', 'quantity']
    raw = [['2025-04-01', '', '5']]
    rows, errors = model.parse_rows(headers, raw)
    assert rows == []
    assert any('equipment_name' in e['message'] for e in errors)


# ── apply_resolutions ─────────────────────────────────────────────────────────
def test_apply_resolutions_replaces_matching_values():
    rows = [
        {'equipment_name': 'BUL 01', 'barge_name': 'Radha 02'},
        {'equipment_name': 'BUL 01', 'barge_name': 'Falcon'},
    ]
    res = {
        'equipment_name': {'BUL 01': {'action': 'replace', 'target': 'BUL-01'}},
        'barge_name': {'Radha 02': {'action': 'replace', 'target': 'RADHA KRISHNA 2'}},
    }
    out = model.apply_resolutions(rows, res)
    assert out[0]['equipment_name'] == 'BUL-01'
    assert out[0]['barge_name'] == 'RADHA KRISHNA 2'
    assert out[1]['equipment_name'] == 'BUL-01'
    assert out[1]['barge_name'] == 'Falcon'  # no resolution → unchanged

def test_apply_resolutions_ignores_add_and_keep():
    rows = [{'cargo_name': 'MLV Coal'}, {'delay_name': 'PL Cleaning'}]
    res = {
        'cargo_name': {'MLV Coal': {'action': 'add'}},
        'delay_name': {'PL Cleaning': {'action': 'keep'}},
    }
    out = model.apply_resolutions(rows, res)
    assert out[0]['cargo_name'] == 'MLV Coal'   # add → value kept as-is
    assert out[1]['delay_name'] == 'PL Cleaning'

def test_apply_resolutions_empty_returns_copy():
    rows = [{'a': 1}]
    out = model.apply_resolutions(rows, {})
    assert out == rows and out is not rows

def test_apply_resolutions_replace_without_target_is_noop():
    rows = [{'cargo_name': 'X'}]
    out = model.apply_resolutions(rows, {'cargo_name': {'X': {'action': 'replace', 'target': ''}}})
    assert out[0]['cargo_name'] == 'X'
