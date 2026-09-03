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
    r"\bELECTRONIC PAYMENT CLEARINGHOUSE\b|\bMICR\b|\bACH TRACE\b|"
    r"\bISSUE DATE\b|\bAMOUNT\b|\bDOLLARS\b|\bPAY\b|\bPAYDOLLARSCENTS\b|\bPAY DOLLARS CENTS\b|"
    r"\bPAYMENT INFORMATION\b|\bEXPLANATIONS\b",
    re.IGNORECASE,
)

CHECK_AMOUNT_PATTERN = re.compile(r'(?<![\d.])\$+\s*[\d,]+\.\d{2}(?!\d)')

AMOUNT_LABEL_PATTERN = re.compile(r"\bAMOUNT\b|\bPAYDOLLARSCENTS\b|\bPAY DOLLARS CENTS\b")
EXPLICIT_CHECK_AMOUNT_PATTERN = re.compile(
    r"\b(?:PAYMENT\s*/?\s*CHECK|CHECK|NET\s+PAYMENT)\s*AMOUNT\b|"
    r"\bAMOUNT\s+PAID\b|\bTRACE\b",
    re.IGNORECASE,
)
DOLLAR_VALUE_PATTERN = re.compile(r"\$+\s*([\d,]+\.\d{2})(?!\d)", re.IGNORECASE,)


def extract_amount_from_instrument_page(page_text: str) -> Dict:
    """extract_amount_from_instrument_page
    On a CONFIRMED check/draft instrument page, check_amount is simplified
    to one deterministic rule: find the "AMOUNT" label, then take the first
    value that carries an explicit '$' sign -- same line first, otherwise
    the nearest line below. No alias cascade, no column/tie resolution: a
    physical check/draft prints exactly one amount box, so none of that
    machinery is needed, and it's exactly what let per-claim rows or
    unrelated $ tables leak in on this page type.

    Handles noisy padding like "$$$$4,420.00" (multiple leading $ used as
    fraud-prevention filler) -- the regex only matches starting at the '$'
    immediately adjacent to actual digits, so the padding is skipped
    automatically.
    """
    lines = normalize_text(page_text).splitlines()

    for i, line in enumerate(lines):
        label_match = AMOUNT_LABEL_PATTERN.search(line)
        if not label_match:
            continue

        # Same line: "AMOUNT: $4,420.00" style
        same_line = DOLLAR_VALUE_PATTERN.search(line[label_match.end():])
        if same_line:
            return _instrument_amount_result(same_line.group(1), i, "same_line")

        # Otherwise: nearest line below with a $-prefixed value
        for offset in range(1, 6):
            idx = i + offset
            if idx >= len(lines):
                break
            below_match = DOLLAR_VALUE_PATTERN.search(lines[idx])
            if below_match:
                return _instrument_amount_result(below_match.group(1), idx, "below")
        # This "AMOUNT" occurrence had no $ value nearby -- keep scanning in
        # case the label appears again further down the page.

    return _empty_field_result()


def _instrument_amount_result(value: str, value_line: int, direction: str) -> Dict:
    return {
        "value": value,
        "confidence": 0.95,
        "alias_used": "Amount",
        "direction": direction,
        "line_number": value_line + 1,
        "zone": None,
        "label_level": None,
        "page_number": None,          # filled in by the caller
        "zone_confidence_boost": 0.0,
        "original_score": 0.95,
        "match_type": "instrument_amount_label",
        "candidates_considered": 1,
        "all_candidates": [],
    }

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
    return {"footer": 0.10, "header": 0.03, "body": 0.00}.get(zone, 0.0)


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
            ordered = sorted(instrument_pages, key=lambda p: p.get("page_number", 0))
            if field_name == "check_amount":
                first_page_text = normalize_text(pages[0].get("text", ""))
                first_twenty_lines = "\n".join(first_page_text.splitlines()[:20])
                if not EXPLICIT_CHECK_AMOUNT_PATTERN.search(first_twenty_lines):
                    for page in ordered:
                        result = extract_amount_from_instrument_page(page.get("text", ""))
                        if result.get("value"):
                            result["page_number"] = page.get("page_number")
                            result["source_page_type"] = "check_instrument"
                            return result

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


# A same-line "Label: $Value" hit (direction right/left) leaves zero ambiguity
# about which value belongs to the label -- it's the strongest signal this
# engine can produce. It should never lose to a zone-boosted guess pulled off
# a table row, so it's checked first, on its own unboosted score.
DIRECT_MATCH_MIN_SCORE = 0.90


def _format_best_candidate(zone_candidates: List[ZoneCandidate], threshold: float) -> Dict:
    if not zone_candidates:
        return _empty_field_result()

    direct_matches = [
        zc for zc in zone_candidates
        if zc.candidate.direction in ("right", "left") and zc.candidate.score >= DIRECT_MATCH_MIN_SCORE
    ]
    if direct_matches:
        direct_matches.sort(key=lambda zc: zc.candidate.score, reverse=True)
        best_zc = direct_matches[0]
        if best_zc.candidate.score >= threshold:
            return _build_result(best_zc, best_zc.candidate.score, zone_candidates)

    # No clean same-line match exists -- NOW zone position is a legitimate
    # tiebreaker among candidates that are all somewhat ambiguous anyway.
    boosted = [(min(1.0, zc.candidate.score + zc.zone_confidence_boost), zc) for zc in zone_candidates]
    boosted.sort(key=lambda x: x[0], reverse=True)
    best_score, best_zc = boosted[0]
    if best_score < threshold:
        return _empty_field_result()
    return _build_result(best_zc, best_score, zone_candidates)


def _build_result(best_zc: ZoneCandidate, final_score: float, all_zone_candidates: List[ZoneCandidate]) -> Dict:
    best = best_zc.candidate
    return {
        "value": best.value,
        "confidence": round(final_score, 3),
        "alias_used": best.alias_used,
        "direction": best.direction,
        "line_number": best.line_number + 1,
        "zone": best_zc.zone,
        "label_level": best_zc.label_level,
        "page_number": best_zc.page_number,
        "zone_confidence_boost": round(best_zc.zone_confidence_boost, 3),
        "original_score": round(best.score, 3),
        "match_type": "direct_same_line" if best.direction in ("right", "left") and best.score >= DIRECT_MATCH_MIN_SCORE else "positional",
        "candidates_considered": len(all_zone_candidates),
        "all_candidates": [
            {
                "value": zc.candidate.value, "score": round(zc.candidate.score, 3),
                "alias": zc.candidate.alias_used, "level": zc.label_level, "zone": zc.zone,
                "page": zc.page_number, "direction": zc.candidate.direction,
                "line": zc.candidate.line_number + 1, "distance": zc.candidate.distance,
                "source": zc.candidate.source_line,
            }
            for zc in sorted(all_zone_candidates, key=lambda z: z.candidate.score, reverse=True)[:5]
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

def extract_check_instrument_amount(text: str):
    """
    Extract the check amount from the AMOUNT field.

    Supports layouts where the amount is on the same line or on the
    following line, including values such as:

        AMOUNT $$$$4,420.00
        AMOUNT
        $$$$4,420.00
        AMOUNT
        $4,420.00
    """
    if not text:
        return None

    lines = text.splitlines()

    for i, line in enumerate(lines):

        if not re.search(r'\bAMOUNT\b', line, re.I):
            continue

        # --------------------------------------------------------
        # 1. Amount on the same line as AMOUNT
        # --------------------------------------------------------
        match = CHECK_AMOUNT_PATTERN.search(line)

        if match:
            return match.group(0).replace(" ", "")

        # --------------------------------------------------------
        # 2. Amount on the following line
        # --------------------------------------------------------
        for next_idx in range(i + 1, min(i + 3, len(lines))):
            next_line = lines[next_idx].strip()

            if not next_line:
                continue

            match = CHECK_AMOUNT_PATTERN.search(next_line)

            if match:
                return match.group(0).replace(" ", "")

            # Don't search through another field/header.
            if re.search(
                r'\b(?:ISSUE\s+DATE|CHECK\s+NUMBER|PAY|PAYABLE)\b',
                next_line,
                re.I
            ):
                break

    return None