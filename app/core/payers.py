"""Payer and practice management module with database backend."""
import re
import difflib
from typing import Dict, List, Optional, Tuple

from ..database import database

# ============ Payor Functions ============

def get_all_payors() -> List[Dict[str, any]]:
    """Get all payers from database."""
    return database.get_all_payers()

def save_payer(name: str, aliases: List[str]) -> Dict[str, any]:
    """Save or update a payer."""
    existing = database.get_payor_by_name(name)
    if existing:
        return database.update_payor(name, aliases)
    return database.create_payor(name, aliases)

def delete_payer(name: str) -> bool:
    """Delete a payer."""
    return database.delete_payor(name)

def _normalize_name(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def _compact_name(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", text.lower())


def _match_known_entity(text: str, records: List[Dict[str, any]], min_ratio: float = 0.80) -> Tuple[bool, Optional[str], float, str]:
    """Match text against configured canonical names and aliases.

    Exact matches and known names/aliases embedded in a longer file line
    return the canonical list name as matched_text. Fuzzy matches return the
    actual file fragment. This prevents header noise from replacing a known
    entity name.
    """
    if not text or not text.strip():
        return False, None, 0.0, ""

    text_norm = _normalize_name(text)
    text_compact = _compact_name(text)
    best = (False, None, 0.0, "")

    for record in records:
        canonical = str(record.get("name", "")).strip()
        if not canonical:
            continue

        names = [canonical] + [
            str(x).strip() for x in record.get("aliases", []) if str(x).strip()
        ]

        for known in names:
            known_norm = _normalize_name(known)
            known_compact = _compact_name(known)
            if not known_compact:
                continue

            # Exact whole-line match: always return the canonical list value.
            if known_norm == text_norm or known_compact == text_compact:
                return True, canonical, 1.0, canonical

            # Known name/alias occurs inside a longer extracted line.
            # IMPORTANT: return canonical, not the complete PDF line and not
            # the noisy matched fragment.
            if known_compact in text_compact:
                ratio = len(known_compact) / max(len(text_compact), 1)
                score = 0.80 + min(ratio, 1.0) * 0.20
                if score > best[2]:
                    best = (True, canonical, score, canonical)
                continue

            # Fuzzy comparison of the complete line.
            ratio = difflib.SequenceMatcher(
                None, known_compact, text_compact
            ).ratio()
            if ratio >= min_ratio and ratio > best[2]:
                best = (True, canonical, ratio, text.strip())

            # Fuzzy comparison against individual text fragments.
            words = text.split()
            if len(words) > 1:
                for size in range(1, min(len(words), 10) + 1):
                    for i in range(0, len(words) - size + 1):
                        fragment = " ".join(words[i:i + size])
                        fragment_compact = _compact_name(fragment)
                        if not fragment_compact:
                            continue
                        ratio = difflib.SequenceMatcher(
                            None, known_compact, fragment_compact
                        ).ratio()
                        if ratio >= min_ratio and ratio > best[2]:
                            best = (True, canonical, ratio, fragment.strip())

    return best

def match_known_payer(text: str, min_ratio: float = 0.80) -> Tuple[bool, Optional[str], float]:
    matched, canonical, score, _ = _match_known_entity(text, database.get_all_payers(), min_ratio)
    return matched, canonical, score

def get_payer_aliases() -> Dict[str, List[str]]:
    """Get all payer aliases as {canonical: [aliases]}."""
    return database.get_payor_aliases()

def bulk_import_payers(payers: List[Dict[str, any]]) -> Dict[str, any]:
    """Bulk import payers."""
    return database.bulk_import_payors(payers)

# ============ Practice Functions ============

def get_all_practices() -> List[Dict[str, any]]:
    """Get all practices from database."""
    return database.get_all_practices()

def save_practice(name: str, aliases: List[str]) -> Dict[str, any]:
    """Save or update a practice."""
    existing = database.get_practice_by_name(name)
    if existing:
        return database.update_practice(name, aliases)
    return database.create_practice(name, aliases)

def delete_practice(name: str) -> bool:
    """Delete a practice."""
    return database.delete_practice(name)

def match_known_practice(text: str, min_ratio: float = 0.80) -> Tuple[bool, Optional[str], float]:
    matched, canonical, score, _ = _match_known_entity(text, database.get_all_practices(), min_ratio)
    return matched, canonical, score


def match_known_payer_text(text: str, min_ratio: float = 0.80) -> Tuple[bool, Optional[str], float, str]:
    return _match_known_entity(text, database.get_all_payers(), min_ratio)


def match_known_practice_text(text: str, min_ratio: float = 0.80) -> Tuple[bool, Optional[str], float, str]:
    return _match_known_entity(text, database.get_all_practices(), min_ratio)

def get_practice_aliases() -> Dict[str, List[str]]:
    """Get all practice aliases as {canonical: [aliases]}."""
    return database.get_practice_aliases()

def bulk_import_practices(practices: List[Dict[str, any]]) -> Dict[str, any]:
    """Bulk import practices."""
    return database.bulk_import_practices(practices)