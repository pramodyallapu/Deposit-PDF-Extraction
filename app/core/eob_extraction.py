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
import logging  # <-- ADD THIS

from .field_extraction import extract_field, get_learned_pattern_candidates, get_free_text_candidates, learn_from_correction
from .cpt_extraction import extract_cpt_codes 
from .patterns import NAME_BOILERPLATE_BLOCKLIST, US_ADDRESS_LINE, LABEL_FRAGMENT_WORDS
from .payers import KNOWN_PAYERS


# ============================================================
# LEARNING INTEGRATION HELPER (DEFINED ONCE AT TOP)
# ============================================================

def _extract_with_learning(text, field_name, corrections=None, doc_metadata=None):
    """
    Extract a field with learning support.
    
    IMPORTANT: Learns from corrections even if extraction was high confidence.
    """
    from .field_extraction import deduplicate_candidates, find_field_candidates
    
    # 1. Normal extraction (alias-based)
    result = extract_field(text, field_name)
    
    # 2. If confidence is low or no value, try learned patterns as fallback
    if result['confidence'] < 0.25 or not result['value']:
        learned_candidates = get_learned_pattern_candidates(text, field_name)
        
        if learned_candidates:
            existing_candidates = find_field_candidates(text, field_name)
            all_candidates = existing_candidates + learned_candidates
            
            if all_candidates:
                all_candidates = deduplicate_candidates(all_candidates)
                all_candidates.sort(key=lambda x: x.score, reverse=True)
                best = all_candidates[0]
                
                if best.score > result['confidence']:
                    result = {
                        "value": best.value,
                        "confidence": round(best.score, 3),
                        "alias_used": best.alias_used,
                        "direction": best.direction,
                        "line_number": best.line_number + 1,
                        "candidates_considered": len(all_candidates),
                        "all_candidates": [{"value": c.value, "score": round(c.score, 3), 
                                           "alias": c.alias_used, "direction": c.direction,
                                           "line": c.line_number + 1, "distance": c.distance, 
                                           "source": c.source_line} for c in all_candidates]
                    }
    
    # 3. LEARN FROM CORRECTION (if provided) - ALWAYS learn, regardless of confidence
    if corrections and field_name in corrections:
        corrected_value = corrections[field_name]
        extracted_value = result.get('value', '')
        
        # Always learn from correction
        learn_from_correction(
            text, 
            field_name, 
            extracted_value, 
            corrected_value,
            result.get('confidence', 0.0),
            doc_metadata
        )
    
    return result


# ============================================================
# EXISTING FUNCTIONS (UNCHANGED)
# ============================================================

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
        if re.search(r'\b\d{5}(?:-\d{4})?\b', line):
            continue

        score = 0.0
        is_known, _ = matches_known_payer(line)
        if is_known:
            score += 0.5
        if re.search(r'(Insurance|Company|Corp|Inc|Care|Health|Services|Plan|Carrier)', line, re.I):
            score += 0.15
        if line.isupper():
            score += 0.1
        if len(line) > 15:
            score += 0.1
        if re.match(r'^[A-Za-z\s]+:\s*$', line):
            score -= 0.4

        if score > 0:
            candidates.append({"text": line, "score": score})

    candidates.sort(key=lambda x: x["score"], reverse=True)
    return candidates[:top_n]


def check_candidates_across_pages(candidates, pages, threshold=0.8):
    if not candidates or not pages:
        return None, 0.0

    total_pages = len(pages)
    normalized_candidates = {cand["text"].lower(): cand["text"] for cand in candidates}
    candidate_lower_list = list(normalized_candidates.keys())

    page_line_sets = []
    for page in pages:
        text = page.get("text", "")
        if not text:
            continue
        lines = [line.strip().lower() for line in text.split('\n') if line.strip()]
        lines = lines[:30]
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

    candidate_counts = defaultdict(int)
    for cand_lower in candidate_lower_list:
        count = 0
        for line_set in page_line_sets:
            if cand_lower in line_set:
                count += 1
        candidate_counts[cand_lower] = count

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
        original = normalized_candidates[best_candidate_lower]
        confidence = 0.5 + ratio * 0.5
        return original, confidence

    return None, 0.0


def extract_insurance_from_first_lines(pages, header_page_count=1, num_lines=20):
    candidates = get_insurance_candidates_from_first_page(pages, header_page_count, num_lines, top_n=1)
    if candidates:
        return candidates[0]["text"], min(1.0, candidates[0]["score"])
    return None, 0.0


def get_header_page_range(total_pages):
    if total_pages > 4:
        return min(3, total_pages)
    return min(2, total_pages)


def detect_payor_and_practice_from_first_page(pages, header_page_count=1):
    """
    Enhanced detection for insurance (payor) and practice names from first page.
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

    # INSURANCE: Priority 1 – candidates from first page, check across pages
    first_page_candidates = get_insurance_candidates_from_first_page(pages, header_page_count, top_n=10)
    if first_page_candidates:
        consistent_candidate, conf = check_candidates_across_pages(first_page_candidates, pages, threshold=0.8)
        if consistent_candidate and conf >= 0.7:
            result["insurance_name"] = {
                "value": consistent_candidate,
                "confidence": conf,
                "source": "page_wide_consistency_from_first_page"
            }

    # INSURANCE: Priority 2 – best candidate from first page (fallback)
    if not result["insurance_name"]["value"] or result["insurance_name"]["confidence"] < 0.7:
        cand, conf = extract_insurance_from_first_lines(pages, header_page_count)
        if cand and conf >= 0.3:
            result["insurance_name"] = {
                "value": cand,
                "confidence": conf,
                "source": "first_lines_scan"
            }

    # INSURANCE: Priority 3 – gazetteer (known payer)
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

    # PRACTICE NAME: existing fallbacks
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

    # ============================================================
    # LEARNING FALLBACK (for insurance/practice)
    # ============================================================
    
    if result["insurance_name"]["confidence"] < 0.5:
        try:
            candidates = get_free_text_candidates(combined_text, "insurance_name")
            if candidates:
                best = max(candidates, key=lambda x: x['score'])
                if best['score'] > 0.4 and best['score'] > result["insurance_name"]["confidence"]:
                    result["insurance_name"] = {
                        "value": best['text'],
                        "confidence": best['score'],
                        "source": "learned_free_text"
                    }
        except Exception as e:
            logging.debug(f"Free-text learning failed: {e}")
    
    if result["practice_name"]["confidence"] < 0.5:
        try:
            candidates = get_free_text_candidates(combined_text, "practice_name")
            if candidates:
                best = max(candidates, key=lambda x: x['score'])
                if best['score'] > 0.4 and best['score'] > result["practice_name"]["confidence"]:
                    result["practice_name"] = {
                        "value": best['text'],
                        "confidence": best['score'],
                        "source": "learned_free_text"
                    }
        except Exception as e:
            logging.debug(f"Free-text learning failed: {e}")

    return result


# ============================================================
# MAIN EXTRACTION FUNCTION
# ============================================================

def extract_eob_data_from_pages(pages, corrections=None, doc_metadata=None):
    """
    Extract all EOB fields from a FULL document.
    
    Args:
        pages: List of page dicts with 'text' and 'page_number'
        corrections: Optional dict of user corrections {field_name: corrected_value}
        doc_metadata: Optional metadata for learning
    """
    if not pages:
        return {}

    total_pages = len(pages)
    header_page_count = get_header_page_range(total_pages)

    header_indices = list(range(0, min(header_page_count, total_pages)))
    last_index = total_pages - 1
    if last_index not in header_indices:
        search_indices = sorted(set(header_indices + [last_index]))
    else:
        search_indices = header_indices

    search_pages = [pages[i] for i in search_indices]
    search_text = "\n\n".join(p["text"] for p in search_pages)

    header_pages = pages[:header_page_count]
    header_text = "\n\n".join(p["text"] for p in header_pages)
    full_text = "\n\n".join(p["text"] for p in pages)

    result = {}

    # Extract check fields with learning
    for field in ["check_number", "check_date", "check_amount"]:
        result[field] = _extract_with_learning(
            search_text, field, corrections, doc_metadata
        )

    # Insurance & Practice (with learning)
    payor_practice_result = detect_payor_and_practice_from_first_page(pages, header_page_count)
    
    for field in ["insurance_name", "practice_name"]:
        if corrections and field in corrections:
            extracted_value = payor_practice_result.get(field, {}).get('value', '')
            corrected_value = corrections[field]
            
            learn_from_correction(
                header_text,
                field,
                extracted_value,
                corrected_value,
                payor_practice_result.get(field, {}).get('confidence', 0.0),
                doc_metadata
            )
            
            payor_practice_result[field] = {
                "value": corrected_value,
                "confidence": 1.0,
                "source": "user_correction"
            }
        elif field in payor_practice_result:
            if payor_practice_result[field].get('confidence', 0) < 0.3:
                learned_candidates = get_free_text_candidates(header_text, field)
                if learned_candidates:
                    best = max(learned_candidates, key=lambda x: x['score'])
                    if best['score'] > payor_practice_result[field].get('confidence', 0):
                        payor_practice_result[field] = {
                            "value": best['text'],
                            "confidence": best['score'],
                            "source": "learned_free_text"
                        }
    
    result["insurance_name"] = payor_practice_result.get("insurance_name", {"value": "", "confidence": 0.0})
    result["practice_name"] = payor_practice_result.get("practice_name", {"value": "", "confidence": 0.0})

    # CPT codes (unchanged)
    result["cpt_codes"] = extract_cpt_codes(full_text)

    # Metadata
    result["_meta"] = {
        "total_pages": total_pages,
        "header_pages_searched": header_page_count,
        "check_fields_searched_on_pages": [p["page_number"] for p in search_pages],
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