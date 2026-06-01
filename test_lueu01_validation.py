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
