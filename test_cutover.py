"""
Unit tests for the go-live cutover logic. Pure helpers are tested without a DB
(matching the test_sap_builder.py pattern); DB-touching paths are covered by
the lock-gate tests via monkeypatch.
"""
from modules.FIN01 import model


# --- Task 2: next_from_seed (floor) ----------------------------------------

def test_next_from_seed_no_existing_uses_seed():
    assert model.next_from_seed(0, 4568) == 4568


def test_next_from_seed_existing_at_seed_increments():
    assert model.next_from_seed(4568, 4568) == 4569


def test_next_from_seed_existing_above_seed_dominates():
    assert model.next_from_seed(5000, 4568) == 5001


def test_next_from_seed_no_seed_is_plain_increment():
    assert model.next_from_seed(10, None) == 11
    assert model.next_from_seed(0, None) == 1


# --- Task 4: pure cutover helpers ------------------------------------------
from modules.ADMIN import cutover


def test_validate_start_seq_ok_above_max():
    assert cutover.validate_start_seq(4568, 0) == (True, '')


def test_validate_start_seq_rejects_at_or_below_max():
    ok, msg = cutover.validate_start_seq(5, 10)
    assert ok is False and '10' in msg


def test_validate_start_seq_rejects_non_positive():
    assert cutover.validate_start_seq(0, 0)[0] is False
    assert cutover.validate_start_seq(-3, 0)[0] is False


def test_cargo_source_maps_known_types():
    assert cutover.cargo_source('VCN_IMPORT') == ('vcn_cargo_declaration', 'bl_quantity')
    assert cutover.cargo_source('VCN_EXPORT') == ('vcn_export_cargo_declaration', 'bl_quantity')
    assert cutover.cargo_source('MBC') == ('mbc_customer_details', 'quantity')


def test_cargo_source_unknown_is_none():
    assert cutover.cargo_source('NOPE') is None
