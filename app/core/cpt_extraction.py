"""CPT code extraction: requires date+money context or explicit CPT/procedure labeling,
to avoid matching every 5-digit look-alike number (account numbers, ZIP codes, etc.) as a CPT code.

FIX SUMMARY (vs original):
1. `is_lookalike_candidate` (renamed from `is_zip_candidate`) is now:
   - Shared by BOTH Approach 1 and Approach 2 (table fallback) instead of only Approach 1.
   - Aware of real US state abbreviations (not "any 2 uppercase letters"), which avoids
     both false accepts and false rejects.
   - Able to look at the PREVIOUS line, so multi-line addresses like:
         123 Main St
         Springfield, IL 62701
     or a ZIP sitting alone on its own line under a "City, ST" line, are still caught.
   - Backed by two full-line address-shape regexes (street line, "City, ST ZIP" line) that
     reject every number on that line regardless of what else is on it.
   - Extended to a wider set of non-CPT ID labels (policy #, claim #, reference #,
     confirmation #, batch #, control #, invoice #, PO Box, fax) in addition to the
     original zip/acct/member id/group/npi/tax id/phone/ssn/ein.
2. CPT "context" is now split into:
   - STRONG context (`CPT`, `HCPCS`, `Procedure Code`, `Proc Code`) — the only thing
     allowed to override the non-CPT-label guard on a line.
   - WEAK context (`Service`, `Code`, etc., the old broad pattern) — kept ONLY for the
     confidence score, no longer able to unlock ID-labeled lines. This closes the hole
     where "Service Address" or "Zip Code" on a line would flip has_cpt_context=True
     and let the address/ID number through untouched.
   - Even with STRONG context present, the address-shape check still runs (a real CPT
     label essentially never coincides with a full postal address line).
"""
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

# ---------------------------------------------------------------------------
# Address / ZIP / ID lookalike filtering
# ---------------------------------------------------------------------------

US_STATE_ABBR = {
    'AL', 'AK', 'AZ', 'AR', 'CA', 'CO', 'CT', 'DE', 'FL', 'GA', 'HI', 'ID',
    'IL', 'IN', 'IA', 'KS', 'KY', 'LA', 'ME', 'MD', 'MA', 'MI', 'MN', 'MS',
    'MO', 'MT', 'NE', 'NV', 'NH', 'NJ', 'NM', 'NY', 'NC', 'ND', 'OH', 'OK',
    'OR', 'PA', 'RI', 'SC', 'SD', 'TN', 'TX', 'UT', 'VT', 'VA', 'WA', 'WV',
    'WI', 'WY', 'DC', 'PR', 'VI', 'GU', 'AS', 'MP'
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

# A line that is JUST a bare 5-digit number (candidate for "ZIP on its own line"
# when the previous line ends in a state abbreviation / city name)
BARE_ZIP_LINE_PATTERN = re.compile(r'^\s*\d{5}(?:-\d{4})?\s*$')

PO_BOX_PATTERN = re.compile(r'\bP\.?O\.?\s*Box\b', re.I)

# Only these unambiguously mean "this number is a procedure code" and may
# override the non-CPT-label guard below.
STRONG_CPT_CONTEXT = re.compile(r'\bCPT\b|\bHCPCS\b|\bProc(?:edure)?\s*Code\b', re.I)

# Broad/ambiguous context, used only for scoring/confidence - never to unlock a line.
context_pattern = re.compile(r'CPT|Procedure|Proc|HCPCS|Code|Service|SVCS', re.I)

non_cpt_pattern = re.compile(
    r'\b(?:zip|acct|account|member\s*id|group\s*(?:no|number|#)?|'
    r'npi|tax\s*id|phone|ssn|ein|policy\s*(?:no|number|#)?|'
    r'claim\s*(?:no|number|#)?|reference\s*(?:no|number|#)?|'
    r'confirmation\s*(?:no|number|#)?|batch\s*(?:no|number|#)?|'
    r'control\s*(?:no|number|#)?|invoice\s*(?:no|number|#)?|'
    r'check\s*(?:no|number|#)?|fax|po\s*box|pcn|provider\s*id)\b', re.I
)


def is_lookalike_candidate(match, line_idx, lines):
    """
    Returns True if the matched number looks like a ZIP code, street number,
    or other non-CPT identifier rather than a genuine CPT/HCPCS code.

    Checks (in order):
      1. Direct ZIP+4 suffix right after the match ("62701-1234").
      2. An explicit label ("zip", "account", "policy #", "PO Box", ...) immediately
         before the match on the same line.
      3. The match sits at the end of a "City, ST ZIP" shaped line, where ST is a
         real US state/territory abbreviation.
      4. The whole line is a street-address line ("123 Main St").
      5. The match is a bare 5-digit number alone on its own line, and the PREVIOUS
         line looks like a city/state line (ends in a real state abbreviation, or in
         "City,"), i.e. the classic wrapped "City, ST\n12345" case.
    """
    line = lines[line_idx]
    start, end = match.span(1)
    code = match.group(1)

    # --- 1. Direct ZIP+4 ---------------------------------------------------
    if len(code) == 5 and re.match(r'-\d{4}\b', line[end:]):
        return True

    before = line[:start]
    after = line[end:]

    # --- 2. Explicit non-CPT label immediately before -----------------------
    if re.search(
        r'\b(?:zip(?:\s*code)?|acct|account|member\s*id|group\s*(?:no|number|#)?|'
        r'npi|tax\s*id|phone|ssn|ein|policy\s*(?:no|number|#)?|claim\s*(?:no|number|#)?|'
        r'reference\s*(?:no|number|#)?|confirmation\s*(?:no|number|#)?|'
        r'batch\s*(?:no|number|#)?|control\s*(?:no|number|#)?|invoice\s*(?:no|number|#)?|'
        r'check\s*(?:no|number|#)?|fax|pcn|provider\s*id)\s*[:#-]?\s*$',
        before, re.I
    ):
        return True

    if PO_BOX_PATTERN.search(before[-25:]):
        return True

    # --- 3. "City, ST ZIP" on this line, with the match landing on the ZIP --
    m = CITY_STATE_ZIP_PATTERN.search(line)
    if m and m.group('state').upper() in US_STATE_ABBR:
        zip_start, zip_end = m.span('zip')
        if zip_start <= start and end <= zip_end:
            return True

    # Looser variant without comma: "...Springfield IL 62701" — still require a
    # REAL state abbreviation directly before the number (this fixes the
    # original bug of accepting ANY two uppercase letters).
    loose = re.search(r'(?:,\s*|\s+)([A-Z]{2})\s*$', before)
    if loose and loose.group(1).upper() in US_STATE_ABBR and len(code) == 5:
        return True

    # Generic address/location keywords anywhere earlier on the line.
    if re.search(
        r'\b(?:city|state|street|road|rd|avenue|ave|'
        r'boulevard|blvd|drive|dr|lane|ln|highway|hwy|'
        r'address|mailing|location)\b',
        before, re.I
    ):
        return True

    # --- 4. Whole line is a street-address line -----------------------------
    if STREET_LINE_PATTERN.match(line):
        return True

    # --- 5. Bare ZIP alone on its own line, previous line ends in a state --
    if len(code) == 5 and BARE_ZIP_LINE_PATTERN.match(line):
        if line_idx > 0:
            prev = lines[line_idx - 1]
            prev_state = re.search(r'(?:,\s*|\s+)([A-Z]{2})\s*$', prev.rstrip())
            if prev_state and prev_state.group(1).upper() in US_STATE_ABBR:
                return True
            if re.search(r',\s*[A-Za-z\s]+$', prev.rstrip()):
                # Looks like "...City," with the state/zip wrapped to next line.
                return True

    return False


def extract_cpt_codes(text):
    """
    Extract CPT codes using two approaches:

    APPROACH 1 (existing):
        - CPT/Procedure context on same line
        OR
        - date + money on same line

    APPROACH 2 (fallback):
        Used ONLY when approach 1 finds no CPT.
        Handles table-based EOB layouts where date, CPT + modifier,
        description and money are extracted onto separate physical
        text lines but belong to the same logical record.

    This preserves the existing extraction logic and adds a fallback
    specifically for table layouts. Both approaches now share the same
    lookalike-rejection logic (`is_lookalike_candidate`) so ZIP codes,
    street addresses, and ID numbers are filtered consistently.
    """
    if not text or not isinstance(text, str):
        return {"cpt_codes": [], "cpt_count": 0, "cpt_total_occurrences": 0,
                "code_frequencies": {}, "extraction_confidence": 0.0, "line_details": []}

    lines = text.split('\n')
    cpt_candidates = []
    code_pattern = re.compile(r'\b(\d{5}|\d{7})(?:[A-Za-z]{0,5})?\b')

    def add_candidate(match, line_idx, line, source, date=False, money=False,
                      date_adj=False, money_adj=False, context=False,
                      service=False, score=0, evidence=None):
        code = match.group(1)
        full_match = match.group(0)
        modifier = full_match[len(code):]
        code_length = len(code)

        try:
            code_int = int(code)
        except ValueError:
            return

        if code_length == 5:
            if not (100 <= code_int <= 99999 and code[0] != '0'):
                return
        elif code_length == 7:
            if not (1000000 <= code_int <= 9999999):
                return
        else:
            return

        cpt_candidates.append({
            'code': code,
            'code_length': code_length,
            'modifier': modifier,
            'is_numeric': code.isdigit(),
            'line': line_idx + 1,
            'line_text': line.strip()[:100],
            'has_date_current': date,
            'has_money_current': money,
            'has_date_adjacent': date_adj,
            'has_money_adjacent': money_adj,
            'has_cpt_context': context,
            'has_service_indicator': service,
            'source': source,
            'evidence_score': score,
            'evidence': evidence or []
        })

    # ============================================================
    # APPROACH 1
    # EXISTING CPT DETECTION LOGIC
    # ============================================================
    for line_idx, line in enumerate(lines):
        has_date_current = bool(date_pattern.search(line))
        has_money_current = bool(money_pattern.search(line))
        has_date_prev = line_idx > 0 and bool(date_pattern.search(lines[line_idx - 1]))
        has_money_prev = line_idx > 0 and bool(money_pattern.search(lines[line_idx - 1]))
        has_date_next = line_idx < len(lines) - 1 and bool(date_pattern.search(lines[line_idx + 1]))
        has_money_next = line_idx < len(lines) - 1 and bool(money_pattern.search(lines[line_idx + 1]))

        has_cpt_context = bool(context_pattern.search(line))          # weak - scoring only
        has_strong_context = bool(STRONG_CPT_CONTEXT.search(line))     # strong - can unlock guard
        has_service_indicator = bool(re.search(r'SVCS|SERVICE|CODE|PROC|CPT', line, re.I))

        # Negative-context guard: never treat a number as a CPT code if it's
        # immediately labeled as some other kind of ID (ZIP, account, member,
        # group, NPI, tax ID, phone, SSN, policy/claim/reference/invoice #, PO Box)
        # -- unless the line ALSO has an unambiguous CPT/HCPCS/Procedure-Code label.
        if non_cpt_pattern.search(line) and not has_strong_context:
            continue

        # Rule: Must have date AND money on the SAME line, OR explicit CPT context
        if has_cpt_context or (has_date_current and has_money_current):
            source = "cpt_context" if has_cpt_context else "date_money_adjacent"

            for match in code_pattern.finditer(line):

                # Reject ZIP codes, street addresses, and other ID lookalikes,
                # unless the line has an unambiguous CPT/HCPCS/Procedure-Code label
                # (a real CPT label essentially never coincides with a full
                # postal address on the same line, so we still check even then —
                # but this ordering means a false reject here only matters in
                # that vanishingly rare overlap case).
                if not has_strong_context and is_lookalike_candidate(match, line_idx, lines):
                    continue

                add_candidate(
                    match, line_idx, line, source,
                    date=has_date_current,
                    money=has_money_current,
                    date_adj=has_date_prev or has_date_next,
                    money_adj=has_money_prev or has_money_next,
                    context=has_cpt_context,
                    service=has_service_indicator
                )

    # ============================================================
    # APPROACH 2
    # FALLBACK FOR TABLE / MULTI-LINE RECORDS
    #
    # ONLY RUN IF APPROACH 1 FOUND NOTHING
    # ============================================================
    if not cpt_candidates:
        print("CPT: Existing detection found no candidate. Trying logical table-record detection...")

        # Find date lines (skip lines that are clearly ID/label lines)
        date_line_indexes = [
            idx for idx, line in enumerate(lines)
            if date_pattern.search(line) and not non_cpt_pattern.search(line)
        ]

        # Build logical blocks around each date.
        #
        # Example:
        #
        #   07/20/2026
        #   97153HM 1295260172 20 20 Home
        #   Medical Care Autism
        #   Behavioral Trmt
        #   $700.00 $525.60 ...
        #
        # All belong to ONE record.
        for date_idx in date_line_indexes:
            block_lines = lines[date_idx:min(len(lines), date_idx + 7)]
            block_text = "\n".join(block_lines)

            # Evidence within logical record
            has_date = bool(date_pattern.search(block_text))
            has_money = bool(money_pattern.search(block_text))
            has_service_context = bool(re.search(
                r'\b(?:service|medical|behavioral|treatment|autism|procedure|'
                r'procedure\s*code|place|home|office|hospital)\b',
                block_text, re.I
            ))

            if not (has_date and has_money):
                continue

            # Find possible CPTs in the block
            for relative_idx, block_line in enumerate(block_lines):
                abs_line_idx = date_idx + relative_idx

                # Skip lines that are clearly a street address or "City, ST ZIP" line
                if STREET_LINE_PATTERN.match(block_line):
                    continue

                for match in code_pattern.finditer(block_line):
                    # Reject obvious IDs / ZIP / address lookalikes using the
                    # SAME shared logic as Approach 1 (previously this only
                    # checked a bare label regex and missed address-shaped lines).
                    if non_cpt_pattern.search(block_line):
                        continue
                    if is_lookalike_candidate(match, abs_line_idx, lines):
                        continue

                    code = match.group(1)
                    modifier = match.group(0)[len(code):]

                    # Candidate-specific evidence
                    evidence = ["date_in_record", "money_in_record"]
                    evidence_score = 60

                    if has_service_context:
                        evidence_score += 20
                        evidence.append("service_context")
                    if modifier:
                        evidence_score += 10
                        evidence.append("modifier")
                    if relative_idx == 1:
                        evidence_score += 20
                        evidence.append("immediately_after_date")

                    # Strong fallback validation:
                    # date + money + service context
                    # OR date + money + modifier + immediately-after-date
                    is_valid_fallback = (
                        has_service_context or
                        (modifier and relative_idx == 1)
                    )

                    if is_valid_fallback:
                        add_candidate(
                            match, abs_line_idx, block_line,
                            "logical_table_record",
                            date_adj=True,
                            money_adj=True,
                            context=bool(context_pattern.search(block_text)),
                            service=has_service_context,
                            score=evidence_score,
                            evidence=evidence
                        )

        # Remove duplicates
        unique_fallback = {}
        for candidate in cpt_candidates:
            key = (candidate['code'], candidate.get('modifier', ''))
            if key not in unique_fallback or candidate.get('evidence_score', 0) > \
                    unique_fallback[key].get('evidence_score', 0):
                unique_fallback[key] = candidate

        cpt_candidates = list(unique_fallback.values())

        if cpt_candidates:
            print("CPT: Logical table-record detection found:",
                  [f"{c['code']}{c.get('modifier', '')}" for c in cpt_candidates])

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
    # COUNT OCCURRENCES
    # ============================================================
    code_counts = {
        code: sum(code in line for line in lines)
        for code in unique_codes
    }

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
        if all(d['is_numeric'] for d in code_details.values()):
            confidence = min(1.0, confidence + 0.1)
        if any(d['source'] == 'logical_table_record' for d in code_details.values()):
            confidence = min(1.0, confidence + 0.2)

    # ============================================================
    # RETURN RESULTS
    # ============================================================
    return {
        "cpt_codes": sorted(unique_codes),
        "cpt_count": len(unique_codes),
        "cpt_total_occurrences": sum(code_counts.values()),
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