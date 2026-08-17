"""Alias dictionaries, value-pattern regexes, and context hint patterns."""
import re
from .cpt_extraction import date_pattern


# 1. FIELD ALIASES


FIELD_ALIASES = {
    "check_number": [
        ("EPC Draft #", 1.00), ("Check/EFT No", 1.00), ("Check / EFT No", 1.00), ("Check EFT No", 1.00),
        ("Check No", 1.00), ("Check #", 1.00), ("Check Number", 1.00), ("CheckNumber", 1.00),
        ("Check No.", 0.98), ("Check No#", 0.98), ("Check Id",0.98), ("Payment Number", 0.95),
        ("EFT Trace Number", 0.98), ("EFTTraceNumber", 0.98), ("EFT Trace", 0.95),
        ("Trace Number", 0.95), ("Trace #", 0.90), ("Trace No", 0.90), ("Trace No.", 0.90),
        ("Reference Number", 0.75), ("Reference No", 0.75), ("Reference #", 0.70),
        ("Reference No.", 0.70), ("Ref No", 0.65), ("Ref #", 0.65), ("EFT#", 0.80),
    ],
    "check_date": [
        ("Payment/Check Date", 1.00), ("Payment / Check Date", 1.00),
        ("Check Date", 1.00), ("CheckDate", 1.00),
        ("Payment Date", 0.98), ("PaymentDate", 0.98),
        ("EFT Date", 0.95), ("EFTDate", 0.95), ("EFT", 0.92),
        ("Date Issued", 0.90), ("DateIssued", 0.90), ("Issued Date", 0.90),
        ("Remit Date", 0.80), ("RemitDate", 0.80), ("Printed", 0.80),
        ("Issue Date", 0.75), ("Issued", 0.70), ("Service Date", 0.60), ("ServiceDate", 0.60),
        ("Date of remittance",0.80), ("Dateofremittance",0.80)
    ],
    "check_amount": [
        ("Payment/Check Amount", 1.00), ("Payment / Check Amount", 1.00),
        ("Check Amount", 1.00), ("CheckAmount", 1.00),
        ("Payment Amount", 0.98), ("PaymentAmount", 0.98),
        ("Net Payment", 0.95), ("NetPayment", 0.95),
        ("Amount Paid", 0.92), ("AmountPaid", 0.92),
        ("Total Paid", 0.85), ("TotalPaid", 0.85), ("Total Payment", 0.85),
        ("Payment", 0.80), ("Paid", 0.70), ("Amount", 0.95), ("Trace Amount",0.70)
    ],
    "practice_name": [
        ("Pay To", 0.90), ("PayTo", 0.90), ("Pay To:", 0.90), ("Payable To", 0.85),
        ("Provider Name", 0.95), ("ProviderName", 0.95), ("Provider:", 0.95),
        ("Billing Provider", 0.90), ("BillingProvider", 0.90), ("Billing Provider:", 0.90),
        ("Rendering Provider", 0.85), ("RenderingProvider", 0.85),
        ("Practice Name", 0.85), ("PracticeName", 0.85), ("Payee", 0.80),
    ],
    "insurance_name": [
        ("Payer Name", 1.00), ("PayerName", 1.00), ("Payer:", 1.00),
        ("Insurance Carrier", 0.95), ("InsuranceCarrier", 0.95), ("Carrier", 0.90),
        # ("Insurance Company", 0.90), ("InsuranceCompany", 0.90),
        ("Plan Name", 0.85), ("PlanName", 0.85),
        ("Payer", 0.80), ("Insurer", 0.80), ("Insurance", 0.75),
    ],
    "cpt_code": [
        ("CPT Code", 1.00), ("CPT", 1.00), ("CPT:", 1.00),
        ("Procedure Code", 0.95), ("Proc Code", 0.95),
        ("Service Code", 0.90), ("HCPCS", 0.90), ("Code", 0.70),
    ],
}

# 2. FIELD-SPECIFIC REGEX

FIELD_PATTERNS = {
    "check_number": re.compile(r"(?<![A-Za-z0-9])([A-Za-z0-9]{4,20}(?:[-/][A-Za-z0-9]{1,10})?)(?![A-Za-z0-9])", re.IGNORECASE),
    "check_date": re.compile(
        r"(?<![A-Za-z0-9])("
        r"(?:0?[1-9]|1[0-2])[/\-.](?:0?[1-9]|[12]\d|3[01])[/\-.](?:19|20)?\d{2}|"  # MM/DD/YY or MM/DD/YYYY
        r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s*\d{1,2},?\s*\d{2,4}|"  # Month DD, YYYY (space optional)
        r"\d{1,2}\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+\d{2,4}|"  # DD Month YYYY
        r"\d{6,8}"  # MMDDYY or MMDDYYYY (without separators)
        r")(?![A-Za-z0-9])",
        re.IGNORECASE
    ),
    "check_amount": re.compile(r"(?<![A-Za-z0-9])[\$]?\s*((-?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d{2})?))(?![A-Za-z0-9])"),
    "practice_name": re.compile(r"(?<![A-Za-z])([A-Z][A-Za-z ,.\-']{2,60}(?:\s+(?:LLC|PLLC|PC|PA|Inc|Corp|LLP|P.C.|P.A.)\b)?)(?![A-Za-z])"),
    "insurance_name": re.compile(r"(?<![A-Za-z])([A-Z][A-Za-z ,.\-']{2,60}(?:\s+(?:Insurance|Company|Corp|Inc|Aetna|Blue|Cross|United|Cigna|Humana)\b)?)(?![A-Za-z])"),
    "cpt_code": re.compile(r"(?<!\d)([0-9]{5})(?!\d)"),
}

# 3. EOB CONTEXT


CHECK_NUMBER_HINTS = re.compile(r"check|eft|trace|reference|payment|remittance|deposit", re.IGNORECASE)
DATE_HINTS = re.compile(r"check|payment|eft|remit|issue|issued|date|service", re.IGNORECASE)
AMOUNT_HINTS = re.compile(r"payment|check|paid|amount|net|total|remittance|balance", re.IGNORECASE)
PRACTICE_HINTS = re.compile(r"provider|practice|billing|rendering|payee|pay\s*to", re.IGNORECASE)
INSURANCE_HINTS = re.compile(r"payer|insurance|carrier|company|plan|insurer", re.IGNORECASE)
CPT_HINTS = re.compile(r"cpt|procedure|service|hcpcs|code|proc", re.IGNORECASE)


# 5. BOILERPLATE BLOCKLIST
#
# Standard EOB/remittance phrases that look like plausible name candidates
# under a loose regex (capitalized, multi-word) but are never actually the
# practice or insurance name. Rejecting these outright removes a large chunk
# of the "matches unnecessary text" false positives.
NAME_BOILERPLATE_BLOCKLIST = re.compile(
    r"^\s*(?:"
    r"explanation of benefits|this is not a bill|remittance advice|"
    r"provider name|payer name|practice name|billing provider|rendering provider|"
    r"page\s*\d+\s*(?:of\s*\d+)?|statement date|date of service|patient name|"
    r"member id|group number|claim number|account number|subscriber|"
    r"summary of payments|payment summary|detail of payment|"
    r"please retain|for your records|customer service|questions.{0,10}call|"
    r"amount billed|amount allowed|amount paid|patient responsibility|"
    r"deductible|copay|coinsurance|adjustment|balance due"
    r")\s*$",
    re.IGNORECASE,
)

# US address pattern: "City, ST 12345" or "City, ST 12345-6789" -- used as a
# positive signal for practice_name, since remittance checks are physically
# mailed to the practice's billing address, so the practice name very often
# sits directly above or below its own address block.
US_ADDRESS_LINE = re.compile(r"[A-Za-z .]+,\s*[A-Z]{2}\s*\d{5}(?:-\d{4})?\b")

# 6. LABEL-FRAGMENT GUARD
#
# A short alias like "Payer" also matches as a substring inside a longer one
# like "Payer Name:" -- when that happens, the "value" the regex grabs right
# next to it is just the REST OF THE LABEL ("Name"), not an actual value.
# This is a generic trap across every field that has both a short and a long
# alias for the same concept, so reject any candidate whose entire value is
# just one of these bare label words.
LABEL_FRAGMENT_WORDS = {
    "name", "no", "no.", "number", "#", "amount", "date", "code", "id",
    "payer", "carrier", "provider", "practice", "insurance", "insurer",
    "plan", "billing", "rendering", "payee", "check", "eft", "trace",
    "reference", "ref", "total", "paid", "issued", "issue",
}

DATE_PATTERN_ENHANCED = re.compile(
    r'(?:\d{1,2}[/\-.]\d{1,2}[/\-.]\d{2,4})|'          # with separators
    r'(?:[A-Za-z]{3,9}\s+\d{1,2},?\s+\d{2,4})|'        # Month DD, YYYY
    r'(?:\d{1,2}\s+[A-Za-z]{3,9}\s+\d{2,4})|'          # DD Month YYYY
    r'(?:\b\d{6}\b)|'                                  # MMDDYY
    r'(?:\b\d{8}\b)',                                  # MMDDYYYY
    re.IGNORECASE
)