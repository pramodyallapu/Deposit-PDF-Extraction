"""Zone-aware extraction with label hierarchy - Core extraction engine."""

from dataclasses import dataclass
from typing import List, Dict, Optional

from .field_extraction import find_field_candidates, deduplicate_candidates
from .scoring import Candidate, normalize_text
from .label_hierarchy import get_aliases_by_level
import re

CHECK_INSTRUMENT_SIGNALS = re.compile(
    r"\bVOID AFTER\b|\bNON[- ]NEGOTIABLE\b|\bDRAFT NO\b|\bDRAFT DATE\b|"
    r"\bPAYABLE THROUGH DRAFT\b|\bPAY(?:ABLE)?\s*TO\s*THE\s*ORDER\s*OF\b|"
    r"\bELECTRONIC PAYMENT CLEARINGHOUSE\b|\bMICR\b|\bACH TRACE\b",
    re.IGNORECASE,
)

def is_check_instrument_page(text: str) -> bool:
    """
    True if this page IS the check/draft/EFT instrument itself, not a
    statement or summary describing it. Requires >=2 independent signals to
    avoid a single stray phrase (e.g. a boilerplate footer mentioning
    "non-negotiable") triggering a false positive.
    """
    if not text:
        return False
    return len(CHECK_INSTRUMENT_SIGNALS.findall(text)) >= 2

@dataclass
class ZoneCandidate:
    candidate: Candidate
    zone: str
    label_level: int
    page_number: int
    zone_confidence_boost: float


def get_zone_for_line(line_number: int, total_lines: int) -> str:
    """
    Classify a line's position on its page as header/body/footer, purely for
    scoring purposes. This replaces divide_page_into_zones(): we no longer
    cut the page text apart before searching it (that was losing context
    across zone boundaries) -- we search the full page and tag results
    afterward based on where they landed.
    """
    if total_lines <= 0:
        return "body"
    header_end = max(1, int(total_lines * 0.15))
    footer_start = max(header_end + 1, int(total_lines * 0.85))
    if line_number < header_end:
        return "header"
    if line_number >= footer_start:
        return "footer"
    return "body"


def get_zone_confidence_boost(zone: str) -> float:
    """Footer summaries (check amount/date/number) are the most reliable."""
    return {"footer": 0.30, "header": 0.05, "body": 0.00}.get(zone, 0.0)


def _page_search_order(total_pages: int) -> List[int]:
    """Last page first (check stub/summary is often appended at the end),
    then first page, then remaining middle pages, no duplicates."""
    order, seen = [], set()
    if total_pages > 1:
        order.append(total_pages - 1)
    order.append(0)
    order.extend(range(1, total_pages - 1))
    return [i for i in order if not (i in seen or seen.add(i))]


def extract_field_by_zone(pages, field_name, threshold=0.20):
    if not pages:
        return _empty_field_result()

    if field_name in ("check_number", "check_date", "check_amount"):
        instrument_pages = [p for p in pages if is_check_instrument_page(p.get("text", ""))]
        if instrument_pages:
            # Search ONLY the instrument page(s) first. A bare "AMOUNT" label
            # here outranks a nicer-sounding "Net Payment" label on a
            # statement-summary page, because this page is the actual
            # disbursement record -- the summary table's row may or may not
            # even represent a total.
            ordered = sorted(instrument_pages, key=lambda p: p.get("page_number", 0), reverse=True)
            result = _search_pages_by_level(ordered, field_name, threshold)
            if result["value"]:
                result["source_page_type"] = "check_instrument"
                return result
            # Instrument page found but nothing usable on it -- fall through
            # to the document-wide search below as a safety net only.

    page_order = _page_search_order(len(pages))
    result = _search_pages_by_level([pages[i] for i in page_order], field_name, threshold)
    if result["value"]:
        result["source_page_type"] = "document_wide"
    return result


def _search_pages_by_level(ordered_pages, field_name, threshold):
    """Level-cascade search restricted to a given, already-ordered page set."""
    for level in (1, 2, 3):
        aliases = get_aliases_by_level(field_name, level)
        if not aliases:
            continue
        level_candidates = []
        for page in ordered_pages:
            page_text = page.get("text", "")
            if not page_text.strip():
                continue
            candidates = find_field_candidates(page_text, field_name, aliases=aliases)
            if not candidates:
                continue
            candidates = deduplicate_candidates(candidates)
            total_lines = len(normalize_text(page_text).splitlines())
            for cand in candidates:
                zone = get_zone_for_line(cand.line_number, total_lines)
                level_candidates.append(ZoneCandidate(
                    candidate=cand, zone=zone, label_level=level,
                    page_number=page.get("page_number"),
                    zone_confidence_boost=get_zone_confidence_boost(zone),
                ))
        if level_candidates:
            result = _format_best_candidate(level_candidates, threshold)
            if result["value"]:
                return result
    return _empty_field_result()


def _format_best_candidate(zone_candidates: List[ZoneCandidate], threshold: float) -> Dict:
    if not zone_candidates:
        return _empty_field_result()

    boosted = [
        (min(1.0, zc.candidate.score + zc.zone_confidence_boost), zc)
        for zc in zone_candidates
    ]
    boosted.sort(key=lambda x: x[0], reverse=True)
    best_score, best_zc = boosted[0]

    if best_score < threshold:
        return _empty_field_result()

    best = best_zc.candidate
    return {
        "value": best.value,
        "confidence": round(best_score, 3),
        "alias_used": best.alias_used,
        "direction": best.direction,
        "line_number": best.line_number + 1,
        "zone": best_zc.zone,
        "label_level": best_zc.label_level,
        "page_number": best_zc.page_number,
        "zone_confidence_boost": round(best_zc.zone_confidence_boost, 3),
        "original_score": round(best.score, 3),
        "candidates_considered": len(zone_candidates),
        "all_candidates": [
            {
                "value": zc.candidate.value, "score": round(zc.candidate.score, 3),
                "boosted_score": round(s, 3), "alias": zc.candidate.alias_used,
                "level": zc.label_level, "zone": zc.zone, "page": zc.page_number,
                "direction": zc.candidate.direction, "line": zc.candidate.line_number + 1,
                "distance": zc.candidate.distance, "source": zc.candidate.source_line,
            }
            for s, zc in boosted[:5]
        ],
    }


def _empty_field_result() -> Dict:
    return {
        "value": "", "confidence": 0.0, "alias_used": None, "direction": None,
        "line_number": None, "zone": None, "label_level": None, "page_number": None,
        "zone_confidence_boost": 0.0, "original_score": 0.0,
        "candidates_considered": 0, "all_candidates": [],
    }


def extract_all_fields_by_zone(pages: List[Dict]) -> Dict:
    result = {}
    for field in ["check_number", "check_date", "check_amount", "practice_name", "insurance_name"]:
        result[field] = extract_field_by_zone(pages, field)

    full_text = "\n\n".join(p.get("text", "") for p in pages)
    from .cpt_extraction import extract_cpt_codes
    result["cpt_codes"] = extract_cpt_codes(full_text)

    result["_meta"] = {
        "total_pages": len(pages),
        "extraction_strategy": "zone_aware_hierarchical",
        "search_order": "level_1(all pages/zones) -> level_2 -> level_3, zone-boosted within each level",
    }
    return result