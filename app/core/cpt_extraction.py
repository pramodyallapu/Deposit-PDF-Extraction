"""CPT code extraction: requires date+money context or explicit CPT/procedure labeling,
to avoid matching every 5-digit look-alike number (account numbers, ZIP codes, etc.) as a CPT code."""
import re
from collections import Counter


# 17. CPT CODE EXTRACTION (5-DIGIT AND 7-DIGIT CPT CODES)

# Date pattern: MM/DD/YYYY, MM/DD/YY, etc.
date_pattern = re.compile(
    r'(?:\d{1,2}[/\-.]\d{1,2}[/\-.]\d{2,4})|'
    r'(?:\b\d{6}\b)|'
    # r'(?:\b\d{8}\b)|'
    r'(?:[A-Za-z]{3,9}\s+\d{1,2},?\s+\d{2,4})',
    re.IGNORECASE
)

# Money pattern: $123.45, 123.45, 1,234.56, etc.
money_pattern = re.compile(r'\$?\s*[\d,]+\.\d{2}')


def extract_cpt_codes(text):
    """
    Extract CPT codes using enhanced rule:
    - Must be a 5-digit or 7-digit code (may have letters after it like 92507GN)
    - Must appear on a line that has date pattern AND money pattern (same line)
    - OR explicitly labeled with CPT/Procedure context
    - OR date on same line + money within ±1 line (adjacent)
    - Rejects pure alpha strings (must contain at least one digit)
    """
    if not text or not isinstance(text, str):
        return {
            "cpt_codes": [],
            "cpt_count": 0,
            "cpt_total_occurrences": 0,
            "code_frequencies": {},
            "extraction_confidence": 0.0,
            "line_details": []
        }

    lines = text.split('\n')
    total_lines = len(lines)
    cpt_candidates = []

    # Pre‑compute date/money presence for each line
    line_has_date = [False] * total_lines
    line_has_money = [False] * total_lines
    for i, line in enumerate(lines):
        line_has_date[i] = bool(date_pattern.search(line))
        line_has_money[i] = bool(money_pattern.search(line))

    WINDOW = 1  # look at immediate neighbours only (±1 line)

    for line_idx, line in enumerate(lines):
        has_date_current = line_has_date[line_idx]
        has_money_current = line_has_money[line_idx]

        # Adjacent flags (only for metadata)
        has_date_prev = line_has_date[line_idx-1] if line_idx > 0 else False
        has_money_prev = line_has_money[line_idx-1] if line_idx > 0 else False
        has_date_next = line_has_date[line_idx+1] if line_idx < total_lines-1 else False
        has_money_next = line_has_money[line_idx+1] if line_idx < total_lines-1 else False

        # Negative‑context guard: skip lines that are obviously not CPT
        if re.search(r'\b(?:zip|acct|account|member\s*id|group\s*(?:no|number|#)?|npi|tax\s*id|phone|ssn|ein)\b',
                     line, re.IGNORECASE):
            continue

        has_cpt_context = bool(re.search(r'CPT|Procedure|Proc|HCPCS|Code|Service|SVCS|SERVICE', line, re.IGNORECASE))
        has_service_indicator = bool(re.search(r'SVCS|SERVICE|CODE|PROC|CPT', line, re.IGNORECASE))

        # Check for money on adjacent lines (±1)
        has_money_nearby = False
        if has_date_current and not has_money_current:
            # Check the line directly above and below (if they exist)
            if line_idx > 0 and line_has_money[line_idx - 1]:
                has_money_nearby = True
            elif line_idx < total_lines - 1 and line_has_money[line_idx + 1]:
                has_money_nearby = True

        # Determine if the line is valid for CPT extraction
        is_valid_line = False
        source = ""

        if has_cpt_context:
            is_valid_line = True
            source = "cpt_context"
        elif has_date_current and has_money_current:
            is_valid_line = True
            source = "date_money_same_line"
        elif has_date_current and has_money_nearby:
            is_valid_line = True
            source = "date_with_money_adjacent"   # ±1 line

        if is_valid_line:
            # Find 5-digit or 7-digit codes (optionally followed by letters)
            for match in re.finditer(r'\b(\d{5}|\d{7})(?:[A-Za-z]{0,5})?\b', line):
                code = match.group(1)
                full_match = match.group(0)
                modifier = full_match[len(code):] if len(full_match) > len(code) else ""

                code_length = len(code)
                try:
                    code_int = int(code)
                    if code_length == 5:
                        if not (100 <= code_int <= 99999 and code[0] != '0'):
                            continue
                    elif code_length == 7:
                        if not (1000000 <= code_int <= 9999999):
                            continue
                    else:
                        continue
                except ValueError:
                    continue

                cpt_candidates.append({
                    'code': code,
                    'code_length': code_length,
                    'modifier': modifier,
                    'is_numeric': code.isdigit(),
                    'line': line_idx + 1,
                    'line_text': line.strip()[:100],
                    'has_date_current': has_date_current,
                    'has_money_current': has_money_current,
                    'has_date_adjacent': has_date_prev or has_date_next,
                    'has_money_adjacent': has_money_prev or has_money_next,
                    'has_cpt_context': has_cpt_context,
                    'has_service_indicator': has_service_indicator,
                    'source': source
                })

    # Second pass – safety net for lines that have a date but were not caught
    # (Now also uses ±1 line for money check)
    for line_idx, line in enumerate(lines):
        if not line_has_date[line_idx]:
            continue
        if re.search(r'\b(?:zip|acct|account|member\s*id|group\s*(?:no|number|#)?|npi|tax\s*id|phone|ssn|ein)\b',
                     line, re.IGNORECASE):
            continue

        for match in re.finditer(r'\b(\d{5}|\d{7})(?:[A-Za-z]{0,5})?\b', line):
            code = match.group(1)
            if any(c['code'] == code for c in cpt_candidates):
                continue

            full_match = match.group(0)
            modifier = full_match[len(code):] if len(full_match) > len(code) else ""
            code_length = len(code)
            try:
                code_int = int(code)
                if code_length == 5:
                    if not (100 <= code_int <= 99999 and code[0] != '0'):
                        continue
                elif code_length == 7:
                    if not (1000000 <= code_int <= 9999999):
                        continue
                else:
                    continue
            except ValueError:
                continue

            # Check for money on adjacent lines (±1)
            has_money_nearby = False
            if line_idx > 0 and line_has_money[line_idx - 1]:
                has_money_nearby = True
            elif line_idx < total_lines - 1 and line_has_money[line_idx + 1]:
                has_money_nearby = True

            if has_money_nearby:
                cpt_candidates.append({
                    'code': code,
                    'code_length': code_length,
                    'modifier': modifier,
                    'is_numeric': code.isdigit(),
                    'line': line_idx + 1,
                    'line_text': line.strip()[:100],
                    'has_date_current': True,
                    'has_money_current': line_has_money[line_idx],
                    'has_date_adjacent': False,
                    'has_money_adjacent': has_money_nearby and not line_has_money[line_idx],
                    'has_cpt_context': False,
                    'has_service_indicator': bool(re.search(r'SVCS|SERVICE|CODE', line, re.IGNORECASE)),
                    'source': 'date_with_money_adjacent_second_pass'
                })

    # ---- Deduplication and aggregation (unchanged) ----
    unique_codes = []
    code_details = {}
    for candidate in cpt_candidates:
        code = candidate['code']
        if code not in unique_codes:
            unique_codes.append(code)
            code_details[code] = {
                'lines': [candidate['line']],
                'line_text': candidate['line_text'],
                'code_length': candidate.get('code_length', len(code)),
                'modifier': candidate.get('modifier', ''),
                'is_numeric': candidate.get('is_numeric', True),
                'has_date_current': candidate.get('has_date_current', False),
                'has_money_current': candidate.get('has_money_current', False),
                'has_date_adjacent': candidate.get('has_date_adjacent', False),
                'has_money_adjacent': candidate.get('has_money_adjacent', False),
                'has_cpt_context': candidate.get('has_cpt_context', False),
                'has_service_indicator': candidate.get('has_service_indicator', False),
                'source': candidate.get('source', 'unknown')
            }
        else:
            if candidate['line'] not in code_details[code]['lines']:
                code_details[code]['lines'].append(candidate['line'])

    code_counts = Counter()
    for code in unique_codes:
        code_counts[code] = sum(1 for line in lines if code in line)

    confidence = 0.0
    if unique_codes:
        confidence = min(1.0, 0.3 + (len(unique_codes) * 0.15))
        if any(details.get('has_cpt_context', False) for details in code_details.values()):
            confidence = min(1.0, confidence + 0.2)
        if any(details.get('modifier', '') for details in code_details.values()):
            confidence = min(1.0, confidence + 0.1)
        if all(details.get('is_numeric', True) for details in code_details.values()):
            confidence = min(1.0, confidence + 0.1)

    return {
        "cpt_codes": sorted(unique_codes),
        "cpt_count": len(unique_codes),
        "cpt_total_occurrences": sum(code_counts.values()),
        "code_frequencies": dict(code_counts),
        "extraction_confidence": round(confidence, 3),
        "line_details": [
            {
                "code": code,
                "code_length": details.get('code_length', len(code)),
                "modifier": details.get('modifier', ''),
                "is_numeric": details.get('is_numeric', True),
                "lines": details['lines'],
                "sample_line": details['line_text'][:60] + "..." if len(details['line_text']) > 60 else details['line_text'],
                "has_date_current": details['has_date_current'],
                "has_money_current": details['has_money_current'],
                "has_date_adjacent": details['has_date_adjacent'],
                "has_money_adjacent": details['has_money_adjacent'],
                "has_cpt_context": details['has_cpt_context'],
                "has_service_indicator": details['has_service_indicator'],
                "source": details['source']
            }
            for code, details in code_details.items()
        ]
    }