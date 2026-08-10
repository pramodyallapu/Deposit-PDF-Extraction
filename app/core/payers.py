# app/core/payers.py
"""
Facade for payer alias persistence – re‑exports all database functions.
The global KNOWN_PAYERS is kept in sync with the database.
"""
from app.database.database import (
    get_known_payers,
    get_all_payers,
    save_payer,
    delete_payer,
    replace_all_payers,
)

# The global dict used by eob_extraction.py and patterns.py
KNOWN_PAYERS = get_known_payers()

__all__ = [
    'KNOWN_PAYERS',
    'get_all_payers',
    'save_payer',
    'delete_payer',
    'replace_all_payers',
]