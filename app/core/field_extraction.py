"""Alias+scoring based field extraction (check_number, check_date, check_amount, etc.)."""
from .scoring import *  # noqa: F401,F403


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