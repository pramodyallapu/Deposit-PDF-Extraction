import re
from collections import Counter

# 17. CPT CODE EXTRACTION (5-DIGIT AND 7-DIGIT CPT CODES)
# Date pattern: MM/DD/YYYY, MM/DD/YY, etc.
date_pattern = re.compile(
    r'(?:\d{1,2}[-/\\.]\d{1,2}[-/\\.]\d{2,4})|'
    r'(?:\b\d{6}\b)|'
    r'(?:[A-Za-z]{3,9}\s+\d{1,2},?\s+\d{2,4})',
    re.IGNORECASE
)
# Money pattern: $123.45, 123.45, 1,234.56, etc.
money_pattern = re.compile(r'\$?\s*[\d,]+\.\d{2}')

# ---------------------------------------------------------------------------
# Address / ZIP / ID lookalike filtering
# ---------------------------------------------------------------------------
US_STATE_ABBR = {
    'AL','AK','AZ','AR','CA','CO','CT','DE','FL','GA','HI','ID','IL','IN','IA',
    'KS','KY','LA','ME','MD','MA','MI','MN','MS','MO','MT','NE','NV','NH','NJ',
    'NM','NY','NC','ND','OH','OK','OR','PA','RI','SC','SD','TN','TX','UT','VT',
    'VA','WA','WV','WI','WY','DC','PR','VI','GU','AS','MP'
}

# A line that is essentially "<number> <street name> <St/Ave/Rd/...>"
STREET_LINE_PATTERN = re.compile(
    r'^\s*\d{1,6}\s+[A-Za-z0-9.\s]{2,40}\b(?:st|street|ave|avenue|rd|road|dr|drive|'
    r'ln|lane|blvd|boulevard|way|ct|court|pl|place|cir|circle|hwy|highway|'
    r'ste|suite|apt|unit|pkwy|parkway|terrace|ter)\b\.?\s*$', re.I
)

# A line (or line ending) shaped like "City, ST 12345" or "City, ST 12345-6789"
CITY_STATE_ZIP_PATTERN = re.compile(
    r'(?P<pre>[A-Za-z][A-Za-z.\s]{1,30}),?\s+(?P<state>[A-Z]{2})\s+'
    r'(?P<zip>\d{5})(?:-\d{4})?\s*$'
)

# A line that is JUST a bare 5-digit number
BARE_ZIP_LINE_PATTERN = re.compile(r'^\s*\d{5}(?:-\d{4})?\s*$')
PO_BOX_PATTERN = re.compile(r'\bP\.?O\.?\s*Box\b', re.I)

# Only these unambiguously mean "this number is a procedure code" and may
# override the non-CPT-label guard below.
STRONG_CPT_CONTEXT = re.compile(
    r'\bCPT\b|\bHCPCS\b|\bProc(?:edure)?\s*Code\b', re.I
)

# Broad/ambiguous context, used only for confidence scoring - never to unlock a line.
context_pattern = re.compile(r'CPT|Procedure|Proc|HCPCS|Code|Service|SVCS', re.I)

non_cpt_pattern = re.compile(
    r'\b(?:zip|acct|account|member\s*id|group\s*(?:no|number|#)?|'
    r'npi|tax\s*id|phone|ssn|ein|policy\s*(?:no|number|#)?|'
    r'claim\s*(?:no|number|#)?|reference\s*(?:no|number|#)?|'
    r'confirmation\s*(?:no|number|#)?|batch\s*(?:no|number|#)?|'
    r'control\s*(?:no|number|#)?|invoice\s*(?:no|number|#)?|'
    r'check\s*(?:no|number|#)?|fax|po\s*box|pcn|provider\s*id)\b', re.I
)

# ---------------------------------------------------------------------------
# CPT / HCPCS code pattern
# ---------------------------------------------------------------------------
code_pattern = re.compile(
    r'\b([A-Z]\d{4}|\d{4}[A-Z]|\d{5}|\d{7})'
    r'([A-Za-z](?:[A-Za-z0-9]){0,4})?\b',
    re.I
)

# Real CPT/HCPCS modifiers only — NOT a generic "any 2 chars" class.
# Using a loose [A-Z]{2}|\d{2} class causes false positives on split-line
# detection (e.g. a following line starting "04/03/26..." would otherwise
# match "04" + "/03" as if it were a modifier + units pair).
KNOWN_MODIFIERS = (
    r'(?:GN|GO|GP|GT|GY|GZ|XE|XP|XS|XU|KX|CO|CQ|SA|TC|'
    r'HN|HO|HP|HQ|HM|HK|HR|HS|HA|HB|'                 # behavioral health / ABA credential & population modifiers
    r'U1|U2|U3|U4|U5|U6|U7|U8|U9|'                     # state-defined modifiers, common in ABA billing
    r'TF|'
    r'22|24|25|26|50|51|52|53|58|59|76|77|78|79|80|81|82|90|91|95|96|97|99)'
)

# ---------------------------------------------------------------------------
# Table header detection (for bare codes with no modifier, e.g. plain
# "97153" rows under a "DATES CODE SVCS CHARGES AMOUNT..." header)
# ---------------------------------------------------------------------------
TABLE_HEADER_PATTERN = re.compile(
    r'\bCODE\b.*\b(?:SVCS|CHARGES|AMOUNT|PAYABLE)\b|'
    r'\b(?:SVCS|CHARGES|AMOUNT|PAYABLE)\b.*\bCODE\b',
    re.I
)
TABLE_END_PATTERN = re.compile(r'^\s*TOTALS?\b', re.I)


def has_table_header_above(line_idx, lines, window=6):
    """
    Look backward for a column header row that establishes this and
    subsequent lines as a procedure-code table (e.g. 'DATES CODE SVCS
    CHARGES AMOUNT...'). Stops early if a TOTALS row is hit, since that
    marks the end of the previous table's data block.
    """
    start = max(0, line_idx - window)
    for idx in range(line_idx - 1, start - 1, -1):
        if TABLE_END_PATTERN.match(lines[idx]):
            return False
        if TABLE_HEADER_PATTERN.search(lines[idx]):
            return True
    return False


def is_lookalike_candidate(match, line_idx, lines):
    """Reject ZIP, address and non-CPT identifier lookalikes."""
    line = lines[line_idx]
    start, end = match.span(1)
    code = match.group(1)
    if not code.isdigit():
        return False
    if len(code) == 5 and re.match(r'-\d{4}\b', line[end:]):
        return True
    before = line[:start]
    if re.search(
        r'\b(?:zip(?:\s*code)?|acct|account|member\s*id|group\s*(?:no|number|#)?|'
        r'npi|tax\s*id|phone|ssn|ein|policy\s*(?:no|number|#)?|'
        r'claim\s*(?:no|number|#)?|reference\s*(?:no|number|#)?|'
        r'confirmation\s*(?:no|number|#)?|batch\s*(?:no|number|#)?|'
        r'control\s*(?:no|number|#)?|invoice\s*(?:no|number|#)?|'
        r'check\s*(?:no|number|#)?|fax|pcn|provider\s*id)\s*[:#-]?\s*$',
        before, re.I
    ):
        return True
    if PO_BOX_PATTERN.search(before[-25:]):
        return True
    m = CITY_STATE_ZIP_PATTERN.search(line)
    if m and m.group('state').upper() in US_STATE_ABBR:
        zip_start, zip_end = m.span('zip')
        if zip_start <= start and end <= zip_end:
            return True
    loose = re.search(r'(?:,\s*|\s+)([A-Z]{2})\s*$', before)
    if loose and loose.group(1).upper() in US_STATE_ABBR and len(code) == 5:
        return True
    if re.search(
        r'\b(?:city|state|street|road|rd|avenue|ave|boulevard|blvd|drive|dr|'
        r'lane|ln|highway|hwy|address|mailing|location)\b', before, re.I
    ):
        return True
    if STREET_LINE_PATTERN.match(line):
        return True
    if len(code) == 5 and BARE_ZIP_LINE_PATTERN.match(line):
        if line_idx > 0:
            prev = lines[line_idx - 1]
            prev_state = re.search(r'(?:,\s*|\s+)([A-Z]{2})\s*$', prev.rstrip())
            if prev_state and prev_state.group(1).upper() in US_STATE_ABBR:
                return True
            if re.search(r',\s*[A-Za-z\s]+$', prev.rstrip()):
                return True
    return False


def get_direct_modifier(match, line):
    """Return a real modifier appearing immediately after the CPT (attached or '/XX')."""
    if match.group(2):
        return match.group(2).upper()
    after = line[match.end():]
    m = re.match(rf'\s*(?:/\s*)?({KNOWN_MODIFIERS})(?=\s|/|,|$)', after, re.I)
    return m.group(1).upper() if m else ''


def has_split_modifier(match, line_idx, lines):
    """
    Detect CPT on one line and a REAL modifier/units on the following line,
    e.g. code line then 'GT / 7.0' on the next line.

    Guards against false positives where the next line is actually a date
    row (e.g. '04/03/26 12 97153...') which would otherwise satisfy a loose
    "\\d{2}...\\/\\d+" shape.
    """
    if line_idx >= len(lines) - 1:
        return False
    next_line = lines[line_idx + 1].strip()
    if date_pattern.match(next_line):
        return False
    return bool(re.match(
        rf'^{KNOWN_MODIFIERS}(?:\s*,\s*{KNOWN_MODIFIERS})*\s*/\s*\d+(?:\.\d+)?\b',
        next_line, re.I
    ))


def has_procedure_structure(match, line, line_idx=None, lines=None):
    """Detect explicit CPT/HCPCS structure, direct modifier, or split modifier."""
    before = line[:match.start()]
    after = line[match.end():]
    if STRONG_CPT_CONTEXT.search(before):
        return True
    if re.search(r'(?:HC|CPT|HCPCS|PROC(?:EDURE)?|CODE)\s*[:#-]\s*$', before, re.I):
        return True
    if re.match(rf'\s*/\s*(?:{KNOWN_MODIFIERS})?\s*/\s*\d+(?:\.\d+)?', after, re.I):
        return True
    if get_direct_modifier(match, line):
        return True
    if lines is not None and line_idx is not None and has_split_modifier(match, line_idx, lines):
        return True
    return False


def has_same_line_cpt_pair(match, all_matches, line):
    """Validate main/sub CPTs located close together on one service line."""
    if len(all_matches) < 2:
        return False
    for other in all_matches:
        if other is match:
            continue
        left, right = sorted((match, other), key=lambda m: m.start())
        between = line[left.end():right.start()]
        if len(between) > 35:
            continue
        if (
            get_direct_modifier(match, line) or
            get_direct_modifier(other, line) or
            re.search(r'(?:HC|CPT|HCPCS|PROC(?:EDURE)?|CODE)\s*[:#-]?\s*$', line[:match.start()], re.I) or
            re.search(r'(?:HC|CPT|HCPCS|PROC(?:EDURE)?|CODE)\s*[:#-]?\s*$', line[:other.start()], re.I)
        ):
            return True
    return False


def has_nearby_procedure_marker(match, line):
    """Detect CPT markers and slash modifiers near the candidate."""
    before = line[:match.start()]
    after = line[match.end():]
    if re.search(r'(?:HC|CPT|HCPCS|PROC|PROCEDURE)\s*[:#-]?\s*$', before, re.I):
        return True
    if re.match(rf'\s*/\s*{KNOWN_MODIFIERS}\s*/\s*\d+(?:\.\d+)?', after, re.I):
        return True
    if re.search(
        r'\b(?:HC|CPT|HCPCS|PROC|PROCEDURE)\s*[:#-]?\s*',
        line[max(0, match.start() - 15):match.start() + 2], re.I
    ):
        return True
    return False


def has_nearby_modifier(match, line_idx, lines):
    """Allow OCR-wrapped modifiers such as '97530 GO' followed by another line.
    (Used only by Approach 2 - kept exactly as-is.)"""
    after = lines[line_idx][match.end():]
    if re.search(r'(?:/|\s)\s*[A-Z]{1,5}\s*(?:/|,|\b)', after, re.I):
        return True
    for idx in range(line_idx + 1, min(len(lines), line_idx + 3)):
        next_line = lines[idx].strip()
        if re.match(r'^[A-Z]{1,5}(?:\s*[/,-]\s*[A-Z]{1,5})?\s*$', next_line, re.I):
            return True
        if re.search(r'\b(?:GO|GP|GN|XP|XS|XE|XU|GY|GZ|KX|59|25|26|TC)\b', next_line, re.I):
            return True
    return False


def is_numeric_candidate_valid(match, line, all_matches, has_date, has_money, line_idx=0, lines=None):
    code = match.group(1)
    if not code.isdigit():
        return True
    if lines is not None and is_lookalike_candidate(match, line_idx, lines):
        return False

    # Strong CPT structure (context label, direct modifier, or split modifier) always wins.
    if has_procedure_structure(match, line, line_idx, lines):
        return True

    # Two CPTs on one service line (main + sub), close together.
    if has_date and has_money and has_same_line_cpt_pair(match, all_matches, line):
        return True

    if not (has_date and has_money):
        return False

    before = line[:match.start()]
    after = line[match.end():]

    under_table_header = lines is not None and has_table_header_above(line_idx, lines)

    # Reject obvious identifiers even if they are 5 digits — UNLESS this line
    # sits under a confirmed procedure-code table header, in which case the
    # header is stronger evidence than a coincidental label word nearby
    # (e.g. a "Reference" column inside a real CPT table).
    if re.search(
        r'\b(?:line|ctrl|control|claim|reference|ref|account|acct|check|batch|invoice|'
        r'member|policy|provider|patient|trace|document|voucher|sequence|seq|page)\b',
        before[-40:], re.I
    ):
        return under_table_header

    # A date immediately following the number usually means it's a line/control ID —
    # same header-based rescue applies.
    if re.match(r'\s*(?:[-:|])?\s*\d{1,2}[-/.]\d{1,2}[-/.]\d{2,4}\b', after):
        return under_table_header

    # Main condition: valid CPT-shaped code + date + money on the same line
    # is sufficient on its own — no header dependency required, since header
    # wording and table layout vary across 40+ payer formats. The header
    # check above only ever acts as a last-resort override for the two
    # rejection guards, never as a requirement for acceptance.
    return True

def extract_cpt_codes(text):
    """
    Extract CPT codes using two approaches:
    APPROACH 1:
        CPT/Procedure context, direct/split modifier, same-line CPT pair,
        or date + money + table-header evidence.
    APPROACH 2:
        Fallback for table-based multi-line service records (unchanged).
    Multiple CPTs on ONE logical service line count as ONE CPT occurrence.
    """
    if not text or not isinstance(text, str):
        return {
            "cpt_codes": [], "cpt_count": 0, "cpt_total_occurrences": 0,
            "code_frequencies": {}, "extraction_confidence": 0.0, "line_details": []
        }
    lines = text.split('\n')
    cpt_candidates = []

    def add_candidate(match, line_idx, line, source, date=False, money=False,
                      date_adj=False, money_adj=False, context=False,
                      service=False, score=0, evidence=None, record_anchor=None):
        code = match.group(1)
        modifier = match.group(2) or ''
        code_length = len(code)
        is_numeric = code.isdigit()
        if is_numeric:
            try:
                code_int = int(code)
            except ValueError:
                return
            if code_length == 5 and not (100 <= code_int <= 99999 and code[0] != '0'):
                return
            if code_length == 7 and not (1000000 <= code_int <= 9999999):
                return
            if code_length not in (5, 7):
                return
        elif not re.fullmatch(r'(?:[A-Z]\d{4}|\d{4}[A-Z]{1,5})', code, re.I):
            return
        cpt_candidates.append({
            'code': code.upper(), 'code_length': code_length,
            'modifier': modifier.upper(), 'is_numeric': is_numeric,
            'line': line_idx + 1, 'line_idx': line_idx,
            'line_text': line.strip()[:100],
            'has_date_current': date, 'has_money_current': money,
            'has_date_adjacent': date_adj, 'has_money_adjacent': money_adj,
            'has_cpt_context': context, 'has_service_indicator': service,
            'source': source, 'evidence_score': score,
            'evidence': evidence or [],
            'record_anchor': record_anchor
        })

    # ============================================================
    # APPROACH 1
    # ============================================================
    for line_idx, line in enumerate(lines):
        has_date_current = bool(date_pattern.search(line))
        has_money_current = bool(money_pattern.search(line))
        has_date_prev = line_idx > 0 and bool(date_pattern.search(lines[line_idx - 1]))
        has_money_prev = line_idx > 0 and bool(money_pattern.search(lines[line_idx - 1]))
        has_date_next = line_idx < len(lines) - 1 and bool(date_pattern.search(lines[line_idx + 1]))
        has_money_next = line_idx < len(lines) - 1 and bool(money_pattern.search(lines[line_idx + 1]))
        has_cpt_context = bool(context_pattern.search(line))
        has_strong_context = bool(STRONG_CPT_CONTEXT.search(line))
        has_service_indicator = bool(re.search(r'SVCS|SERVICE|CODE|PROC|CPT|HCPCS', line, re.I))
        if non_cpt_pattern.search(line) and not has_strong_context:
            continue

        matches = list(code_pattern.finditer(line))
        if not matches:
            continue

        for match in matches:
            if is_lookalike_candidate(match, line_idx, lines):
                continue
            if not is_numeric_candidate_valid(
                match, line, matches, has_date_current, has_money_current, line_idx, lines
            ):
                continue

            procedure_structure = has_procedure_structure(match, line, line_idx, lines)
            direct_modifier = get_direct_modifier(match, line)
            split_modifier = has_split_modifier(match, line_idx, lines)
            same_line_pair = has_same_line_cpt_pair(match, matches, line)
            table_header = has_table_header_above(line_idx, lines)

            if not (
                has_strong_context or procedure_structure or direct_modifier or
                split_modifier or same_line_pair or
                (has_date_current and has_money_current)
            ):
                continue

            # # Print only lines that are actually eligible for CPT extraction
            evidence = []
            score = 0
            if has_strong_context:
                score += 50
                evidence.append("strong_procedure_context")
            if procedure_structure:
                score += 40
                evidence.append("procedure_structure")
            if direct_modifier:
                score += 20
                evidence.append("direct_modifier")
            elif split_modifier:
                score += 20
                evidence.append("split_modifier")
            if same_line_pair:
                score += 20
                evidence.append("same_line_cpt_pair")
            if table_header:
                score += 15
                evidence.append("table_header")
            if has_date_current:
                score += 10
                evidence.append("date")
            if has_money_current:
                score += 10
                evidence.append("money")

            add_candidate(
                match, line_idx, line,
                "cpt_context" if has_strong_context else "procedure_structure",
                date=has_date_current, money=has_money_current,
                date_adj=has_date_prev or has_date_next,
                money_adj=has_money_prev or has_money_next,
                context=has_cpt_context, service=has_service_indicator,
                score=score, evidence=evidence,
                record_anchor=f"service:{line_idx}"
            )

            if direct_modifier and cpt_candidates:
                cpt_candidates[-1]['modifier'] = direct_modifier

    # ============================================================
    # APPROACH 2
    # FALLBACK FOR TABLE / MULTI-LINE RECORDS (unchanged logic; only the
    # has_procedure_structure call is updated to pass line_idx/lines)
    # ============================================================
    if not cpt_candidates:
        print("CPT: Existing detection found no candidate. Trying logical table-record detection...")
        date_line_indexes = [
            idx for idx, line in enumerate(lines)
            if date_pattern.search(line) and not non_cpt_pattern.search(line)
        ]
        for date_idx in date_line_indexes:
            block_lines = lines[date_idx:min(len(lines), date_idx + 7)]
            block_text = "\n".join(block_lines)
            has_date = bool(date_pattern.search(block_text))
            has_money = bool(money_pattern.search(block_text))
            has_service_context = bool(re.search(
                r'\b(?:service|medical|behavioral|treatment|autism|procedure|'
                r'procedure\s*code|place|home|office|hospital|hcpcs|cpt|therapy|'
                r'visit|surgery|diagnosis)\b', block_text, re.I
            ))
            if not (has_date and has_money):
                continue
            for relative_idx, block_line in enumerate(block_lines):
                abs_line_idx = date_idx + relative_idx
                if STREET_LINE_PATTERN.match(block_line):
                    continue
                for match in code_pattern.finditer(block_line):
                    if non_cpt_pattern.search(block_line):
                        continue
                    if is_lookalike_candidate(match, abs_line_idx, lines):
                        continue
                    code = match.group(1)
                    modifier = match.group(2) or ''
                    nearby_modifier = has_nearby_modifier(match, abs_line_idx, lines)
                    procedure_structure = has_procedure_structure(match, block_line, abs_line_idx, lines)
                    if code.isdigit() and not (
                        procedure_structure or modifier or nearby_modifier or has_service_context
                    ):
                        continue
                    evidence = ["date_in_record", "money_in_record"]
                    evidence_score = 60
                    if has_service_context:
                        evidence_score += 20
                        evidence.append("service_context")
                    if procedure_structure:
                        evidence_score += 25
                        evidence.append("procedure_structure")
                    if modifier:
                        evidence_score += 10
                        evidence.append("modifier")
                    elif nearby_modifier:
                        evidence_score += 10
                        evidence.append("nearby_modifier")
                    if relative_idx == 1:
                        evidence_score += 20
                        evidence.append("immediately_after_date")
                    add_candidate(
                        match, abs_line_idx, block_line, "logical_table_record",
                        date_adj=True, money_adj=True,
                        context=bool(context_pattern.search(block_text)),
                        service=has_service_context, score=evidence_score,
                        evidence=evidence, record_anchor=f"service:{date_idx}"
                    )
        unique_fallback = {}
        for candidate in cpt_candidates:
            key = (candidate['code'], candidate.get('modifier', ''), candidate.get('record_anchor'))
            if key not in unique_fallback or candidate.get('evidence_score', 0) > unique_fallback[key].get('evidence_score', 0):
                unique_fallback[key] = candidate
        cpt_candidates = list(unique_fallback.values())

    # ============================================================
    # BUILD UNIQUE CODES
    # ============================================================
    code_details = {}
    for candidate in cpt_candidates:
        code = candidate['code']
        if code not in code_details:
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
                'source': candidate.get('source', 'unknown'),
                'evidence_score': candidate.get('evidence_score', 0),
                'evidence': candidate.get('evidence', [])
            }
        elif candidate['line'] not in code_details[code]['lines']:
            code_details[code]['lines'].append(candidate['line'])

    unique_codes = list(code_details)

    # ============================================================
    # COUNT OCCURRENCES BY LOGICAL SERVICE LINE
    # ============================================================
    service_records = {}
    for candidate in cpt_candidates:
        anchor = candidate.get('record_anchor') or f"service:{candidate['line_idx']}"
        service_records.setdefault(anchor, set()).add(candidate['code'])
    code_counts = {code: 0 for code in code_details}
    for codes in service_records.values():
        for code in codes:
            code_counts[code] += 1
    # print("CPT RECORDDS : ")
    # for anchor, codes in service_records.items():
    #     print(f"  {anchor}: {', '.join(codes)}")
    cpt_total_occurrences = len(service_records)

    # ============================================================
    # CALCULATE CONFIDENCE
    # ============================================================
    confidence = 0.0
    if unique_codes:
        confidence = min(1.0, 0.3 + len(unique_codes) * 0.15)
        if any(d['has_cpt_context'] for d in code_details.values()):
            confidence = min(1.0, confidence + 0.2)
        if any(d['modifier'] for d in code_details.values()):
            confidence = min(1.0, confidence + 0.1)
        if any(d['source'] == 'logical_table_record' for d in code_details.values()):
            confidence = min(1.0, confidence + 0.2)

    return {
        "cpt_codes": sorted(unique_codes),
        "cpt_count": len(unique_codes),
        "cpt_total_occurrences": cpt_total_occurrences,
        "code_frequencies": code_counts,
        "extraction_confidence": round(confidence, 3),
        "line_details": [
            {
                "code": code,
                "code_length": d['code_length'],
                "modifier": d['modifier'],
                "is_numeric": d['is_numeric'],
                "lines": d['lines'],
                "sample_line": d['line_text'][:60] + ("..." if len(d['line_text']) > 60 else ""),
                "has_date_current": d['has_date_current'],
                "has_money_current": d['has_money_current'],
                "has_date_adjacent": d['has_date_adjacent'],
                "has_money_adjacent": d['has_money_adjacent'],
                "has_cpt_context": d['has_cpt_context'],
                "has_service_indicator": d['has_service_indicator'],
                "source": d['source'],
                "evidence_score": d['evidence_score'],
                "evidence": d['evidence']
            }
            for code, d in code_details.items()
        ]
    }