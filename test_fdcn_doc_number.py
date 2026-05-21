"""
Unit tests for FDCN01 doc-number prefix resolution after the configurable
DN/CN doc series was removed. Prefixes are now fixed in code (the SAP Reference
comes from the original invoice, so doc_number is only the Portbird-side id).
"""
from modules.FDCN01 import model


def test_cn_prefix_is_dpplcn():
    assert model.fdcn_doc_prefix('CN') == 'DPPLCN'


def test_dn_prefix_is_dppldn():
    assert model.fdcn_doc_prefix('DN') == 'DPPLDN'


def test_unknown_type_falls_back_to_doc_type():
    assert model.fdcn_doc_prefix('XX') == 'XX'
