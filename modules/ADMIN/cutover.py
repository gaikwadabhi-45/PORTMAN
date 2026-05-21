"""Go-live cutover logic: seed numbers, mark items billed, lock. Pure helpers
have no DB dependency so they are unit-testable; DB functions open their own
connection like the rest of the codebase."""
from database import get_db, get_cursor, get_module_config, save_module_config
import json

# cargo_source_type -> (declaration table, total-quantity column)
CARGO_SOURCES = {
    'VCN_IMPORT': ('vcn_cargo_declaration', 'bl_quantity'),
    'VCN_EXPORT': ('vcn_export_cargo_declaration', 'bl_quantity'),
    'MBC':        ('mbc_customer_details', 'quantity'),
}


def cargo_source(source_type):
    """Map a cargo_source_type to its (table, qty_column), or None if unknown."""
    return CARGO_SOURCES.get(source_type)


def validate_start_seq(start_seq, current_max):
    """A cutover start number must be a positive integer strictly greater than
    the highest number already issued (else it would be silently ignored)."""
    if not isinstance(start_seq, int) or start_seq <= 0:
        return False, 'Start number must be a positive integer.'
    if start_seq <= (current_max or 0):
        return False, (f'Start number must be greater than the highest number '
                       f'already issued ({current_max or 0}).')
    return True, ''
