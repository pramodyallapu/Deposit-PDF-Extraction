"""Alias+scoring based field extraction (check_number, check_date, check_amount, etc.)."""
from .scoring import *  # noqa: F401,F403
from typing import List, Dict, Optional
import re
import logging

# Lazy-loaded learning engine
_learning_engine = None


def get_learning_engine():
    """Lazy-load learning engine."""
    global _learning_engine
    if _learning_engine is None:
        from .learning.learner import get_learning_engine as _get_engine
        _learning_engine = _get_engine()
    return _learning_engine


# 14. FIELD CANDIDATE ENGINE

def find_field_candidates(text, field_name):
    text = normalize_text(text)
    lines = text.splitlines()
    candidates = []
    aliases = find_aliases(lines, field_name)
    for alias_match in aliases:
        alias_line = alias_match["line"]
        nearby = get_nearby_lines(lines, alias_line, alias_match["start"], alias_match["end"])
        for area in nearby:
            source_text = area["text"]
            if not source_text.strip(): continue
            matches = extract_values_from_text(source_text, field_name)
            for value_match in matches:
                value = value_match.group(1).strip()
                if field_name == "check_number": value = clean_check_number_candidate(value)
                elif field_name == "cpt_code": value = clean_cpt_candidate(value)
                if area["direction"] == "right": distance = value_match.start()
                elif area["direction"] == "left": distance = len(source_text) - value_match.end()
                else: distance = area["distance"]
                score = score_candidate(
                    field_name=field_name, value=value, alias_weight=alias_match["weight"],
                    direction=area["direction"], distance=distance,
                    source_text=lines[alias_line] + " " + source_text,
                    line_number=area["line_number"], alias_line_number=alias_line
                )
                candidates.append(Candidate(
                    value=value, score=score, alias_used=alias_match["matched_text"],
                    direction=area["direction"], line_number=area["line_number"],
                    distance=distance, source_line=lines[area["line_number"]],
                    context_score=context_score(field_name, source_text)
                ))
    return candidates


# 15. DEDUPLICATE CANDIDATES

def deduplicate_candidates(candidates):
    grouped = {}
    for candidate in candidates:
        key = candidate.value.strip().lower()
        if key not in grouped or candidate.score > grouped[key].score:
            grouped[key] = candidate
    return list(grouped.values())


# 16. FINAL FIELD EXTRACTION

def extract_field(text, field_name, threshold=0.20):
    candidates = find_field_candidates(text, field_name)
    if not candidates:
        return {"value": "", "confidence": 0.0, "alias_used": None, "direction": None, "line_number": None, "candidates_considered": 0, "all_candidates": []}
    candidates = deduplicate_candidates(candidates)
    candidates.sort(key=lambda x: x.score, reverse=True)
    best = candidates[0]
    if best.score < threshold:
        return {"value": "", "confidence": best.score, "alias_used": best.alias_used, "direction": best.direction, "line_number": best.line_number + 1,
                "candidates_considered": len(candidates),
                "all_candidates": [{"value": c.value, "score": round(c.score, 3), "alias": c.alias_used, "direction": c.direction,
                                    "line": c.line_number + 1, "distance": c.distance, "source": c.source_line} for c in candidates]}
    return {"value": best.value, "confidence": round(best.score, 3), "alias_used": best.alias_used, "direction": best.direction, "line_number": best.line_number + 1,
            "candidates_considered": len(candidates),
            "all_candidates": [{"value": c.value, "score": round(c.score, 3), "alias": c.alias_used, "direction": c.direction,
                                "line": c.line_number + 1, "distance": c.distance, "source": c.source_line} for c in candidates]}


# ============================================================
# LEARNING INTEGRATION (SINGLE DEFINITION)
# ============================================================

def get_learned_pattern_candidates(text: str, field_name: str, 
                                   min_confidence: float = 0.3) -> List[Candidate]:
    """
    Get candidates from learned patterns.
    Used as FALLBACK when alias-based extraction fails.
    """
    candidates = []
    
    try:
        engine = get_learning_engine()
        patterns = engine.get_patterns_for_extraction(field_name, min_confidence)
        
        if not patterns:
            return candidates
        
        lines = text.splitlines()
        
        for pattern_info in patterns:
            if 'regex' not in pattern_info:
                continue
            
            try:
                pattern_regex = re.compile(pattern_info['regex'], re.IGNORECASE | re.MULTILINE)
            except re.error:
                continue
            
            for line_idx, line in enumerate(lines):
                matches = pattern_regex.finditer(line)
                for match in matches:
                    if match.lastindex is None:
                        continue
                    value = match.group(1).strip()
                    if not value:
                        continue
                    
                    # Clean based on field
                    if field_name == "check_number":
                        value = clean_check_number_candidate(value)
                    elif field_name == "cpt_code":
                        value = clean_cpt_candidate(value)
                    
                    # Validate
                    if field_name == "check_number" and not validate_check_number(value):
                        continue
                    elif field_name == "check_date" and not validate_date(value):
                        continue
                    elif field_name == "check_amount" and not validate_amount(value):
                        continue
                    
                    # Lower confidence for learned patterns (they're fallback)
                    confidence = pattern_info['confidence'] * 0.7
                    
                    candidates.append(Candidate(
                        value=value,
                        score=confidence,
                        alias_used=f"learned:{pattern_info.get('label_text', 'pattern')[:20]}",
                        direction="right",
                        line_number=line_idx,
                        distance=0,
                        source_line=line,
                        context_score=context_score(field_name, line)
                    ))
    except Exception as e:
        logging.debug(f"Learning engine failed: {e}")
    
    return candidates


def get_free_text_candidates(text: str, field_name: str) -> List[Dict]:
    """Get free-text candidates for insurance/practice names."""
    if field_name not in ['insurance_name', 'practice_name']:
        return []
    
    try:
        engine = get_learning_engine()
        lines = text.splitlines()
        return engine.get_free_text_candidates(field_name, lines)
    except Exception as e:
        logging.debug(f"Free-text learning failed: {e}")
        return []


def learn_from_correction(document_text: str, field_name: str,
                          extracted_value: str, corrected_value: str,
                          confidence: float = 0.0,
                          doc_metadata: Optional[Dict] = None) -> Dict:
    """
    Public API: Learn from ANY user correction.
    
    This is called from the API whenever a user corrects a field.
    """
    try:
        engine = get_learning_engine()
        return engine.learn_from_correction(
            document_text, field_name, extracted_value, corrected_value,
            confidence, doc_metadata
        )
    except Exception as e:
        logging.error(f"Failed to learn from correction: {e}")
        return {'pattern_learned': False, 'error': str(e)}