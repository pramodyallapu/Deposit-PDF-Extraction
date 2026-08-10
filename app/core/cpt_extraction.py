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
    - Must appear on a line that has date pattern AND money pattern
    - OR explicitly labeled with CPT/Procedure context
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

    # Split into lines for analysis
    lines = text.split('\n')

    # Find all CPT candidates with their line context
    cpt_candidates = []

    for line_idx, line in enumerate(lines):
        # Check current line for patterns
        has_date_current = bool(date_pattern.search(line))
        has_money_current = bool(money_pattern.search(line))

        # Check previous line for patterns
        has_date_prev = False
        has_money_prev = False
        if line_idx > 0:
            has_date_prev = bool(date_pattern.search(lines[line_idx - 1]))
            has_money_prev = bool(money_pattern.search(lines[line_idx - 1]))

        # Check next line for patterns
        has_date_next = False
        has_money_next = False
        if line_idx < len(lines) - 1:
            has_date_next = bool(date_pattern.search(lines[line_idx + 1]))
            has_money_next = bool(money_pattern.search(lines[line_idx + 1]))

        # NOTE: date/money are checked on the SAME line only (has_date_current /
        # has_money_current). Using has_date_prev/has_date_next/has_money_prev/
        # has_money_next here used to let a charge line "leak" onto the very
        # next line -- e.g. a ZIP code or account number sitting right below a
        # valid $-amount line would get pulled in as a CPT code, since that
        # line "had money nearby". Same-line-only avoids that false positive.
        has_date = has_date_current
        has_money = has_money_current

        # Check if line has CPT context
        has_cpt_context = bool(re.search(r'CPT|Procedure|Proc|HCPCS|Code|Service|SVCS|SERVICE', line, re.IGNORECASE))

        # Check if line has service-related indicators (common in EOBs)
        has_service_indicator = bool(re.search(r'SVCS|SERVICE|CODE|PROC|CPT', line, re.IGNORECASE))

        # Negative-context guard: never treat a number as a CPT code if it's
        # immediately labeled as some other kind of ID (ZIP, account, member,
        # group, NPI, tax ID, phone, SSN) -- these are the most common
        # look-alike 5-digit false positives in billing/EOB documents.
        has_non_cpt_label = bool(re.search(
            r'\b(?:zip|acct|account|member\s*id|group\s*(?:no|number|#)?|npi|tax\s*id|phone|ssn|ein)\b',
            line, re.IGNORECASE
        ))
        if has_non_cpt_label and not has_cpt_context:
            continue

        # Rule: Must have date AND money on the SAME line, OR explicit CPT context
        is_valid_line = False
        source = ""

        if has_cpt_context:
            is_valid_line = True
            source = "cpt_context"
        elif has_date and has_money:
            is_valid_line = True
            source = "date_money_adjacent"
        # elif has_date and has_service_indicator:
        #     # Lines with dates and service indicators are likely CPT lines
        #     is_valid_line = True
        #     source = "date_service_indicator"

        if is_valid_line:
            # Find 5-digit or 7-digit codes (optionally followed by letters)
            # Pattern: match exactly 5 or 7 digits, optionally followed by up to 5 letters
            for match in re.finditer(r'\b(\d{5}|\d{7})(?:[A-Za-z]{0,5})?\b', line):
                code = match.group(1)

                # Extract the full match to check for letters after the code
                full_match = match.group(0)
                modifier = ""
                if len(full_match) > len(code):
                    modifier = full_match[len(code):]

                # Check if the code is numeric (contains only digits)
                is_numeric = code.isdigit()
                code_length = len(code)

                # Basic validation: code should be in valid range
                try:
                    code_int = int(code)
                    # For 5-digit codes: 100-99999
                    # For 7-digit codes: 1000000-9999999
                    if code_length == 5:
                        if 100 <= code_int <= 99999 and code[0] != '0':
                            cpt_candidates.append({
                                'code': code,
                                'code_length': 5,
                                'modifier': modifier,
                                'is_numeric': is_numeric,
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
                    elif code_length == 7:
                        if 1000000 <= code_int <= 9999999:
                            cpt_candidates.append({
                                'code': code,
                                'code_length': 7,
                                'modifier': modifier,
                                'is_numeric': is_numeric,
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
                except ValueError:
                    continue

    # Also check for CPT codes in lines with dates, even if money is not on same line
    # Some EOBs have money totals on a separate line
    for line_idx, line in enumerate(lines):
        if date_pattern.search(line):
            # Same negative-context guard as above -- a date sharing a line
            # with a ZIP/account/member-ID label shouldn't let that ID get
            # swept up as a CPT code just because money appears nearby.
            if re.search(r'\b(?:zip|acct|account|member\s*id|group\s*(?:no|number|#)?|npi|tax\s*id|phone|ssn|ein)\b',
                         line, re.IGNORECASE):
                continue
            # Find 5-digit or 7-digit codes in this line
            for match in re.finditer(r'\b(\d{5}|\d{7})(?:[A-Za-z]{0,5})?\b', line):
                code = match.group(1)
                full_match = match.group(0)
                modifier = ""
                if len(full_match) > len(code):
                    modifier = full_match[len(code):]

                code_length = len(code)

                try:
                    code_int = int(code)
                    # Check if already added
                    if not any(c['code'] == code for c in cpt_candidates):
                        # Check if there's money on any nearby line (within 2 lines)
                        has_money_nearby = False
                        start_line = max(0, line_idx - 2)
                        end_line = min(len(lines), line_idx + 3)
                        for check_idx in range(start_line, end_line):
                            if money_pattern.search(lines[check_idx]):
                                has_money_nearby = True
                                break

                        if has_money_nearby:
                            if code_length == 5:
                                if 100 <= code_int <= 99999 and code[0] != '0':
                                    cpt_candidates.append({
                                        'code': code,
                                        'code_length': 5,
                                        'modifier': modifier,
                                        'is_numeric': code.isdigit(),
                                        'line': line_idx + 1,
                                        'line_text': line.strip()[:100],
                                        'has_date_current': True,
                                        'has_money_current': bool(money_pattern.search(line)),
                                        'has_date_adjacent': False,
                                        'has_money_adjacent': has_money_nearby and not bool(money_pattern.search(line)),
                                        'has_cpt_context': False,
                                        'has_service_indicator': bool(re.search(r'SVCS|SERVICE|CODE', line, re.IGNORECASE)),
                                        'source': 'date_with_money_nearby'
                                    })
                            elif code_length == 7:
                                if 1000000 <= code_int <= 9999999:
                                    cpt_candidates.append({
                                        'code': code,
                                        'code_length': 7,
                                        'modifier': modifier,
                                        'is_numeric': code.isdigit(),
                                        'line': line_idx + 1,
                                        'line_text': line.strip()[:100],
                                        'has_date_current': True,
                                        'has_money_current': bool(money_pattern.search(line)),
                                        'has_date_adjacent': False,
                                        'has_money_adjacent': has_money_nearby and not bool(money_pattern.search(line)),
                                        'has_cpt_context': False,
                                        'has_service_indicator': bool(re.search(r'SVCS|SERVICE|CODE', line, re.IGNORECASE)),
                                        'source': 'date_with_money_nearby'
                                    })
                except ValueError:
                    continue

    # Extract unique codes
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

    # Count occurrences
    code_counts = Counter()
    for code in unique_codes:
        code_counts[code] = sum(1 for line in lines if code in line)

    # Calculate confidence based on number of codes found and sources
    confidence = 0.0
    if unique_codes:
        confidence = min(1.0, 0.3 + (len(unique_codes) * 0.15))
        # Boost confidence if codes found with CPT context
        if any(details.get('has_cpt_context', False) for details in code_details.values()):
            confidence = min(1.0, confidence + 0.2)
        # Boost confidence if codes have modifiers
        if any(details.get('modifier', '') for details in code_details.values()):
            confidence = min(1.0, confidence + 0.1)
        # Boost confidence if codes are numeric (stronger indicator)
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