"""Alias+scoring based field extraction (check_number, check_date, check_amount, etc.)."""
import re
from .scoring import *  # noqa: F401,F403


# 14. FIELD CANDIDATE ENGINE
CHECK_NUMBER_DOCUMENT_ID_PATTERN = re.compile(
    r"\b(?:DOCU\s*ID|DOCUMENT\s+ID)\b",
    re.IGNORECASE,
)


def find_field_candidates(text, field_name, aliases=None):
    text = normalize_text(text)
    lines = text.splitlines()
    candidates = []
    aliases_matches = find_aliases(lines, field_name, aliases=aliases)   # <- pass through
    for alias_match in aliases_matches:
        alias_line = alias_match["line"]
        nearby = get_nearby_lines(lines, alias_line, alias_match["start"], alias_match["end"])
        for area in nearby:
            source_text = area["text"]
            if not source_text.strip():
                continue
            if (
                field_name == "check_number"
                and CHECK_NUMBER_DOCUMENT_ID_PATTERN.search(source_text)
            ):
                continue
            matches = extract_values_from_text(source_text, field_name)
            for value_match in matches:
                value = value_match.group(1).strip()
                if field_name == "check_number":
                    if (
                        area["direction"] == "below"
                        and area["distance"] == 1
                        and value_match is matches[0]
                    ):
                        prefix_match = re.search(
                            r"(?P<prefix>[A-Za-z0-9]{1,20}-)\s*"
                            r"(?:Date\s*:\s*(?:"
                            r"(?:\d{1,2}[/.-]\d{1,2}[/.-]\d{2,4})|"
                            r"[A-Za-z]{3,9}\s+\d{1,2},?\s+\d{2,4}|"
                            r"\d{1,2}\s+[A-Za-z]{3,9}\s+\d{2,4}"
                            r")?\s*)?$",
                            lines[alias_line],
                            re.IGNORECASE,
                        )
                        if prefix_match:
                            value = f"{prefix_match.group('prefix')}{value}"
                    value = clean_check_number_candidate(value)
                    if not validate_check_number(value):
                        continue
                elif field_name == "check_amount" and not validate_amount(value):
                    continue
                elif field_name == "cpt_code":
                    value = clean_cpt_candidate(value)
                if area["direction"] == "right":
                    distance = value_match.start()
                elif area["direction"] == "left":
                    distance = len(source_text) - value_match.end()
                else:
                    distance = area["distance"]
                score = score_candidate(
                    field_name=field_name, value=value, alias_weight=alias_match["weight"],
                    direction=area["direction"], distance=distance,
                    source_text=lines[alias_line] + " " + source_text,
                    line_number=area["line_number"], alias_line_number=alias_line,
                    candidate_source_text=source_text,
                    candidate_position=value_match.start(),
                    alias_position=alias_match["start"],
                )

                # Position of alias/header relative to source_text.
                alias_position = alias_match["start"]

                # Position of candidate value in source_text.
                value_position = value_match.start()
                candidates.append(Candidate(
                    value=value, score=score, alias_used=alias_match["matched_text"],
                    direction=area["direction"], line_number=area["line_number"],
                    distance=distance, source_line=lines[area["line_number"]],
                    context_score=context_score(field_name, source_text),position=value_position,alias_position=alias_position,
                    has_currency_symbol=(
                        field_name == "check_amount"
                        and bool(re.match(r"\s*\$+", value_match.group(0)))
                    ),
                ))
    return candidates

# 15. DEDUPLICATE CANDIDATES


def deduplicate_candidates(candidates,field_name:None):
    grouped = {}
    for candidate in candidates:
        key = candidate.value.strip().lower()
        if key not in grouped or candidate.score > grouped[key].score:
            grouped[key] = candidate
        existing = grouped[key]
        if (
            field_name == "check_amount"
            and candidate.has_currency_symbol != existing.has_currency_symbol
        ):
            if candidate.has_currency_symbol:
                grouped[key] = candidate
        elif candidate.score > existing.score:
            grouped[key] = candidate

        elif (
            field_name == "check_amount"
            and candidate.score == existing.score
            and column_distance(candidate) < column_distance(existing)
        ):
            grouped[key] = candidate
    return list(grouped.values())

# 16. FINAL FIELD EXTRACTION


def extract_field(text, field_name, threshold=0.20):
    candidates = find_field_candidates(text, field_name)
    if not candidates:
        return {"value": "", "confidence": 0.0, "alias_used": None, "direction": None, "line_number": None, "candidates_considered": 0, "all_candidates": []}
    candidates = deduplicate_candidates(candidates,field_name)
    if field_name == "check_amount":
        candidates.sort(key=lambda x: (x.score, x.position), reverse=True)
    else:
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


def column_distance(candidate):
    return abs(
        candidate.position - candidate.alias_position
    )