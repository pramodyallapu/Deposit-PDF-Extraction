import re

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