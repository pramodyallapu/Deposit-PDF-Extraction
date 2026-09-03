"""Updated EOB extraction using zone-aware approach.

This module shows how to integrate the new zone-based extraction into the existing
eob_extraction.py module. Can be used to replace extract_eob_data_from_pages().
"""

from .zone_extraction import extract_field_by_zone, extract_all_fields_by_zone
from .cpt_extraction import extract_cpt_codes
from .eob_extraction import (
    detect_payor_and_practice_from_first_page,
    get_header_page_range
)


def extract_eob_data_from_pages_v2(pages):
    """
    IMPROVED EXTRACTION with zone awareness and label hierarchy.
    
    Key improvements over v1:
    1. Searches ALL pages (not just header pages)
    2. Prioritizes footer zones (where summary values are)
    3. Uses 3-level label hierarchy (L1 > L2 > L3)
    4. Last page checked first (typically has summary)
    
    Extracts:
    - check_number: From L1 labels (EPC Draft #, Check/EFT No)
    - check_date: From L1 labels (Payment/Check Date, Check Date)
    - check_amount: From L1 labels (Payment/Check Amount, Check Amount)
    - practice_name: Enhanced detection (still from first page)
    - insurance_name: Enhanced detection (still from first page)
    - cpt_codes: From all pages (procedure lines can appear anywhere)
    
    Args:
        pages: List of page dicts with {"page_number", "text", "method"}
    
    Returns:
        Dict with all extracted fields and metadata
    """
    
    if not pages:
        return {}
    
    total_pages = len(pages)
    result = {}
    
    # ─────────────────────────────────────────────────────
    # 1. INSURANCE & PRACTICE (from first page - unchanged)
    # ─────────────────────────────────────────────────────
    header_page_count = get_header_page_range(total_pages)
    payor_practice_result = detect_payor_and_practice_from_first_page(pages, header_page_count)
    
    if payor_practice_result.get("insurance_name", {}).get("value"):
        result["insurance_name"] = {
            "value": payor_practice_result["insurance_name"]["value"],
            "confidence": round(payor_practice_result["insurance_name"]["confidence"], 3),
            "alias_used": payor_practice_result["insurance_name"].get("source", "enhanced_detection"),
            "direction": "first_page",
            "line_number": 1,
            "zone": "header",
            "candidates_considered": 1,
            "all_candidates": [],
            "extraction_method": "enhanced_detection"
        }
    else:
        result["insurance_name"] = {
            "value": "",
            "confidence": 0.0,
            "alias_used": None,
            "direction": None,
            "line_number": None,
            "zone": None,
            "candidates_considered": 0,
            "all_candidates": [],
            "extraction_method": "enhanced_detection"
        }
    
    if payor_practice_result.get("practice_name", {}).get("value"):
        result["practice_name"] = {
            "value": payor_practice_result["practice_name"]["value"],
            "confidence": round(payor_practice_result["practice_name"]["confidence"], 3),
            "alias_used": payor_practice_result["practice_name"].get("source", "enhanced_detection"),
            "direction": "first_page",
            "line_number": 1,
            "zone": "header",
            "candidates_considered": 1,
            "all_candidates": [],
            "extraction_method": "enhanced_detection"
        }
    else:
        result["practice_name"] = {
            "value": "",
            "confidence": 0.0,
            "alias_used": None,
            "direction": None,
            "line_number": None,
            "zone": None,
            "candidates_considered": 0,
            "all_candidates": [],
            "extraction_method": "enhanced_detection"
        }
    
    # ─────────────────────────────────────────────────────
    # 2. CHECK FIELDS (NEW: Zone-aware extraction)
    # ─────────────────────────────────────────────────────
    # These fields are now searched across ALL pages and ALL zones
    # with priority given to footer zones and L1 labels
    
    for field in ["check_number", "check_date", "check_amount"]:
        field_result = extract_field_by_zone(pages, field)
        field_result["extraction_method"] = "zone_aware_hierarchical"
        result[field] = field_result
    
    # ─────────────────────────────────────────────────────
    # 3. CPT CODES (from all pages - unchanged)
    # ─────────────────────────────────────────────────────
    full_text = "\n\n".join(p.get("text", "") for p in pages)
    result["cpt_codes"] = extract_cpt_codes(full_text)
    
    # ─────────────────────────────────────────────────────
    # 4. METADATA
    # ─────────────────────────────────────────────────────
    result["_meta"] = {
        "total_pages": total_pages,
        "header_pages_searched": header_page_count,
        "extraction_strategy": "zone_aware_hierarchical_v2",
        "improvement_notes": {
            "check_fields": "Now searches ALL pages with zone prioritization (footer > header > body)",
            "label_hierarchy": "Uses 3-level hierarchy (L1 authoritative > L2 strong > L3 fallback)",
            "page_order": "Last page checked first (summary values), then first page, then middle pages",
            "zone_priorities": {
                "footer": "+0.30 confidence",
                "header": "+0.05 confidence",
                "body": "+0.00 confidence"
            }
        },
        "extraction_details": {
            "check_number": "Level 1: EPC Draft #, Check/EFT No | Level 2: Check #, Check No, etc.",
            "check_date": "Level 1: Payment/Check Date, Check Date | Level 2: Payment Date, EFT Date, etc.",
            "check_amount": "Level 1: Payment/Check Amount, Check Amount | Level 2: Net Payment, Amount Paid, etc.",
            "insurance_name": "From first page (unchanged from v1)",
            "practice_name": "From first page (unchanged from v1)",
            "cpt_codes": "From all pages (unchanged from v1)"
        }
    }
    
    return result


# ───────────────────────────────────────────────────────────────────
# INTEGRATION GUIDE
# ───────────────────────────────────────────────────────────────────
#
# To use the improved extraction in your codebase:
#
# OPTION 1: Drop-in replacement (immediate migration)
# ─────────────────────────────────────────────────────
#   In eob_extraction.py, rename:
#   - extract_eob_data_from_pages() → extract_eob_data_from_pages_v1()
#   - extract_eob_data_from_pages_v2() → extract_eob_data_from_pages()
#
#   Result: All downstream code works unchanged (same API)
#
#
# OPTION 2: Gradual migration (A/B testing)
# ─────────────────────────────────────────────────────
#   1. Import both versions:
#      from .eob_extraction import extract_eob_data_from_pages as v1_extract
#      from .eob_extraction_v2 import extract_eob_data_from_pages_v2 as v2_extract
#
#   2. Add feature flag:
#      if config.USE_NEW_EXTRACTION:
#          result = v2_extract(pages)
#      else:
#          result = v1_extract(pages)
#
#   3. Compare results and gradually roll out v2
#
#
# OPTION 3: Hybrid approach (best of both)
# ─────────────────────────────────────────────────────
#   1. Use v2 for check fields (better footer detection)
#   2. Keep v1 for insurance/practice (already working)
#
#      result["insurance_name"] = v1_extract(pages)["insurance_name"]
#      result["practice_name"] = v1_extract(pages)["practice_name"]
#      result["check_number"] = v2_extract(pages)["check_number"]
#      result["check_date"] = v2_extract(pages)["check_date"]
#      result["check_amount"] = v2_extract(pages)["check_amount"]
#      result["cpt_codes"] = v1_extract(pages)["cpt_codes"]


# ───────────────────────────────────────────────────────────────────
# TESTING STRATEGY
# ───────────────────────────────────────────────────────────────────
#
# Test cases to validate improvements:
#
# 1. Footer value detection:
#    - PDF with check amount in header (stale) and footer (current)
#    - Expected: Extract footer value
#    - Old result: Header value (WRONG)
#    - New result: Footer value (CORRECT)
#
# 2. Label priority:
#    - PDF with "Check #" label in header and "EPC Draft #" in footer
#    - Expected: Extract from EPC Draft # (L1)
#    - Old result: Extract from Check # (first found)
#    - New result: Extract from EPC Draft # (CORRECT)
#
# 3. Multi-page handling:
#    - PDF with check info on page 1 and page N
#    - Expected: Both found, footer zone prioritized
#    - Old result: Page 1 only (if in header pages)
#    - New result: All pages checked, best selected (CORRECT)
#
# 4. Zone confidence:
#    - Same value on page 1 header and page 2 footer
#    - Expected: Footer value has higher confidence
#    - Old result: Both same confidence
#    - New result: Footer +0.30 boost (CORRECT)
#
#
# Example test:
#
#   def test_footer_value_extraction():
#       # Create mock PDF with footer value
#       pages = [
#           {
#               "page_number": 1,
#               "text": "...\n\nCheck Amount: $100\n" + "X" * 1000,
#               "method": "pdfplumber"
#           },
#           {
#               "page_number": 1,
#               "text": "Y" * 1000 + "\n\nCheck Amount: $5000",
#               "method": "pdfplumber"
#           }
#       ]
#
#       result = extract_eob_data_from_pages_v2(pages)
#       assert result["check_amount"]["value"] == "$5000"  # Footer value
#       assert result["check_amount"]["zone"] == "footer"
#       assert result["check_amount"]["confidence"] >= 0.7
