# Extraction Flow Analysis

## High-Level Pipeline

```
User Upload (PDF/Text)
        ↓
    _extract_sync()
        ↓
    ├─→ Is Text File? 
    │   ├─ YES → extract_pages_from_text_file() → [page dict]
    │   └─ NO → extract_pages_from_pdf() → [page dicts]
    │
    ├─→ extract_eob_data_from_pages(pages)
    │   ├─→ detect_payor_and_practice_from_first_page()
    │   │   ├─ Insurance Name Detection
    │   │   └─ Practice Name Detection
    │   ├─→ extract_field(header_text, "check_number")
    │   ├─→ extract_field(header_text, "check_date")
    │   ├─→ extract_field(header_text, "check_amount")
    │   └─→ extract_cpt_codes(full_text)
    │
    └─→ ExtractionResponse (to user)
```

---

## Detailed Stage Breakdown

### Stage 1: PDF Text Extraction (`extract_pages_from_pdf`)

**Purpose**: Convert PDF pages to text with multiple fallback methods.

**Flow**:
1. **Rotation Detection** → `detect_page_rotation()` using Tesseract OCR orientation
   - Analyzes each page for rotation angle
   - Builds in-memory corrected PDF if rotations detected

2. **Text Extraction (per-page, in order)**:
   - **Method 1 (Primary)**: `pdfplumber` with layout preservation
   - **Method 2 (Secondary)**: `PyMuPDF` (fitz) text extraction
   - **Method 3 (Tertiary)**: `PyPDF2` standard extraction
   - **Method 4 (Fallback)**: OCR via `pytesseract` if text is missing/short

3. **Output**: List of page dicts
   ```python
   [
       {"page_number": 1, "text": "...", "method": "pdfplumber"},
       {"page_number": 2, "text": "...", "method": "PyMuPDF"},
       ...
   ]
   ```

---

### Stage 2: Insurance & Practice Name Detection (`detect_payor_and_practice_from_first_page`)

**Purpose**: Identify the insurance provider and practice/provider names from document header.

#### Insurance Name Detection (Priority Order)
1. **Priority 1 - Cross-Page Consistency**:
   - Scan first `header_page_count` pages for candidates
   - Check if candidates appear consistently across pages (≥80% threshold)
   - Requires minimum confidence 0.7

2. **Priority 2 - First Page Lines**:
   - Extract from first 20 lines of header pages
   - Score based on:
     - Known payer match (+0.5)
     - Insurance keywords: "Insurance", "Company", "Corp", "Inc" (+0.15)
     - All uppercase text (+0.1)
     - Length > 15 chars (+0.1)
     - Penalty for label-like text (-0.4)

3. **Priority 3 - Gazetteer Lookup** (KNOWN_PAYERS):
   - Match against known insurance company names
   - Confidence: 0.85
   - Uses fuzzy matching with 0.82 similarity ratio

#### Practice Name Detection (Priority Order)
1. **"Pay To" Section**:
   - Regex pattern: `Pay To:\s+([A-Z][A-Za-z ,.&\-]{3,60})`
   - Confidence: 0.85 (0.95 if has LLC/PLLC/PC/PA)

2. **Practice Patterns**:
   - Matches: LLC, PLLC, PC, P.A., Associates, Medical Group, Clinic
   - Or: MD/DO/DDS/DMD/DC/PhD credentials

3. **Address Adjacency**:
   - Lines above US addresses (ZIP code pattern)
   - Confidence: 0.75 (reduced by 0.1 per line distance)

---

### Stage 3: Document Header Extraction (`extract_field`)

**Purpose**: Extract header fields from first N pages only.

**Scope**: Search only header pages
- 4+ total pages → search pages 1-3
- ≤4 total pages → search pages 1-2 (capped at actual count)

**Fields Extracted**:
- `check_number`: Regex matching + validation
- `check_date`: Multiple date format matching (MM/DD/YYYY, MM/DD/YY, etc.)
- `check_amount`: Currency parsing ($XXX.XX, XXX,XXX.XX formats)

**Algorithm** (in `field_extraction.py`):
1. Find field aliases (label patterns like "Check #:", "Date:", "Amount:")
2. Get nearby lines (right, left, up, down from alias)
3. Extract values using field-specific regex
4. Score candidates by:
   - Alias weight
   - Direction (right=0, left=30, same-line=20 penalty)
   - Distance from alias
   - Source context quality
5. Deduplicate by value
6. Return best-scored candidate

---

### Stage 4: CPT Code Extraction (`extract_cpt_codes`)

**Purpose**: Extract medical procedure codes from entire document.

**Rules** (in `cpt_extraction.py`):
1. Code format: 5-digit or 7-digit with optional letters (e.g., "92507", "99214GN")
2. Valid only on lines with:
   - **AND** `Date pattern` (MM/DD/YYYY) AND `Money pattern` ($XXX.XX)
   - **OR** Explicit CPT context: "CPT", "Procedure", "HCPCS", "Service"

3. **False Positive Guard**:
   - Reject if line labeled as: ZIP, Account, Member ID, Group #, NPI, Tax ID, Phone, SSN, EIN
   - UNLESS explicit CPT context present

4. **Scoring**: 
   - Count code frequency
   - Calculate extraction confidence
   - Track line-by-line details

**Output**:
```python
{
    "cpt_codes": ["92507", "99214", ...],
    "cpt_count": 15,
    "cpt_total_occurrences": 25,
    "code_frequencies": {"92507": 3, "99214": 2, ...},
    "extraction_confidence": 0.92,
    "line_details": [...]
}
```

---

### Stage 5: Response Assembly

**Maps extracted data** to `ExtractionResponse` schema:
```python
ExtractionResponse(
    filename: str,
    check_number: FieldResult,      # value, confidence, alias_used
    check_date: FieldResult,
    check_amount: FieldResult,
    practice_name: FieldResult,
    insurance_name: FieldResult,
    cpt_codes: CPTResult,           # codes, count, confidence, frequencies
    meta: ExtractionMeta            # total_pages, header_pages_searched
)
```

---

## Key Configuration Parameters

| Parameter | Default | Used In |
|-----------|---------|---------|
| `header_page_count` | get_header_page_range() | Stage 3, extracts from first 1-3 pages |
| `min_chars` | 20 | PDF extraction fallback threshold |
| `min_rotation_conf` | 1.0 | Rotation detection confidence |
| `dpi` | 150 (rotation), 300 (OCR) | Image processing |
| `date_pattern` | See `cpt_extraction.py` | CPT validation |
| `money_pattern` | `\$?\s*[\d,]+\.\d{2}` | CPT validation |
| `threshold` | 0.8 | Insurance cross-page consistency |
| `min_ratio` | 0.82 | Payer fuzzy matching |

---

## Error Handling & Fallbacks

1. **PDF Extraction Failures**:
   - Tries 4 methods sequentially
   - OCR is last resort
   - Marks method used per page

2. **Field Extraction**:
   - Returns empty value + 0.0 confidence if not found
   - No exception raised

3. **Insurance/Practice Detection**:
   - Falls through priority levels
   - Returns empty if all priorities fail

4. **CPT Codes**:
   - Returns empty list if no valid codes found
   - Confidence = 0.0

---

## Summary

The extraction follows a **sequential multi-stage pipeline** with **strategic fallbacks** and **confidence scoring**. It prioritizes:
1. **Accuracy** via multi-method extraction and cross-validation
2. **Robustness** via OCR fallback for corrupted PDFs
3. **Specificity** via rule-based guards (no false positives for CPT)
4. **Completeness** via priority-ordered detection (e.g., 3-tier insurance matching)
