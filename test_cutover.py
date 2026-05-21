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
