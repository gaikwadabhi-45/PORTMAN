"""
Pure-function unit tests for LUEU01 save-time validation:
- time-range overlap math (overnight aware)
- quantity-over-trip and overlap rejection decisions

These import only pure helpers from modules.LUEU01.model and never touch the
database (mirrors test_sap_builder.py).
"""
from modules.LUEU01 import model


# ── _hhmm_to_minutes ────────────────────────────────────────────────────────

def test_hhmm_to_minutes_parses_valid():
    assert model._hhmm_to_minutes('06:30') == 390
    assert model._hhmm_to_minutes('00:00') == 0
    assert model._hhmm_to_minutes('23:59') == 1439

def test_hhmm_to_minutes_rejects_bad():
    assert model._hhmm_to_minutes('') is None
    assert model._hhmm_to_minutes(None) is None
    assert model._hhmm_to_minutes('7') is None
    assert model._hhmm_to_minutes('25:00') is None
    assert model._hhmm_to_minutes('ab:cd') is None


# ── _intervals_overlap ──────────────────────────────────────────────────────

def test_overlap_true_when_ranges_intersect():
    assert model._intervals_overlap('06:00', '08:00', '07:00', '09:00') is True

def test_overlap_false_when_adjacent():
    # back-to-back, no shared minute
    assert model._intervals_overlap('06:00', '08:00', '08:00', '10:00') is False

def test_overlap_false_when_separate():
    assert model._intervals_overlap('06:00', '07:00', '09:00', '10:00') is False

def test_overlap_overnight_wrap():
    # 23:00-02:00 wraps midnight and must overlap 01:00-03:00
    assert model._intervals_overlap('23:00', '02:00', '01:00', '03:00') is True

def test_overlap_false_when_incomplete():
    assert model._intervals_overlap('06:00', '', '07:00', '09:00') is False
    assert model._intervals_overlap('06:00', '08:00', None, '09:00') is False

def test_overlap_is_symmetric():
    # Argument order must never change the result, including overnight cases.
    assert model._intervals_overlap('23:00', '02:00', '01:00', '03:00') == \
           model._intervals_overlap('01:00', '03:00', '23:00', '02:00')
    assert model._intervals_overlap('01:00', '03:00', '23:00', '02:00') is True

def test_overlap_separate_overnight_still_false():
    # 22:00-23:00 and 00:30-01:00 (next day) do not intersect, either order.
    assert model._intervals_overlap('22:00', '23:00', '00:30', '01:00') is False
    assert model._intervals_overlap('00:30', '01:00', '22:00', '23:00') is False


# ── compute_rejections ──────────────────────────────────────────────────────

def _row(**over):
    base = {
        'source_type': 'VCN', 'source_id': 1, 'source_display': 'VCN1 / SHIP',
        'barge_name': 'BARGE-A / 1', 'equipment_name': 'CRANE-1',
        'entry_date': '2026-06-01', 'from_time': '06:00', 'to_time': '08:00',
        'quantity': 100.0,
    }
    base.update(over)
    return base

def test_quantity_under_remaining_is_kept():
    clean, rej = model.compute_rejections(_row(quantity=50.0),
                                          trip_expected=200.0, trip_handled=100.0,
                                          overlap_candidates=[])
    assert clean['quantity'] == 50.0
    assert rej == []

def test_quantity_over_remaining_is_blanked_and_reported():
    clean, rej = model.compute_rejections(_row(quantity=150.0),
                                          trip_expected=200.0, trip_handled=100.0,
                                          overlap_candidates=[])
    assert clean['quantity'] is None
    assert len(rej) == 1
    assert rej[0]['field'] == 'quantity'
    assert rej[0]['remaining'] == 100.0
    assert rej[0]['attempted'] == 150.0

def test_quantity_not_checked_when_no_expected():
    clean, rej = model.compute_rejections(_row(quantity=9999.0),
                                          trip_expected=0.0, trip_handled=0.0,
                                          overlap_candidates=[])
    assert clean['quantity'] == 9999.0
    assert rej == []

def test_quantity_exactly_remaining_is_kept():
    clean, rej = model.compute_rejections(_row(quantity=100.0),
                                          trip_expected=200.0, trip_handled=100.0,
                                          overlap_candidates=[])
    assert clean['quantity'] == 100.0
    assert rej == []

def test_time_overlap_blanks_both_times_and_reports():
    clean, rej = model.compute_rejections(
        _row(from_time='07:00', to_time='09:00'),
        trip_expected=0.0, trip_handled=0.0,
        overlap_candidates=[('06:00', '08:00')])
    assert clean['from_time'] is None
    assert clean['to_time'] is None
    assert len(rej) == 1
    assert rej[0]['field'] == 'time'
    assert rej[0]['conflict'] == {'from_time': '06:00', 'to_time': '08:00'}

def test_no_time_overlap_keeps_times():
    clean, rej = model.compute_rejections(
        _row(from_time='09:00', to_time='10:00'),
        trip_expected=0.0, trip_handled=0.0,
        overlap_candidates=[('06:00', '08:00')])
    assert clean['from_time'] == '09:00'
    assert clean['to_time'] == '10:00'
    assert rej == []

def test_both_rejections_can_fire_together():
    clean, rej = model.compute_rejections(
        _row(quantity=150.0, from_time='07:00', to_time='09:00'),
        trip_expected=200.0, trip_handled=100.0,
        overlap_candidates=[('06:00', '08:00')])
    fields = {r['field'] for r in rej}
    assert fields == {'quantity', 'time'}
    assert clean['quantity'] is None
    assert clean['from_time'] is None
    assert clean['to_time'] is None
