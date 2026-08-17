"""Candidate dataclass, normalization, alias/value matching, and confidence scoring."""
import re
from dataclasses import dataclass, field as dc_field
from datetime import datetime

from .patterns import *  # noqa: F401,F403 -- FIELD_ALIASES, FIELD_PATTERNS, *_HINTS, DATE_PATTERN_ENHANCED


# 4. CANDIDATE (unchanged)
@dataclass
class Candidate:
    value: str
    score: float
    alias_used: str
    direction: str
    line_number: int
    distance: int
    source_line: str = ""
    context_score: float = 0.0

# 5. NORMALIZATION (unchanged)
def normalize_text(text: str) -> str:
    if not text: return ""
    replacements = {"\r\n": "\n", "\r": "\n", "\xa0": " ", "—": "-", "–": "-", "−": "-", "：": ":", "／": "/", "＄": "$", "•": " ", "·": " "}
    for old, new in replacements.items(): text = text.replace(old, new)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()

def normalize_for_alias(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", text.lower())

# 6. ALIAS MATCHING (unchanged)
def alias_regex(alias: str):
    words = re.findall(r"[A-Za-z0-9]+", alias)
    if not words: return None
    separator = r"[\s:/#._\-]*"
    pattern = separator.join(map(re.escape, words))
    pattern_without_spaces = r"".join(map(re.escape, words))
    combined_pattern = f"(?:{pattern}|{pattern_without_spaces})"
    return re.compile(r"(?<![A-Za-z0-9])" + combined_pattern + r"(?![A-Za-z0-9])", re.IGNORECASE)

def find_aliases(lines, field_name):
    matches = []
    for line_no, line in enumerate(lines):
        for alias, weight in FIELD_ALIASES[field_name]:
            pattern = alias_regex(alias)
            if not pattern: continue
            for match in pattern.finditer(line):
                matches.append({"line": line_no, "start": match.start(), "end": match.end(), "alias": alias, "weight": weight, "matched_text": match.group(0)})
    unique_matches = []
    seen = set()
    for match in matches:
        key = (match["line"], match["start"], match["alias"])
        if key not in seen:
            seen.add(key)
            unique_matches.append(match)
    return unique_matches

# 7. GET NEARBY TEXT (unchanged)
def get_nearby_lines(lines, alias_line, alias_start, alias_end, max_lines=5):
    result = []
    line = lines[alias_line]
    result.append({"direction": "right", "line_number": alias_line, "text": line[alias_end:], "distance": 0})
    result.append({"direction": "left", "line_number": alias_line, "text": line[:alias_start], "distance": 0})
    for distance in range(1, max_lines + 1):
        idx = alias_line - distance
        if idx >= 0: result.append({"direction": "above", "line_number": idx, "text": lines[idx], "distance": distance})
    for distance in range(1, max_lines + 1):
        idx = alias_line + distance
        if idx < len(lines): result.append({"direction": "below", "line_number": idx, "text": lines[idx], "distance": distance})
    return result

# 8. VALUE EXTRACTION (unchanged)
def extract_values_from_text(text, field_name):
    return list(FIELD_PATTERNS[field_name].finditer(text))

# 9. VALIDATION FUNCTIONS (updated validate_date)
def validate_date(value: str):
    if not isinstance(value, str):
        return False
    value = value.strip()
    if not value:
        return False

    # First try formats with separators (/, -, .)
    cleaned = value.replace("-", "/").replace(".", "/")
    for fmt in ("%m/%d/%Y", "%m/%d/%y", "%m/%d/%Y", "%Y/%m/%d", "%d/%m/%Y"):
        try:
            dt = datetime.strptime(cleaned, fmt)
            if 1990 <= dt.year <= datetime.now().year + 1:
                return True
        except ValueError:
            pass

    # Try 6-digit (MMDDYY) and 8-digit (MMDDYYYY)
    if re.match(r'^\d{6}$', value):
        try:
            dt = datetime.strptime(value, "%m%d%y")
            if 1990 <= dt.year <= datetime.now().year + 1:
                return True
        except ValueError:
            pass
    if re.match(r'^\d{8}$', value):
        try:
            dt = datetime.strptime(value, "%m%d%Y")
            if 1990 <= dt.year <= datetime.now().year + 1:
                return True
        except ValueError:
            pass

    # Try month name formats – case‑insensitive
    # Convert to title case: "JULY" -> "July", "JUL" -> "Jul"
    title_value = value.title()
    # Normalize common no-space month/day forms like "JULY23,2026" -> "July 23 2026"
    title_value = re.sub(r'\b([A-Za-z]{3,9})(\d{1,2}),?\s*(\d{2,4})\b', r'\1 \2 \3', title_value)
    # Remove commas for consistent parsing
    title_value = title_value.replace(',', '')
    month_formats = [
        "%b %d %Y", "%B %d %Y", "%b %d %y", "%B %d %y",
        "%d %b %Y", "%d %B %Y", "%d %b %y", "%d %B %y"
    ]
    for fmt in month_formats:
        try:
            dt = datetime.strptime(title_value, fmt)
            if 1990 <= dt.year <= datetime.now().year + 1:
                return True
        except ValueError:
            continue

    return False

def parse_amount(value: str):
    if not isinstance(value, str): return None
    try: return float(value.replace("$", "").replace(",", "").strip())
    except ValueError: return None

def validate_amount(value: str):
    if not isinstance(value, str): return False
    amount = parse_amount(value)
    if amount is None or amount < 0 or amount > 10_000_000: return False
    if '.' in value and len(value.split('.')[-1]) != 2: return False
    return True

def validate_check_number(value: str):
    if not isinstance(value, str): return False
    value = value.strip()
    if len(value) < 4 or len(value) > 25: return False
    if not re.search(r"\d", value): return False
    bad_words = {"payment", "check", "number", "date", "amount", "provider", "payer", "insurance", "total", "paid", "service", "code", "cpt", "procedure"}
    if value.lower() in bad_words: return False
    digits = sum(c.isdigit() for c in value)
    letters = sum(c.isalpha() for c in value)
    if letters > digits and digits < 3: return False
    return True

def validate_cpt_code(value):
    if not value: return False
    if isinstance(value, list):
        return any(validate_cpt_code(v) for v in value if v)
    if isinstance(value, str):
        try:
            code = int(value.strip())
            return 100 <= code <= 99999
        except (ValueError, TypeError):
            return False
    return False

def validate_practice_name(value: str):
    if not isinstance(value, str): return False
    value = value.strip()
    if len(value) < 3 or len(value) > 80: return False
    if not value[0].isupper(): return False
    return any(c.isalpha() for c in value)

def validate_insurance_name(value: str):
    if not isinstance(value, str): return False
    value = value.strip()
    if len(value) < 3 or len(value) > 80: return False
    if not value[0].isupper(): return False
    return any(c.isalpha() for c in value)

# 10. CONTEXT SCORE (updated for check_date)
def context_score(field_name, text):
    score = 0.0
    if field_name == "check_number":
        if CHECK_NUMBER_HINTS.search(text): score += 0.25
        if re.search(r'\b(?:#|no|number)\b', text, re.IGNORECASE): score += 0.10
    elif field_name == "check_date":
        if DATE_HINTS.search(text): score += 0.25
        # Use enhanced date pattern to detect any date-like string
        if DATE_PATTERN_ENHANCED.search(text):
            score += 0.15
    elif field_name == "check_amount":
        if AMOUNT_HINTS.search(text): score += 0.25
        if re.search(r'[\$]', text): score += 0.40
    elif field_name == "practice_name":
        if PRACTICE_HINTS.search(text): score += 0.25
        if re.search(r'\b(?:LLC|PLLC|PC|PA|Inc|Corp)\b', text, re.IGNORECASE): score += 0.10
    elif field_name == "insurance_name":
        if INSURANCE_HINTS.search(text): score += 0.25
        if re.search(r'\b(?:Insurance|Company|Corp|Inc)\b', text, re.IGNORECASE): score += 0.10
    elif field_name == "cpt_code":
        if CPT_HINTS.search(text): score += 0.30
        if len(re.findall(r'\b[0-9]{5}\b', text)) >= 2: score += 0.15
    return score

# 11. DIRECTION SCORE (unchanged)
DIRECTION_SCORES = {"right": 1.00, "left": 0.75, "below": 0.90, "above": 0.80}

# 12. CANDIDATE SCORING (unchanged)
def score_candidate(field_name, value, alias_weight, direction, distance, source_text, line_number, alias_line_number):
    score = 0.0
    score += alias_weight * 0.35
    score += DIRECTION_SCORES.get(direction, 0.50) * 0.35
    distance_score = max(0.0, 1.0 - (distance / 80.0)) if direction in ("right", "left") else max(0.0, 1.0 - (distance / 5.0))
    score += distance_score * 0.15
    score += context_score(field_name, source_text) * 0.15
    if field_name == "check_number":
        score += 0.15 if validate_check_number(value) else -0.30
    elif field_name == "check_date":
        score += 0.20 if validate_date(value) else -0.40
    elif field_name == "check_amount":
        score += 0.20 if validate_amount(value) else -0.40
        if re.search(r'[\$]', value): score += 0.20
    elif field_name == "practice_name":
        score += 0.15 if validate_practice_name(value) else -0.25
    elif field_name == "insurance_name":
        score += 0.15 if validate_insurance_name(value) else -0.25
    elif field_name == "cpt_code":
        score += 0.20 if validate_cpt_code(value) else -0.30
    if direction in ["right", "left"]: score += 0.05
    if abs(line_number - alias_line_number) <= 1: score += 0.05
    return max(0.0, min(1.0, score))

# 13. CLEAN FUNCTIONS (unchanged)
def clean_check_number_candidate(value):
    if not isinstance(value, str): return ""
    return re.sub(r"^[^\w]+|[^\w]+$", "", value.strip())

def clean_cpt_candidate(value):
    if not isinstance(value, str): return ""
    match = re.search(r'\b(\d{5})\b', value.strip())
    return match.group(1) if match else value

def normalize_date_to_ddmmyyyy(value: str) -> str:
    if not isinstance(value, str):
        return value
    value = value.strip()
    if not value:
        return value
 
    def _in_range(dt):
        return 1990 <= dt.year <= datetime.now().year + 1
 
    # Numeric with separators: MM/DD/YYYY, MM-DD-YYYY, MM.DD.YYYY, MM/DD/YY, and
    # already-dd/mm/yyyy style YYYY/MM/DD.
    cleaned = value.replace("-", "/").replace(".", "/")
    for fmt in ("%m/%d/%Y", "%m/%d/%y", "%Y/%m/%d", "%d/%m/%Y"):
        try:
            dt = datetime.strptime(cleaned, fmt)
            if _in_range(dt):
                return dt.strftime("%d/%m/%Y")
        except ValueError:
            pass
 
    # 6-digit MMDDYY / 8-digit MMDDYYYY (no separators)
    if re.match(r'^\d{6}$', value):
        try:
            dt = datetime.strptime(value, "%m%d%y")
            if _in_range(dt):
                return dt.strftime("%d/%m/%Y")
        except ValueError:
            pass
    if re.match(r'^\d{8}$', value):
        try:
            dt = datetime.strptime(value, "%m%d%Y")
            if _in_range(dt):
                return dt.strftime("%d/%m/%Y")
        except ValueError:
            pass
 
    # Month-name formats: "July 23, 2026", "Jul 23 2026", "23 July 2026", etc.
    title_value = value.title()
    title_value = re.sub(r'\b([A-Za-z]{3,9})(\d{1,2}),?\s*(\d{2,4})\b', r'\1 \2 \3', title_value)
    title_value = title_value.replace(',', '')
    month_formats = (
        "%b %d %Y", "%B %d %Y", "%b %d %y", "%B %d %y",
        "%d %b %Y", "%d %B %Y", "%d %b %y", "%d %B %y",
    )
    for fmt in month_formats:
        try:
            dt = datetime.strptime(title_value, fmt)
            if _in_range(dt):
                return dt.strftime("%d/%m/%Y")
        except ValueError:
            continue
 
    # Unrecognized format -- leave value untouched rather than guessing/corrupting it.
    return value