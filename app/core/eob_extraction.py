"""Document-level EOB field extraction.

- check_number, check_date, check_amount, insurance_name, practice_name:
  searched only within the leading header pages (get_header_page_range):
    > 4 total pages  -> search pages 1-3
    <= 4 total pages -> search pages 1-2 (capped at actual page count)
- cpt_codes: searched across ALL pages (procedure lines can appear anywhere).
"""
import re
import difflib
from collections import defaultdict

from .field_extraction import extract_field
from .zone_extraction import extract_field_by_zone 
from .cpt_extraction import extract_cpt_codes
from .patterns import NAME_BOILERPLATE_BLOCKLIST, US_ADDRESS_LINE, LABEL_FRAGMENT_WORDS
from .payers import KNOWN_PAYERS


def _is_boilerplate(value: str) -> bool:
    if not value or not value.strip():
        return True
    stripped = value.strip()
    if stripped.lower() in LABEL_FRAGMENT_WORDS:
        return True
    return bool(NAME_BOILERPLATE_BLOCKLIST.search(stripped))


def matches_known_payer(text: str, min_ratio: float = 0.82):
    text_norm = re.sub(r"[^a-z0-9 ]", "", text.lower()).strip()
    for canonical, aliases in KNOWN_PAYERS.items():
        for alias in aliases:
            if alias in text_norm:
                return True, canonical
            ratio = difflib.SequenceMatcher(None, alias, text_norm).ratio()
            if ratio >= min_ratio:
                return True, canonical
    return False, None


def get_insurance_candidates_from_first_page(pages, header_page_count=1, num_lines=20, top_n=10):
    """
    Scan the first `num_lines` lines of the header pages to find insurance name candidates.
    Returns a list of dicts: [{"text": candidate, "score": score}, ...] sorted by score descending.
    """
    if not pages:
        return []

    header_pages = pages[:max(1, header_page_count)]
    combined_text = "\n\n".join(p["text"] for p in header_pages)
    lines = [line.strip() for line in combined_text.split('\n') if line.strip()]
    lines = lines[:num_lines]

    candidates = []

    for line in lines:
        if len(line) < 3 or _is_boilerplate(line):
            continue
        # Skip lines that look like addresses (contain ZIP)
        if re.search(r'\b\d{5}(?:-\d{4})?\b', line):
            continue

        score = 0.0
        # 1. Known payer match (strongest)
        is_known, _ = matches_known_payer(line)
        if is_known:
            score += 0.5
        # 2. Insurance keywords
        if re.search(r'(Insurance|Company|Corp|Inc|Care|Health|Services|Plan|Carrier)', line, re.I):
            score += 0.15
        # 3. All caps (often a name)
        if line.isupper():
            score += 0.1
        # 4. Length boost
        if len(line) > 15:
            score += 0.1
        # 5. Penalty for labels ending with colon
        if re.match(r'^[A-Za-z\s]+:\s*$', line):
            score -= 0.4

        if score > 0:
            candidates.append({"text": line, "score": score})

    # Sort by score descending and return top N
    candidates.sort(key=lambda x: x["score"], reverse=True)
    return candidates[:top_n]


def check_candidates_across_pages(candidates, pages, threshold=0.8):
    """
    For each candidate (line text), count on how many pages it appears.
    Returns (best_candidate, confidence) if any candidate appears on >= threshold * total_pages pages,
    else (None, 0.0).
    """
    if not candidates or not pages:
        return None, 0.0

    total_pages = len(pages)
    # Normalize candidates: lower-case, stripped
    normalized_candidates = {cand["text"].lower(): cand["text"] for cand in candidates}
    candidate_lower_list = list(normalized_candidates.keys())

    # For each page, collect the set of lines (lower-case) from the first 30 lines
    page_line_sets = []
    for page in pages:
        text = page.get("text", "")
        if not text:
            continue
        lines = [line.strip().lower() for line in text.split('\n') if line.strip()]
        lines = lines[:30]  # consider first 30 lines per page
        # Filter out boilerplate and address lines
        filtered = []
        for line in lines:
            if len(line) < 3 or _is_boilerplate(line):
                continue
            if re.search(r'\b\d{5}(?:-\d{4})?\b', line):
                continue
            filtered.append(line)
        page_line_sets.append(set(filtered))

    if not page_line_sets:
        return None, 0.0

    # Count occurrences per candidate
    candidate_counts = defaultdict(int)
    for cand_lower in candidate_lower_list:
        count = 0
        for line_set in page_line_sets:
            if cand_lower in line_set:
                count += 1
        candidate_counts[cand_lower] = count

    # Find best candidate
    best_candidate_lower = None
    best_count = 0
    for cand_lower, count in candidate_counts.items():
        if count > best_count:
            best_count = count
            best_candidate_lower = cand_lower

    if best_candidate_lower is None:
        return None, 0.0

    ratio = best_count / total_pages
    if ratio >= threshold:
        # Get original case
        original = normalized_candidates[best_candidate_lower]
        confidence = 0.5 + ratio * 0.5  # ratio 0.8 -> 0.9, 1.0 -> 1.0
        return original, confidence

    return None, 0.0


def extract_insurance_from_first_lines(pages, header_page_count=1, num_lines=20):
    """
    Scan the first `num_lines` lines of the header pages to find the best insurance name.
    Returns (candidate, confidence) or (None, 0.0).
    """
    candidates = get_insurance_candidates_from_first_page(pages, header_page_count, num_lines, top_n=1)
    if candidates:
        return candidates[0]["text"], min(1.0, candidates[0]["score"])
    return None, 0.0


def detect_payor_and_practice_from_first_page(pages, header_page_count=1):
    """
    Enhanced detection for insurance (payor) and practice names from first page.
    Priority:
    1. Get candidates from first page, then check consistency across all pages.
    2. If consistent, use it.
    3. Otherwise, fallback to best candidate from first page + gazetteer.
    """
    if not pages:
        return {"insurance_name": {"value": "", "confidence": 0.0},
                "practice_name": {"value": "", "confidence": 0.0}}

    header_pages = pages[:max(1, header_page_count)]
    combined_text = "\n\n".join(p["text"] for p in header_pages)
    lines = combined_text.split('\n')

    result = {
        "insurance_name": {"value": "", "confidence": 0.0, "source": ""},
        "practice_name": {"value": "", "confidence": 0.0, "source": ""}
    }

    # ------------------------------------------------------------
    #  INSURANCE: Priority 1 – candidates from first page, check across pages
    # ------------------------------------------------------------
    first_page_candidates = get_insurance_candidates_from_first_page(pages, header_page_count, top_n=10)
    if first_page_candidates:
        consistent_candidate, conf = check_candidates_across_pages(first_page_candidates, pages, threshold=0.8)
        if consistent_candidate and conf >= 0.7:
            result["insurance_name"] = {
                "value": consistent_candidate,
                "confidence": conf,
                "source": "page_wide_consistency_from_first_page"
            }

    # ------------------------------------------------------------
    #  INSURANCE: Priority 2 – best candidate from first page (fallback)
    # ------------------------------------------------------------
    if not result["insurance_name"]["value"] or result["insurance_name"]["confidence"] < 0.7:
        cand, conf = extract_insurance_from_first_lines(pages, header_page_count)
        if cand and conf >= 0.3:
            result["insurance_name"] = {
                "value": cand,
                "confidence": conf,
                "source": "first_lines_scan"
            }

    # ------------------------------------------------------------
    #  INSURANCE: Priority 3 – gazetteer (known payer)
    # ------------------------------------------------------------
    if not result["insurance_name"]["value"] or result["insurance_name"]["confidence"] < 0.5:
        for line in lines:
            is_known, canonical = matches_known_payer(line)
            if is_known:
                match = re.search(
                    r'([A-Z][A-Za-z ,.&\-]{3,60}(?:Insurance|Company|Corp|Inc|Aetna|Blue|Cross|United|Cigna|Humana)\b)',
                    line, re.I
                )
                if match:
                    value = match.group(1).strip()
                else:
                    if line[0].isupper() and len(line.strip()) > 5:
                        value = line.strip()
                    else:
                        value = canonical
                confidence = 0.85
                if confidence > result["insurance_name"]["confidence"]:
                    result["insurance_name"] = {
                        "value": value,
                        "confidence": confidence,
                        "source": "gazetteer"
                    }
                    break

    # ------------------------------------------------------------
    #  PRACTICE NAME: existing fallbacks (no alias)
    # ------------------------------------------------------------
    # 1. "Pay To" section
    if not result["practice_name"]["value"] or result["practice_name"]["confidence"] < 0.5:
        pay_to_section = re.search(r'Pay\s*To[:\s]+([A-Z][A-Za-z ,.&\-]{3,60})', combined_text, re.IGNORECASE)
        if pay_to_section:
            value = pay_to_section.group(1).strip()
            if len(value) > 3 and not _is_boilerplate(value):
                confidence = 0.85
                if re.search(r'(LLC|PLLC|PC|PA)', value, re.IGNORECASE):
                    confidence = 0.95
                if confidence > result["practice_name"]["confidence"]:
                    result["practice_name"] = {
                        "value": value,
                        "confidence": confidence,
                        "source": "pay_to"
                    }

    # 2. Patterns ending with LLC, PC, etc.
    if not result["practice_name"]["value"] or result["practice_name"]["confidence"] < 0.5:
        practice_patterns = [
            r'([A-Z][A-Za-z ,.&\-]{3,50}(?:LLC|PLLC|PC|P\.C\.|P\.A\.|Associates|Medical\s+Group|Clinic|Family\s+Practice))\b',
            r'([A-Z][A-Za-z ,.&\-]{3,50}(?:MD|DO|DDS|DMD|DC|PhD)(?:\s+[A-Z][A-Za-z]+)?\s+(?:&|and)\s+[A-Z][A-Za-z]+)',
        ]
        for pattern in practice_patterns:
            for match in re.finditer(pattern, combined_text, re.IGNORECASE):
                value = match.group(1).strip()
                if len(value) > 5 and any(c.isalpha() for c in value) and not _is_boilerplate(value):
                    confidence = 0.70
                    if re.search(r'(LLC|PLLC|PC|PA|Associates|Medical\s+Group)', value, re.IGNORECASE):
                        confidence += 0.15
                    if len(value) > 15:
                        confidence += 0.10
                    if confidence > result["practice_name"]["confidence"]:
                        result["practice_name"] = {
                            "value": value,
                            "confidence": min(1.0, confidence),
                            "source": "pattern"
                        }
                        break
            if result["practice_name"]["value"]:
                break

    # 3. Address adjacency (line above a US address)
    if not result["practice_name"]["value"] or result["practice_name"]["confidence"] < 0.5:
        for i, line in enumerate(lines):
            if not US_ADDRESS_LINE.search(line):
                continue
            for back in (1, 2):
                idx = i - back
                if idx < 0:
                    continue
                candidate = lines[idx].strip()
                if (
                    len(candidate) < 3
                    or _is_boilerplate(candidate)
                    or not candidate[0].isupper()
                    or US_ADDRESS_LINE.search(candidate)
                ):
                    continue
                if matches_known_payer(candidate, min_ratio=0.85)[0]:
                    continue
                confidence = 0.75 - (back - 1) * 0.10
                if confidence > result["practice_name"]["confidence"]:
                    result["practice_name"] = {
                        "value": candidate,
                        "confidence": confidence,
                        "source": "address_adjacency"
                    }
                break

    return result


def get_header_page_range(total_pages):
    if total_pages >= 4:
        return min(2, total_pages)
    return min(1, total_pages)

def get_candidate_pages(pages, header_page_count):
    """
    check_number/check_date/check_amount mostly live on page 1, 2, or the
    LAST page (the check stub / remittance summary is often appended at the
    end of a multi-page EOB). pages[:header_page_count] alone silently drops
    that last page -- this returns header pages UNION the last page.
    """
    total = len(pages)
    if total == 0:
        return []
    count = min(header_page_count, total)
    idx = set(range(count))
    # For longer PDFs, include the last 3 pages unconditionally.
    if total > 4:
        idx.update(range(max(0, total - 3), total))
    else:
        # For 4 pages or fewer, include the last page.
        idx.add(total - 1)
    return [pages[i] for i in sorted(idx)]

def extract_eob_data_from_pages(pages):
    """
    Extract all EOB fields from a FULL document (list of page dicts).
    No spatial layout used – insurance extracted from first 20 lines.
    """
    if not pages:
        return {}

    total_pages = len(pages)
    header_page_count = get_header_page_range(total_pages)
    header_pages = pages[:header_page_count]
    candidate_pages = get_candidate_pages(pages, header_page_count)   # NEW: includes last page

    header_text = "\n\n".join(p["text"] for p in header_pages)
    full_text = "\n\n".join(p["text"] for p in pages)

    result = {}

    payor_practice_result = detect_payor_and_practice_from_first_page(pages, header_page_count)

    for field in ["check_number", "check_date", "check_amount"]:
        result[field] = extract_field_by_zone(candidate_pages, field)

    if payor_practice_result.get("insurance_name", {}).get("value"):
        result["insurance_name"] = {
            "value": payor_practice_result["insurance_name"]["value"],
            "confidence": round(payor_practice_result["insurance_name"]["confidence"], 3),
            "alias_used": payor_practice_result["insurance_name"].get("source", "enhanced_detection"),
            "direction": "first_page",
            "line_number": 1,
            "candidates_considered": 1,
            "all_candidates": []
        }
    else:
        result["insurance_name"] = {"value": "", "confidence": 0.0, "alias_used": None, "direction": None, "line_number": None, "candidates_considered": 0, "all_candidates": []}

    if payor_practice_result.get("practice_name", {}).get("value"):
        result["practice_name"] = {
            "value": payor_practice_result["practice_name"]["value"],
            "confidence": round(payor_practice_result["practice_name"]["confidence"], 3),
            "alias_used": payor_practice_result["practice_name"].get("source", "enhanced_detection"),
            "direction": "first_page",
            "line_number": 1,
            "candidates_considered": 1,
            "all_candidates": []
        }
    else:
        result["practice_name"] = {"value": "", "confidence": 0.0, "alias_used": None, "direction": None, "line_number": None, "candidates_considered": 0, "all_candidates": []}

    result["cpt_codes"] = extract_cpt_codes(full_text)

    result["_meta"] = {
        "total_pages": total_pages,
        "header_pages_searched": header_page_count,
        "candidate_page_numbers": [p["page_number"] for p in candidate_pages],
        "payor_practice_detection": payor_practice_result
    }

    return result


def extract_eob_data(text):
    """Legacy helper for a single page's text."""
    if not text or not isinstance(text, str):
        return {}
    result = {}
    for field in ["check_number", "check_date", "check_amount", "practice_name", "insurance_name"]:
        result[field] = extract_field(text, field)
    result["cpt_codes"] = extract_cpt_codes(text)
    return result