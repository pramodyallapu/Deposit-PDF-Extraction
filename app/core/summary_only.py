import re
from .eob_extraction import detect_payor_and_practice_from_header

def extract_summary_from_text_file(text: str) -> list:
    if not text:
        return []

    lines = [
        re.sub(r"\s+", " ", line).strip()
        for line in text.splitlines()
        if line.strip()
    ]

    # Find the summary header
    header_idx = None
    for i, line in enumerate(lines):
        normalized = line.lower()

        if (
            "check#" in normalized
            and "amount" in normalized
            and "# claims" in normalized
            and "npi or tax id" in normalized
            and "payee" in normalized
            and "date" in normalized
        ):
            header_idx = i
            break

    if header_idx is None:
        return []

    records = []

    # Read all summary rows until the next separator
    for line in lines[header_idx + 1:]:
        if re.fullmatch(r"[-= ]+", line):
            if records:
                break
            continue

        match = re.match(
            r"^(?P<check_number>\S+)\s+"
            r"(?P<check_amount>\d[\d,]*\.\d{2})\s+"
            r"(?P<claim_count>\d+)\s+"
            r"(?P<npi_or_tax_id>\d+)\s+"
            r"(?P<payee>.+?)\s+"
            r"(?P<check_date>\d{2}/\d{2}/\d{4})$",
            line
        )

        if match:
            record = match.groupdict()
            records.append(record)

    return records


def _detail_header_index(lines):
    """Line index of the detail-table header: Check# ... Status ... Payer."""
    for i, line in enumerate(lines):
        n = line.lower()
        if "check#" in n and "status" in n and "payer" in n:
            return i
    return None


def _payor_for_check(lines, start, check_number, span=5):
    """First detail row for this check, plus its following lines, as a mini 'header page'."""
    for i in range(start, len(lines)):
        if lines[i].split(" ", 1)[0] == check_number:
            snippet = "\n".join(lines[i:i + span])
            page = {"page_number": 1, "text": snippet, "method": "text"}
            result = detect_payor_and_practice_from_header([page], 1)
            return result["insurance_name"]
    return None


def extract_summary_with_payor(text: str) -> list:
    records = extract_summary_from_text_file(text)      # your original, unchanged
    if not records:
        return []

    lines = [re.sub(r"\s+", " ", l).strip() for l in text.splitlines() if l.strip()]
    start = _detail_header_index(lines)

    for rec in records:
        payor = _payor_for_check(lines, start, rec["check_number"]) if start is not None else None
        rec["insurance_name"] = payor["value"] if payor else ""
        rec["insurance_confidence"] = payor["confidence"] if payor else 0.0
        rec["insurance_source"] = payor["source"] if payor else "not_found"
    return records