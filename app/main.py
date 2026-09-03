"""
FastAPI backend for EOB/billing PDF extraction.

Run with:
    uvicorn app.main:app --reload --port 8000

Endpoints:
    POST /api/extract          -- single PDF upload
    POST /api/extract-batch    -- multiple PDF uploads
    GET  /api/records          -- list all submitted records
    POST /api/records          -- add a new record
    DELETE /api/records        -- clear all records
    GET  /api/admin/payers     -- get known payer aliases
    POST /api/admin/payers     -- update payer aliases
    GET  /health               -- liveness check
    GET  /                     -- serve extract page (index.html)
    GET  /records              -- serve records page
    GET  /admin                -- serve admin page
"""
import asyncio
import os
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from typing import List

from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from app.core import payers as payers_module
from app.core.pdf_extraction import extract_pages_from_pdf
from app.core.eob_extraction import extract_eob_data_from_pages
from app.core.scoring import normalize_date_to_ddmmyyyy
from app.models.schemas import (
    FieldResult,
    CPTResult,
    ExtractionMeta,
    ExtractionResponse,
    BatchExtractionResponse,
    Record,
    PayersPayload,
)

app = FastAPI(title="EOB Extraction API", version="1.0.0")

# CORS – allow all origins for development
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ─── HTML PAGE ROUTES ───────────────────────────────────────────────

@app.get("/")
async def home():
    return FileResponse("app/templates/index.html")

@app.get("/records")
async def records_page():
    return FileResponse("app/templates/records.html")

@app.get("/admin")
async def admin_page():
    return FileResponse("app/templates/admin.html")

# ─── API ROUTES (all under /api) ──────────────────────────────────

@app.get("/health")
async def health():
    return {"status": "ok", "time": time.time()}

@app.post("/api/extract", response_model=ExtractionResponse)
async def extract_single(file: UploadFile = File(...)):
    if not (file.filename or "").lower().endswith((".pdf", ".txt", ".text")):
        raise HTTPException(status_code=400, detail="Only PDF or text files are supported.")
    tmp_path = await _save_upload_to_temp(file)
    try:
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(EXECUTOR, _extract_sync, tmp_path, file.filename)
        return result
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"Extraction failed: {e}")
    finally:
        os.remove(tmp_path)

@app.post("/api/extract-batch", response_model=BatchExtractionResponse)
async def extract_batch(files: list[UploadFile] = File(...)):
    loop = asyncio.get_event_loop()
    results = []
    failed = []

    async def process_one(upload: UploadFile):
        if not (upload.filename or "").lower().endswith(".pdf",  ".txt", ".text"):
            failed.append({"filename": upload.filename, "error": "Not a supported file type."})
            return
        tmp_path = await _save_upload_to_temp(upload)
        try:
            result = await loop.run_in_executor(EXECUTOR, _extract_sync, tmp_path, upload.filename)
            results.append(result)
        except Exception as e:
            failed.append({"filename": upload.filename, "error": str(e)})
        finally:
            os.remove(tmp_path)

    await asyncio.gather(*(process_one(f) for f in files))
    return BatchExtractionResponse(results=results, failed=failed)

# In‑memory storage
submitted_records: List[Record] = []

@app.get("/api/records", response_model=List[Record])
async def get_records():
    return submitted_records

@app.post("/api/records", status_code=201)
async def add_record(record: Record):
    # Normalize check_date to dd/mm/yyyy here, once, at save time -- the
    # extracted date can arrive in any recognized format, but everything
    # downstream (the records table, and the CSV export) reads from this
    # same in-memory record, so normalizing on save is enough to make both
    # of them consistent.
    record.checkDate = normalize_date_to_ddmmyyyy(record.checkDate)
    exists = any(
        r.checkNumber == record.checkNumber
        and r.checkDate == record.checkDate
        and r.checkAmount == record.checkAmount
        for r in submitted_records
    )
    if exists:
        raise HTTPException(status_code=409, detail="Duplicate record")
    submitted_records.append(record)
    return {"status": "ok", "count": len(submitted_records)}

@app.delete("/api/records")
async def clear_records():
    submitted_records.clear()
    return {"status": "ok"}

@app.get("/api/admin/payers")
async def get_admin_payers():
    return {"payers": payers_module.get_all_payers()}

@app.post("/api/admin/payer", status_code=201)
async def create_payer(payload: dict):
    """Create a single payer. Expects {"name": "...", "aliases": [...]}."""
    name = payload.get("name", "").strip()
    aliases = payload.get("aliases", [])
    if not name:
        raise HTTPException(status_code=400, detail="Payer name is required.")
    # Check if already exists (case‑insensitive)
    existing = payers_module.get_all_payers()
    if any(p["name"].lower() == name.lower() for p in existing):
        raise HTTPException(status_code=409, detail=f"Payer '{name}' already exists.")
    payers_module.save_payer(name, aliases)
    return {"status": "ok", "name": name}

@app.put("/api/admin/payer/{name}")
async def update_payer(name: str, payload: dict):
    """Update an existing payer's aliases. Expects {"aliases": [...]}."""
    aliases = payload.get("aliases", [])
    existing = payers_module.get_all_payers()
    if not any(p["name"].lower() == name.lower() for p in existing):
        raise HTTPException(status_code=404, detail=f"Payer '{name}' not found.")
    # save_payer replaces or inserts; we use the original name (preserve case)
    original_name = next(p["name"] for p in existing if p["name"].lower() == name.lower())
    payers_module.save_payer(original_name, aliases)
    return {"status": "ok", "name": original_name}


@app.delete("/api/admin/payer/{name}")
async def delete_payer(name: str):
    """Delete a payer by name."""
    existing = payers_module.get_all_payers()
    if not any(p["name"].lower() == name.lower() for p in existing):
        raise HTTPException(status_code=404, detail=f"Payer '{name}' not found.")
    # Use the original case for deletion
    original_name = next(p["name"] for p in existing if p["name"].lower() == name.lower())
    payers_module.delete_payer(original_name)
    return {"status": "ok", "name": original_name}

# ─── HELPERS ────────────────────────────────────────────────────────

EXECUTOR = ThreadPoolExecutor(max_workers=int(os.environ.get("EXTRACTION_WORKERS", 4)))

def _extract_sync(file_path: str, filename: str) -> ExtractionResponse:

    if is_text_file(filename):
        pages = extract_pages_from_text_file(file_path)
    else:
        pages = extract_pages_from_pdf(file_path)
    if not pages:
        raise ValueError("No content could be extracted from this file.")
    result = extract_eob_data_from_pages(pages)
    meta = result.get("_meta", {})

    def field(name: str) -> FieldResult:
        data = result.get(name, {})
        return FieldResult(
            value=data.get("value", ""),
            confidence=data.get("confidence", 0.0),
            alias_used=data.get("alias_used"),
        )

    cpt = result.get("cpt_codes", {})
    response = ExtractionResponse(
        filename=filename,
        check_number=field("check_number"),
        check_date=field("check_date"),
        check_amount=field("check_amount"),
        practice_name=field("practice_name"),
        insurance_name=field("insurance_name"),
        cpt_codes=CPTResult(
            cpt_codes=cpt.get("cpt_codes", []),
            cpt_count=cpt.get("cpt_count", 0),
            cpt_total_occurrences=cpt.get("cpt_total_occurrences", 0),
            extraction_confidence=cpt.get("extraction_confidence", 0.0),
            cpt_occurrences=cpt.get("code_frequencies", {}),
        ),
        meta=ExtractionMeta(
            total_pages=meta.get("total_pages", len(pages)),
            header_pages_searched=meta.get("header_pages_searched", 0),
            candidate_searched = meta.get("candidate_page_numbers",0)
        ),
    )
    print("Response : ",response)
    return response

async def _save_upload_to_temp(upload: UploadFile) -> str:
    suffix = os.path.splitext(upload.filename or "")[1] or ".pdf"
    fd, path = tempfile.mkstemp(suffix=suffix)
    try:
        with os.fdopen(fd, "wb") as f:
            content = await upload.read()
            f.write(content)
    except Exception:
        os.remove(path)
        raise
    return path

def is_text_file(filename: str) -> bool:
    return filename.lower().endswith(('.txt', '.text'))

def extract_pages_from_text_file(file_path: str) -> list:
    """Read a text file and return a list with a single page dict."""
    with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
        content = f.read()
    return [{"page_number": 1, "text": content, "method": "text"}]