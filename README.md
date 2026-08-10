# EOB Extraction API

FastAPI backend for extracting billing fields from EOB/remittance PDFs:
check number, check date, check amount, practice name, insurance name, and CPT codes.

## Project structure

```
app/
  main.py                   FastAPI app (endpoints, thread pool, temp-file handling)
  core/
    patterns.py             Alias dictionaries + value-pattern regexes + context hints
    scoring.py               Candidate dataclass, normalization, alias/value matching, scoring
    field_extraction.py      find_field_candidates / extract_field (the alias+scoring engine)
    cpt_extraction.py        CPT code extraction (date+money-on-same-line rule + negative-context guard)
    pdf_extraction.py         Per-page cascade: pdfplumber -> PyMuPDF -> PyPDF2 -> OCR, with rotation correction
    eob_extraction.py         Document-level orchestration + header page-range rule
  models/
    schemas.py                Pydantic response models
requirements.txt
```

## Setup

1. System dependencies (OCR path needs these regardless of the Python packages):
   ```bash
   # Debian/Ubuntu
   sudo apt-get install tesseract-ocr poppler-utils
   ```

2. Python dependencies:
   ```bash
   python -m venv venv
   source venv/bin/activate   # Windows: venv\Scripts\activate
   pip install -r requirements.txt
   ```

## Run

```bash
uvicorn app.main:app --reload --port 8000
```

Interactive API docs (Swagger UI) at: http://localhost:8000/docs

## Endpoints

- `POST /extract` -- upload one PDF (`file` form field), returns one extraction result.
- `POST /extract-batch` -- upload multiple PDFs (`files` form field, repeated), returns
  results + a `failed` list for any files that errored out (one bad PDF doesn't
  break the whole batch).
- `GET /health` -- liveness check.

Example:
```bash
curl -X POST http://localhost:8000/extract \
  -F "file=@/path/to/eob.pdf"
```

## Header page-range rule

- PDFs with **more than 4 pages** -> check_number / check_date / check_amount /
  practice_name / insurance_name are searched in **pages 1-3**.
- PDFs with **4 or fewer pages** -> searched in **pages 1-2**.
- **CPT codes** are always searched across **all pages**.

## Fixes made while porting this out of the notebook

1. `get_header_page_range`'s "<=4 pages" branch was returning `min(1, total_pages)`
   (page 1 only), which didn't match the stated rule. Fixed to `min(2, total_pages)`.
2. `detect_payor_and_practice_from_first_page` always used `pages[0]` only, ignoring
   the header page-range entirely. It now accepts `header_page_count` and searches
   the same page window as the other header fields.
3. CPT extraction was matching numbers on a line *adjacent* to a valid date+money
   charge line (e.g. a ZIP code or account number sitting right after a charge row
   would get swept in as a CPT code). Tightened to require date+money on the
   **same line**, and added an explicit negative-context guard against
   zip/account/member-id/group/NPI/tax-id/phone/SSN labels.

## Concurrency note

PDF parsing and OCR are CPU-bound, blocking operations. `main.py` runs them in a
`ThreadPoolExecutor` (via `run_in_executor`) rather than directly inside the async
endpoint, so the API stays responsive under load and a batch upload extracts
multiple PDFs concurrently rather than one at a time. Tune worker count with the
`EXTRACTION_WORKERS` environment variable (default 4).

## Known limitation

This sandbox has no network access, so `fastapi`/`pydantic`/`PyMuPDF` could not be
installed and the app could not be run end-to-end here. All syntax has been
verified, and the extraction core (`patterns` -> `scoring` -> `field_extraction` ->
`cpt_extraction` -> `eob_extraction`) has been imported and functionally tested
against a sample EOB. Run `uvicorn app.main:app --reload` in your own environment
(which already has these packages, per your original notebook) to verify the API
layer end-to-end.
