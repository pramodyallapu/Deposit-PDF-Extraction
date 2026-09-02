"""Hierarchical label definitions for field extraction.

Three-level label hierarchy ensures correct extraction when multiple labels exist:
- Level 1: Authoritative labels (use these first)
- Level 2: Strong labels (use if L1 absent)
- Level 3: Fallback labels (use only if L1, L2 absent)

Example: For check_number, "EPC Draft #" (L1) should be preferred over "Check #" (L2).
"""


FIELD_LABEL_HIERARCHY = {
    # ─────────────────────────────────────────────────────
    # CHECK NUMBER
    # ─────────────────────────────────────────────────────
    "check_number": {
        "level_1": [
            # Most authoritative - use these
            ("EPC Draft #", 1.00),
            ("Check/EFT No", 1.00),
            ("Check / EFT No", 1.00),
            ("Check EFT No", 1.00),
            ("EFT#", 0.80),
            ("Trace Number", 0.60),
        ],
        "level_2": [
            # Strong secondary options
            ("Check No", 0.98),
            ("Check No.", 0.98),
            ("Check Number", 0.98),
            ("CheckNumber", 0.98),
            ("Check #", 0.98),
            ("Check Id", 0.98),
            ("EFT Trace Number", 0.98),
            ("EFTTraceNumber", 0.98),
            ("Payment Number", 0.95),
        ],
        "level_3": [
            # Fallback options
            ("EFT Trace", 0.95),
            ("Trace Number", 0.95),
            ("Trace #", 0.90),
            ("Trace No", 0.90),
            ("Trace No.", 0.90),
            ("Reference Number", 0.75),
            ("Reference No", 0.75),
            ("Reference #", 0.70),
            ("Reference No.", 0.70),
            ("Ref No", 0.65),
            ("Ref #", 0.65),
            ("EFT", 0.65),
            ("EFT#", 0.70),
            ("PAYDOLLARSCENTS", 0.70),
        ],
        "rationale": "EPC Draft # is most specific (EFT/check system specific). Use it first."
    },

    # ─────────────────────────────────────────────────────
    # CHECK DATE
    # ─────────────────────────────────────────────────────
    "check_date": {
        "level_1": [
            # Most authoritative
            ("Payment/Check Date", 1.00),
            ("Payment / Check Date", 1.00),
            ("Check Date", 1.00),
            ("CheckDate", 1.00),
        ],
        "level_2": [
            # Strong secondary
            ("Payment Date", 0.98),
            ("PaymentDate", 0.98),
            ("EFT Date", 0.95),
            ("EFTDate", 0.95),
            ("Checkwrite Date", 0.75),
            ("Date Issued", 0.90),
            ("DateIssued", 0.90),
            ("Issued Date", 0.90),
        ],
        "level_3": [
            # Fallback
            ("Remit Date", 0.80),
            ("RemitDate", 0.80),
            ("Date of remittance", 0.80),
            ("Dateofremittance", 0.80),
            ("Printed", 0.80),
            ("Issue Date", 0.75),
            ("Issued", 0.70),
            ("Service Date", 0.60),
            ("ServiceDate", 0.60),
            ("Date", 0.50)
        ],
        "rationale": "Payment/Check Date is most specific. Avoid Service Date (may be different)."
    },

    # ─────────────────────────────────────────────────────
    # CHECK AMOUNT
    # ─────────────────────────────────────────────────────
    "check_amount": {
        "level_1": [
            # Most authoritative
            ("Payment/Check Amount", 1.00),
            ("Payment / Check Amount", 1.00),
            ("Check Amount", 1.00),
            ("CheckAmount", 1.00),
            ("Net Payment Amount", 1.00),
            ("Amount Paid", 1.00),
            ("PAYDOLLARSCENTS", 0.80),
            ("PAY DOLLARS CENTS", 0.80),
            ("Card Value", 0.40),
            ("Trace Amount", 0.60)
        ],
        "level_2": [
            # Strong secondary
            ("Payment Amount", 0.98),
            ("PaymentAmount", 0.98),
            ("Net Payment", 0.95),
            ("NetPayment", 0.95),
            ("Amount Paid", 0.92),
            ("AmountPaid", 0.92),
            ("Amount", 0.85),
        ],
        "level_3": [
            # Fallback (generic/ambiguous)
            ("Total Paid", 0.85),
            ("TotalPaid", 0.85),
            ("Total Payment", 0.85),
            ("Payment", 0.80),
            ("Paid", 0.70),
            ("Trace Amount", 0.70),
            ("Net Claim Payment Amount", 0.70),
        ],
        "rationale": "Check Amount is unambiguous. Avoid generic 'Amount' (could be service amount)."
    },

    # ─────────────────────────────────────────────────────
    # PRACTICE NAME
    # ─────────────────────────────────────────────────────
    "practice_name": {
        "level_1": [
            # Most authoritative
            ("Provider Name", 0.95),
            ("ProviderName", 0.95),
            ("Provider:", 0.95),
            ("Billing Provider", 0.90),
            ("BillingProvider", 0.90),
            ("Billing Provider:", 0.90),
        ],
        "level_2": [
            # Strong secondary
            ("Pay To", 0.90),
            ("PayTo", 0.90),
            ("Pay To:", 0.90),
            ("Payable To", 0.85),
            ("Rendering Provider", 0.85),
            ("RenderingProvider", 0.85),
            ("Practice Name", 0.85),
            ("PracticeName", 0.85),
        ],
        "level_3": [
            # Fallback
            ("Payee", 0.80),
        ],
        "rationale": "Provider Name is most specific. 'Pay To' is reliable for checks."
    },

    # ─────────────────────────────────────────────────────
    # INSURANCE NAME
    # ─────────────────────────────────────────────────────
    "insurance_name": {
        "level_1": [
            # Most authoritative
            ("Payer Name", 1.00),
            ("PayerName", 1.00),
            ("Payer:", 1.00),
        ],
        "level_2": [
            # Strong secondary
            ("Insurance Carrier", 0.95),
            ("InsuranceCarrier", 0.95),
            ("Carrier", 0.90),
        ],
        "level_3": [
            # Fallback
            ("Plan Name", 0.85),
            ("PlanName", 0.85),
            ("Payer", 0.80),
            ("Insurer", 0.80),
            ("Insurance", 0.75),
        ],
        "rationale": "Payer Name is most explicit. Insurance/Carrier are weaker (can be ambiguous)."
    },

    # ─────────────────────────────────────────────────────
    # CPT CODE
    # ─────────────────────────────────────────────────────
    "cpt_code": {
        "level_1": [
            # Most authoritative
            ("CPT Code", 1.00),
            ("CPT", 1.00),
            ("CPT:", 1.00),
        ],
        "level_2": [
            # Strong secondary
            ("Procedure Code", 0.95),
            ("Proc Code", 0.95),
            ("Service Code", 0.90),
            ("HCPCS", 0.90),
        ],
        "level_3": [
            # Fallback
            ("Code", 0.70),
        ],
        "rationale": "CPT is most explicit. 'Code' is too generic (could be diagnosis code, etc.)."
    },
}


def get_aliases_by_level(field_name: str, level: int):
    """
    Get aliases for a field at a specific level.
    
    Args:
        field_name: Field name ("check_number", "check_date", etc.)
        level: Label level (1, 2, or 3)
    
    Returns:
        List of (alias_text, weight) tuples
    
    Example:
        >>> get_aliases_by_level("check_number", 1)
        [("EPC Draft #", 1.00), ("Check/EFT No", 1.00), ...]
    """
    hierarchy = FIELD_LABEL_HIERARCHY.get(field_name, {})
    level_key = f"level_{level}"
    return hierarchy.get(level_key, [])


def get_all_aliases_for_field(field_name: str):
    """
    Get all aliases for a field across all levels.
    
    Args:
        field_name: Field name
    
    Returns:
        List of (alias_text, weight) tuples, sorted by level (L1 first)
    """
    aliases = []
    hierarchy = FIELD_LABEL_HIERARCHY.get(field_name, {})
    
    for level in [1, 2, 3]:
        level_key = f"level_{level}"
        aliases.extend(hierarchy.get(level_key, []))
    
    return aliases


def get_rationale(field_name: str) -> str:
    """Get rationale for label hierarchy of a field."""
    hierarchy = FIELD_LABEL_HIERARCHY.get(field_name, {})
    return hierarchy.get("rationale", "")


def flatten_hierarchy_to_aliases_dict() -> dict:
    """
    Convert hierarchy back to flat FIELD_ALIASES format for backward compatibility.
    
    Returns:
        {"field_name": [("alias", weight), ...], ...}
    """
    result = {}
    
    for field_name, hierarchy in FIELD_LABEL_HIERARCHY.items():
        aliases = []
        for level in [1, 2, 3]:
            level_key = f"level_{level}"
            aliases.extend(hierarchy.get(level_key, []))
        result[field_name] = aliases
    
    return result


# Print hierarchy info
if __name__ == "__main__":
    print("=" * 70)
    print("FIELD LABEL HIERARCHY SUMMARY")
    print("=" * 70)
    
    for field_name, hierarchy in FIELD_LABEL_HIERARCHY.items():
        print(f"\n{field_name.upper()}")
        print("-" * 70)
        
        for level in [1, 2, 3]:
            level_key = f"level_{level}"
            aliases = hierarchy.get(level_key, [])
            print(f"  Level {level} ({len(aliases)} aliases):")
            for alias, weight in aliases[:3]:  # Show first 3
                print(f"    - {alias} ({weight})")
            if len(aliases) > 3:
                print(f"    ... and {len(aliases) - 3} more")
        
        print(f"  Rationale: {hierarchy.get('rationale', 'N/A')}")
