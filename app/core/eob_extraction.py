"""Document-level EOB field extraction.

- check_number, check_date, check_amount, insurance_name, practice_name:
  searched only within the leading header pages (get_header_page_range):
    > 4 total pages  -> search pages 1-3
    <= 4 total pages -> search pages 1-2 (capped at actual page count)
- cpt_codes: searched across ALL pages (procedure lines can appear anywhere).
"""
"""Document-level EOB field extraction with known payor/practice matching."""
import re
from collections import defaultdict
from . import payers as payers_module
from .field_extraction import extract_field
from .zone_extraction import extract_field_by_zone
from .cpt_extraction import extract_cpt_codes
from .scoring import validate_check_number
from .patterns import NAME_BOILERPLATE_BLOCKLIST, US_ADDRESS_LINE, LABEL_FRAGMENT_WORDS


def _is_boilerplate(value: str) -> bool:
    if not value or not value.strip():
        return True
    stripped = value.strip()
    if stripped.lower() in LABEL_FRAGMENT_WORDS:
        return True
    return bool(NAME_BOILERPLATE_BLOCKLIST.search(stripped))


ENTITY_MATCH_THRESHOLD = 0.80


def _entity_pages(pages, entity_matcher, known_name, aliases):
    """Return the number of pages containing the same known entity."""
    names = [known_name] + [x for x in aliases if x]
    compact_names = [re.sub(r"[^a-z0-9]", "", x.lower()) for x in names]
    present = 0
    for page in pages:
        text = page.get("text", "") or ""
        compact = re.sub(r"[^a-z0-9]", "", text.lower())
        if any(n and n in compact for n in compact_names):
            present += 1
    return present


def _find_best_known_entity(pages, entity_type, header_page_count=1):
    """Ordered known-entity extraction: page 1 -> match -> page consistency -> final value."""
    if not pages:
        return {"value": "", "confidence": 0.0, "source": "not_found"}

    header_page = pages[0]
    lines = [line.strip() for line in (header_page.get("text", "") or "").splitlines() if line.strip()]
    matcher = (payers_module.match_known_payer_text if entity_type == "payor"
               else payers_module.match_known_practice_text)
    records = (payers_module.get_all_payors() if entity_type == "payor"
               else payers_module.get_all_practices())

    candidates = []
    for line_index, line in enumerate(lines):
        if len(line) < 3 or _is_boilerplate(line):
            continue
        if re.search(r"\b\d{5}(?:-\d{4})?\b", line):
            continue
        matched, canonical, match_score, matched_text = matcher(line, ENTITY_MATCH_THRESHOLD)
        if not matched or not canonical or match_score < ENTITY_MATCH_THRESHOLD:
            continue
        record = next((r for r in records if str(r.get("name", "")).strip().lower() == canonical.lower()), None)
        if not record:
            continue
        aliases = record.get("aliases", []) or []
        page_presence = _entity_pages(pages, matcher, canonical, aliases)
        presence_ratio = page_presence / max(len(pages), 1)
        # Page consistency increases the score without allowing an unknown name through.
        final_score = min(1.0, match_score * 0.75 + presence_ratio * 0.25)
        candidates.append({
            "canonical": canonical,
            "matched_text": matched_text or line,
            "match_score": match_score,
            "page_presence": page_presence,
            "page_ratio": presence_ratio,
            "score": final_score,
            "line_number": line_index + 1,
        })

    if not candidates:
        return {"value": "", "confidence": 0.0, "source": "not_found"}

    # Prefer strongest match first, then consistency across pages.
    candidates.sort(key=lambda x: (x["score"], x["page_ratio"], x["match_score"]), reverse=True)
    best = candidates[0]

    # If a configured name/alias is contained in a longer header line,
    # always return the canonical list value. This prevents PDF noise such as
    # "Payee Tax ID" or another name from being included in the result.
    matched_compact = re.sub(r"[^a-z0-9]", "", best["matched_text"].lower())
    canonical_compact = re.sub(r"[^a-z0-9]", "", best["canonical"].lower())
    value = (
        best["canonical"]
        if matched_compact == canonical_compact
        else best["matched_text"]
    )
    return {
        "value": value,
        "confidence": round(best["score"], 3),
        "source": "known_payor_matching" if entity_type == "payor" else "known_practice_matching",
        "matched_name": best["canonical"],
        "file_text": best["matched_text"],
        "match_score": round(best["match_score"], 3),
        "page_presence": best["page_presence"],
        "page_ratio": round(best["page_ratio"], 3),
        "line_number": best["line_number"],
    }


def extract_insurance_from_header(pages, header_page_count=1):
    result = _find_best_known_entity(pages, "payor", header_page_count)
    return result["value"] or None, result["confidence"]


def extract_practice_from_header(pages, header_page_count=1):
    result = _find_best_known_entity(pages, "practice", header_page_count)
    return result["value"] or None, result["confidence"]


def detect_payor_and_practice_from_header(pages, header_page_count=1):
    """Extract payor and practice only when they match the configured lists."""
    header_pages = pages[:header_page_count] if pages else []
    payor = _find_best_known_entity(header_pages, "payor", header_page_count)
    practice = _find_best_known_entity(header_pages, "practice", header_page_count)
    return {
        "insurance_name": payor,
        "practice_name": practice,
    }


def get_header_page_range(total_pages):
    if total_pages <= 0:
        return 0
    if total_pages > 4:
        return 3
    return min(2, total_pages)



def get_candidate_pages(pages, header_page_count):
    total = len(pages)
    if total == 0:
        return []
    count = min(header_page_count, total)
    idx = set(range(count))
    if total > 4:
        idx.update(range(max(0, total - 3), total))
    else:
        idx.update(range(total))
    return [pages[i] for i in sorted(idx)]


def extract_eob_data_from_pages(pages):
    """Extract all EOB fields from a full document."""
    if not pages:
        return {}

    total_pages = len(pages)
    header_page_count = get_header_page_range(total_pages)
    candidate_pages = get_candidate_pages(pages, header_page_count)

    header_pages = pages[:header_page_count]
    full_text = "\n\n".join(p["text"] for p in pages)

    result = {}

    result["check_number"] = extract_field_by_zone(candidate_pages, "check_number")
    result["check_date"] = extract_field_by_zone(candidate_pages, "check_date")

    if validate_check_number(result["check_number"].get("value", "")):
        result["payment_status"] = "pay"
        result["check_amount"] = extract_field_by_zone(candidate_pages, "check_amount")
    else:
        result["check_number"]["value"] = ""
        result["check_number"]["confidence"] = 0.0
        result["check_number"]["alias_used"] = None
        result["payment_status"] = "no_pay"
        result["check_amount"] = {
            "value": "0",
            "confidence": 1.0,
            "alias_used": "no_pay_rule",
            "direction": None,
            "line_number": None,
            "zone": None,
            "label_level": None,
            "page_number": None,
            "zone_confidence_boost": 0.0,
            "original_score": 1.0,
            "match_type": "no_pay_rule",
            "candidates_considered": 0,
            "all_candidates": [],
        }

    # Extract payor and practice from header
    payor_practice_result = detect_payor_and_practice_from_header(header_pages, header_page_count)

    if payor_practice_result.get("insurance_name", {}).get("value"):
        result["insurance_name"] = {
            "value": payor_practice_result["insurance_name"]["value"],
            "confidence": round(payor_practice_result["insurance_name"]["confidence"], 3),
            "alias_used": payor_practice_result["insurance_name"].get("source", "enhanced_detection"),
            "direction": "header",
            "line_number": 1,
            "candidates_considered": 1,
            "all_candidates": []
        }
    else:
        result["insurance_name"] = {
            "value": "", "confidence": 0.0, "alias_used": None,
            "direction": None, "line_number": None,
            "candidates_considered": 0, "all_candidates": []
        }

    if payor_practice_result.get("practice_name", {}).get("value"):
        result["practice_name"] = {
            "value": payor_practice_result["practice_name"]["value"],
            "confidence": round(payor_practice_result["practice_name"]["confidence"], 3),
            "alias_used": payor_practice_result["practice_name"].get("source", "enhanced_detection"),
            "direction": "header",
            "line_number": 1,
            "candidates_considered": 1,
            "all_candidates": []
        }
    else:
        result["practice_name"] = {
            "value": "", "confidence": 0.0, "alias_used": None,
            "direction": None, "line_number": None,
            "candidates_considered": 0, "all_candidates": []
        }

    result["cpt_codes"] = extract_cpt_codes(full_text)

    result["_meta"] = {
        "total_pages": total_pages,
        "header_pages_searched": header_page_count,
        "candidate_page_numbers": [p["page_number"] for p in candidate_pages],
        "payor_practice_detection": payor_practice_result
    }

    return result