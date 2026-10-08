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
import csv
import io
import logging
import os
import tempfile
import threading
import time
import uuid
import webbrowser
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Dict, List

from app.core.summary_only import extract_summary_from_text_file
from app.database import database
from dotenv import load_dotenv
from fastapi import FastAPI, File, HTTPException, Header, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from openpyxl import load_workbook
from pydantic import BaseModel

from app.core.payers import get_all_practices
from app.core.pdf_extraction import extract_pages_from_pdf
from app.core.eob_extraction import extract_eob_data_from_pages
from app.core.scoring import normalize_date_to_ddmmyyyy
from app.models.schemas import (
    FieldResult,
    CPTResult,
    ExtractionMeta,
    ExtractionResponse,
    Record,
)

load_dotenv()

app = FastAPI(title="EOB Extraction API", version="2.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
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
    return {"status": "ok", "time": time.time(), "auto_approve": AUTO_APPROVE}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)
logger = logging.getLogger(__name__)

AUTO_APPROVE = os.getenv("AUTO_APPROVE", "false").strip().lower() in {
    "true", "1", "yes", "y", "on"
}
HOST = os.getenv("HOST", "127.0.0.1")
PORT = int(os.getenv("PORT", "8000"))
EXTRACTION_WORKERS = int(os.getenv("EXTRACTION_WORKERS", "4"))

EXECUTOR = ThreadPoolExecutor(max_workers=EXTRACTION_WORKERS)

submitted_records: List[Record] = []
batch_extraction_state: Dict[str, Dict[str, Any]] = {}
batch_extraction_lock = threading.Lock()


class BatchExtractionRequest(BaseModel):
    batch_location: str


def generate_batch_id() -> str:
    return str(int(time.time())) + uuid.uuid4().hex[:8]


def normalize_batch_path(location: str) -> Path:
    if not location or not location.strip():
        raise HTTPException(
            status_code=400,
            detail="batch_location is required",
        )

    path = Path(location.strip()).expanduser()

    try:
        path = path.resolve()
    except Exception as exc:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid batch location: {exc}",
        )

    if not path.exists():
        raise HTTPException(
            status_code=404,
            detail=f"Batch location does not exist: {path}",
        )

    if not path.is_dir():
        raise HTTPException(
            status_code=400,
            detail=f"Batch location is not a directory: {path}",
        )

    return path


def find_pdf_files(batch_path: Path) -> List[Path]:
    # Recursive search includes PDFs inside subfolders.
    return sorted(
        [
            path.resolve()
            for path in batch_path.rglob("*")
            if path.is_file() and path.suffix.lower() == ".pdf"
        ],
        key=lambda p: str(p).lower(),
    )


def is_text_file(filename: str) -> bool:
    return filename.lower().endswith((".txt", ".text"))


def extract_pages_from_text_file(file_path: str) -> list:
    with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
        content = f.read()

    return [{
        "page_number": 1,
        "text": content,
        "method": "text",
    }]


def _extract_sync(file_path: str, filename: str):
    logger.info("Extracting file: %s", filename)
    is_text = is_text_file(filename)

    pages = (
        extract_pages_from_text_file(file_path)
        if is_text
        else extract_pages_from_pdf(file_path)
    )

    if not pages:
        raise ValueError("No content could be extracted from this file.")

    for page in pages:
        print("------------------------------------------")
        print(f"Page {page['page_number']} (method: {page['method']}):")
        print(page["text"])
        print("------------------------------------------")

    # TXT/TEXT: extract all summary records
    if is_text:
        summary_records = extract_summary_from_text_file(pages[0]["text"])

        if summary_records:
            total_amount = sum(
                float(record["check_amount"].replace(",", ""))
                for record in summary_records
            )

            payment_status = "pay" if total_amount > 0 else "no_pay"

            responses = []

            for summary in summary_records:
                claim_count = int(summary.get("claim_count", 0) or 0)
                response = ExtractionResponse(
                    filename=filename,
                    check_number=FieldResult(
                        value=summary["check_number"],
                        confidence=1.0,
                        alias_used="summary",
                    ),
                    check_date=FieldResult(
                        value=summary["check_date"],
                        confidence=1.0,
                        alias_used="summary",
                    ),
                    check_amount=FieldResult(
                        value=summary["check_amount"],
                        confidence=1.0,
                        alias_used="summary",
                    ),
                    payment_status=payment_status,
                    practice_name=FieldResult(
                        value=summary.get("payee", ""),
                        confidence=1.0,
                        alias_used="summary",
                    ),
                    insurance_name=FieldResult(
                        value="",
                        confidence=0.0,
                        alias_used=None,
                    ),
                    cpt_codes=CPTResult(
                        cpt_codes=[],
                        cpt_count=claim_count,
                        cpt_total_occurrences=claim_count,
                        extraction_confidence=1.0 if claim_count > 0 else 0.0,
                        cpt_occurrences={},
                    ),
                    meta=ExtractionMeta(
                        total_pages=len(pages),
                        header_pages_searched=1,
                        candidate_searched=[1],
                    ),
                )

                responses.append(response)

            return responses

        # TXT/TEXT without summary -> existing extraction
        result = extract_eob_data_from_pages(pages)
    # PDF -> existing extraction unchanged
    else:
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
        payment_status=result.get("payment_status", ""),
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
            candidate_searched=meta.get("candidate_page_numbers", []),
        ),
    )

    print("Response: ", response)
    return response

async def _save_upload_to_temp(upload: UploadFile) -> str:
    suffix = os.path.splitext(upload.filename or "")[1] or ".pdf"
    fd, path = tempfile.mkstemp(suffix=suffix)

    try:
        with os.fdopen(fd, "wb") as f:
            f.write(await upload.read())
    except Exception:
        if os.path.exists(path):
            os.remove(path)
        raise

    return path


@app.post("/api/extract")
async def extract_single(file: UploadFile = File(...)):

    filename = file.filename or ""

    if not filename.lower().endswith((".pdf", ".txt", ".text")):
        raise HTTPException(
            status_code=400,
            detail="Only PDF or text files are supported.",
        )

    tmp_path = await _save_upload_to_temp(file)

    try:
        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(
            EXECUTOR,
            _extract_sync,
            tmp_path,
            filename,
        )

        if isinstance(result, list):
            return {
                "success": True,
                "count": len(result),
                "records": [
                    item.model_dump() if hasattr(item, "model_dump") else item
                    for item in result
                ],
            }

        return result
    except Exception as exc:
        logger.exception("Manual extraction failed: %s", filename)
        raise HTTPException(
            status_code=422,
            detail=f"Extraction failed: {exc}",
        )
    finally:
        try:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
        except Exception:
            logger.warning("Could not remove temp file: %s", tmp_path)


def find_duplicate(record: Dict[str, Any]) -> bool:
    normalized_date = normalize_date_to_ddmmyyyy(
        record.get("checkDate", "")
    )

    return any(
        r.checkNumber == record.get("checkNumber", "")
        and r.checkDate == normalized_date
        and r.checkAmount == record.get("checkAmount", "")
        for r in submitted_records
    )


async def save_record(record: Dict[str, Any]) -> Dict[str, Any]:
    record = dict(record)
    record["checkDate"] = normalize_date_to_ddmmyyyy(
        record.get("checkDate", "")
    )

    if find_duplicate(record):
        return {
            "success": False,
            "duplicate": True,
            "message": "Duplicate record skipped",
        }

    saved_record = Record(
        checkNumber=record.get("checkNumber", ""),
        checkDate=record.get("checkDate", ""),
        checkAmount=record.get("checkAmount", ""),
        insuranceName=record.get("insuranceName", ""),
        practiceName=record.get("practiceName", ""),
        cptCodes=record.get("cptCodes", ""),
        cptCount=str(record.get("cptCount", "")),
    )

    submitted_records.append(saved_record)

    return {
        "success": True,
        "duplicate": False,
        "message": "Record saved",
        "record": saved_record.model_dump(),
    }


def convert_extraction_to_record(result: Any) -> Dict[str, Any]:
    if hasattr(result, "model_dump"):
        result = result.model_dump()

    cpt = result.get("cpt_codes", {})

    return {
        "checkNumber": result.get("check_number", {}).get("value", ""),
        "checkDate": result.get("check_date", {}).get("value", ""),
        "checkAmount": result.get("check_amount", {}).get("value", ""),
        "insuranceName": result.get("insurance_name", {}).get("value", ""),
        "practiceName": result.get("practice_name", {}).get("value", ""),
        "cptCodes": "; ".join(
            str(x) for x in cpt.get("cpt_codes", [])
        ),
        "cptCount": str(cpt.get("cpt_total_occurrences", 0)),
    }


def get_batch(batch_id: str) -> Dict[str, Any]:
    batch = batch_extraction_state.get(batch_id)

    if not batch:
        raise HTTPException(
            status_code=404,
            detail="Batch not found",
        )

    return batch


def get_batch_file(batch_id: str, filename: str) -> Path:
    batch = get_batch(batch_id)
    requested_name = Path(filename).name

    for file_path in batch["files"]:
        path = Path(file_path)
        if path.name == requested_name:
            return path

    raise HTTPException(
        status_code=404,
        detail=f"File '{requested_name}' not found in batch.",
    )


async def extract_batch_pdf(file_path: Path) -> Any:
    loop = asyncio.get_running_loop()

    return await loop.run_in_executor(
        EXECUTOR,
        _extract_sync,
        str(file_path),
        file_path.name,
    )


async def process_batch(batch_id: str):
    batch = batch_extraction_state.get(batch_id)

    if not batch:
        return

    batch["status"] = "processing"
    total = len(batch["files"])

    for index, file_path_string in enumerate(batch["files"]):
        file_path = Path(file_path_string)
        filename = file_path.name

        batch.update({
            "current_index": index,
            "current_file": filename,
            "current_result": None,
            "current_error": None,
            "current_save_result": None,
            "waiting_for_approval": False,
            "current_status": "extracting",
            "approval_processed": False,  # Add flag to track if approval was processed
        })

        logger.info(
            "Batch %s: extracting %s/%s: %s",
            batch_id,
            index + 1,
            total,
            filename,
        )

        try:
            if not file_path.exists():
                raise FileNotFoundError(
                    f"PDF not found: {file_path}"
                )

            result = await extract_batch_pdf(file_path)

            result_for_ui = (
                result.model_dump()
                if hasattr(result, "model_dump")
                else result
            )

            batch["current_result"] = result_for_ui
            batch["current_status"] = "extracted"

        except Exception as exc:
            logger.exception(
                "Batch extraction failed: %s",
                filename,
            )

            batch["current_error"] = str(exc)
            batch["current_status"] = "failed"
            batch["failed_count"] += 1

            if batch["auto_approve"]:
                continue

            batch["waiting_for_approval"] = True
            batch["current_status"] = "waiting_approval"
            batch["approval_processed"] = False

            while batch["waiting_for_approval"]:
                if batch.get("abort", False):
                    batch["status"] = "cancelled"
                    return
                await asyncio.sleep(0.2)

            continue

        if batch["auto_approve"]:
            record = convert_extraction_to_record(result_for_ui)
            save_result = await save_record(record)

            batch["current_save_result"] = save_result

            if save_result.get("duplicate"):
                batch["current_status"] = "duplicate"
                batch["skipped_count"] += 1
            else:
                batch["current_status"] = "approved"
                batch["approved_count"] += 1

            await asyncio.sleep(0.05)
            continue

        batch["current_status"] = "waiting_approval"
        batch["waiting_for_approval"] = True
        batch["approval_processed"] = False

        while batch["waiting_for_approval"]:
            if batch.get("abort", False):
                batch["status"] = "cancelled"
                return
            await asyncio.sleep(0.2)

    batch.update({
        "current_index": total,
        "current_file": None,
        "current_result": None,
        "waiting_for_approval": False,
        "current_status": "completed",
        "status": "completed",
        "approval_processed": False,
    })

    logger.info("Batch completed: %s", batch_id)


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "time": time.time(),
        "auto_approve": AUTO_APPROVE,
    }


@app.post("/api/extract-batch")
async def extract_batch(payload: BatchExtractionRequest):
    batch_path = normalize_batch_path(payload.batch_location)
    pdf_files = find_pdf_files(batch_path)

    if not pdf_files:
        raise HTTPException(
            status_code=404,
            detail=f"No PDF files found in: {batch_path}",
        )

    batch_id = generate_batch_id()

    batch_extraction_state[batch_id] = {
        "batch_id": batch_id,
        "batch_location": str(batch_path),
        "files": [str(path) for path in pdf_files],
        "total": len(pdf_files),
        "current_index": 0,
        "current_file": None,
        "current_result": None,
        "current_error": None,
        "current_save_result": None,
        "current_status": "queued",
        "waiting_for_approval": False,
        "approved_count": 0,
        "failed_count": 0,
        "skipped_count": 0,
        "status": "queued",
        "abort": False,
        "auto_approve": AUTO_APPROVE,
        "approval_processed": False,
    }

    logger.info("Batch created: %s", batch_id)
    logger.info("Batch location: %s", batch_path)
    logger.info("PDF count: %s", len(pdf_files))

    asyncio.create_task(process_batch(batch_id))

    ui_url = f"http://{HOST}:{PORT}/?batch_id={batch_id}"

    # Opens the UI on the machine running FastAPI.
    def open_browser():
        try:
            webbrowser.open_new_tab(ui_url)
            logger.info("Opened batch UI: %s", ui_url)
        except Exception as exc:
            logger.exception("Could not open browser: %s", exc)

    threading.Timer(1.0, open_browser).start()

    return {
        "success": True,
        "batch_id": batch_id,
        "batch_location": str(batch_path),
        "total_files": len(pdf_files),
        "files": [path.name for path in pdf_files],
        "auto_approve": AUTO_APPROVE,
        "status": "processing",
        "ui_url": ui_url,
    }


@app.get("/api/extract-batch/{batch_id}/status")
async def get_batch_status(batch_id: str):
    batch = get_batch(batch_id)

    return {
        "success": True,
        "batch_id": batch["batch_id"],
        "batch_location": batch["batch_location"],
        "status": batch["status"],
        "total": batch["total"],
        "current": min(
            batch["current_index"] + 1,
            batch["total"],
        ),
        "current_index": batch["current_index"],
        "current_file": batch["current_file"],
        "current_status": batch["current_status"],
        "waiting_for_approval": batch["waiting_for_approval"],
        "approved_count": batch["approved_count"],
        "failed_count": batch["failed_count"],
        "skipped_count": batch["skipped_count"],
        "auto_approve": batch["auto_approve"],
        "error": batch.get("current_error"),
        "approval_processed": batch.get("approval_processed", False),
    }


@app.get("/api/extract-batch/{batch_id}/current")
async def get_batch_current(batch_id: str):
    batch = get_batch(batch_id)

    return {
        "success": True,
        "batch_id": batch["batch_id"],
        "current_index": batch["current_index"],
        "current_file": batch["current_file"],
        "current_status": batch["current_status"],
        "result": batch.get("current_result"),
        "waiting_for_approval": batch["waiting_for_approval"],
        "error": batch.get("current_error"),
        "save_result": batch.get("current_save_result"),
        "approval_processed": batch.get("approval_processed", False),
    }


@app.get("/api/extract-batch/{batch_id}/file/{filename:path}")
async def get_batch_file_endpoint(
    batch_id: str,
    filename: str,
):
    file_path = get_batch_file(batch_id, filename)

    if not file_path.exists():
        raise HTTPException(
            status_code=404,
            detail=f"PDF no longer exists: {file_path}",
        )

    if file_path.suffix.lower() != ".pdf":
        raise HTTPException(
            status_code=400,
            detail="Only PDF files are allowed.",
        )

    return FileResponse(
        path=str(file_path),
        media_type="application/pdf",
        filename=file_path.name,
    )


@app.post("/api/extract-batch/{batch_id}/approve")
async def approve_batch_pdf(
    batch_id: str,
    record: Dict[str, Any],
):
    batch = get_batch(batch_id)

    if batch["auto_approve"]:
        raise HTTPException(
            status_code=400,
            detail=(
                "Manual approval is disabled "
                "because AUTO_APPROVE=true"
            ),
        )

    if batch["status"] == "completed":
        raise HTTPException(
            status_code=400,
            detail="Batch already completed.",
        )

    if not batch["waiting_for_approval"]:
        raise HTTPException(
            status_code=400,
            detail="No PDF is currently waiting for approval.",
        )

    if batch.get("approval_processed", False):
        raise HTTPException(
            status_code=400,
            detail="This PDF has already been processed.",
        )

    save_result = await save_record(record)
    batch["current_save_result"] = save_result

    if save_result.get("duplicate"):
        batch["current_status"] = "duplicate"
        batch["skipped_count"] += 1
    else:
        batch["current_status"] = "approved"
        batch["approved_count"] += 1

    batch["waiting_for_approval"] = False
    batch["approval_processed"] = True

    logger.info(
        "Batch %s approved: %s",
        batch_id,
        batch["current_file"],
    )

    return {
        "success": True,
        "batch_id": batch_id,
        "filename": batch["current_file"],
        "status": batch["current_status"],
        **save_result,
    }


@app.post("/api/extract-batch/{batch_id}/skip")
async def skip_batch_pdf(batch_id: str):
    batch = get_batch(batch_id)

    if batch["status"] == "completed":
        raise HTTPException(
            status_code=400,
            detail="Batch already completed.",
        )

    if not batch["waiting_for_approval"]:
        raise HTTPException(
            status_code=400,
            detail="No PDF is currently waiting.",
        )

    if batch.get("approval_processed", False):
        raise HTTPException(
            status_code=400,
            detail="This PDF has already been processed.",
        )

    batch["current_status"] = "skipped"
    batch["skipped_count"] += 1
    batch["waiting_for_approval"] = False
    batch["approval_processed"] = True

    logger.info(
        "Batch %s skipped: %s",
        batch_id,
        batch["current_file"],
    )

    return {
        "success": True,
        "batch_id": batch_id,
        "filename": batch["current_file"],
        "status": "skipped",
    }


@app.get("/api/records", response_model=List[Record])
async def get_records():
    return submitted_records


@app.post("/api/records", status_code=201)
async def add_record(record: Record):
    record.checkDate = normalize_date_to_ddmmyyyy(
        record.checkDate
    )

    exists = any(
        r.checkNumber == record.checkNumber
        and r.checkDate == record.checkDate
        and r.checkAmount == record.checkAmount
        for r in submitted_records
    )

    if exists:
        raise HTTPException(
            status_code=409,
            detail="Duplicate record",
        )

    submitted_records.append(record)

    return {
        "status": "ok",
        "count": len(submitted_records),
    }


@app.delete("/api/records")
async def clear_records():
    submitted_records.clear()
    return {"status": "ok"}


# ============ Admin API Endpoints ============

# Import database functions

def _parse_entity_upload(filename: str, content: bytes) -> List[Dict[str, Any]]:
    """Parse a names/possible names CSV or XLSX into canonical records."""
    suffix = Path(filename).suffix.lower()
    if suffix == ".csv":
        text = content.decode("utf-8-sig")
        rows = list(csv.reader(io.StringIO(text)))
    elif suffix in {".xlsx", ".xlsm"}:
        workbook = load_workbook(io.BytesIO(content), read_only=True, data_only=True)
        worksheet = workbook.active
        rows = [["" if value is None else str(value) for value in row]
                for row in worksheet.iter_rows(values_only=True)]
        workbook.close()
    else:
        raise HTTPException(status_code=400, detail="Only CSV or Excel (.xlsx) files are allowed.")

    if not rows:
        raise HTTPException(status_code=400, detail="The file is empty.")

    headers = [str(value).strip().lower() for value in rows[0]]
    try:
        name_index = headers.index("names")
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail="The file must contain a 'names' column.",
        )
    alias_index = headers.index("possible names") if "possible names" in headers else None

    entries = {}
    for row_number, row in enumerate(rows[1:], start=2):
        name = str(row[name_index]).strip() if name_index < len(row) and row[name_index] is not None else ""
        if not name:
            continue
        record = entries.setdefault(name, {"name": name, "aliases": []})
        if alias_index is not None:
            for value in row[alias_index:]:
                aliases = str(value).split(",") if value is not None else []
                for alias in aliases:
                    alias = alias.strip()
                    if alias and alias not in record["aliases"]:
                        record["aliases"].append(alias)

    if not entries:
        raise HTTPException(status_code=400, detail="The file contains no valid names.")
    return list(entries.values())


async def _bulk_import_entity_file(file: UploadFile, entity: str):
    """Import one uploaded entity file into its dedicated database table."""
    if not file.filename:
        raise HTTPException(status_code=400, detail="A CSV or Excel file is required.")
    rows = _parse_entity_upload(file.filename, await file.read())
    importer = database.bulk_import_payers if entity == "payors" else database.bulk_import_practices
    result = importer(rows)
    return {
        "status": "ok",
        "created": result["created"],
        "updated": result["updated"],
        "errors": result["errors"],
    }

@app.get("/api/admin/payors")
async def get_admin_payors():
    """Get all payors from database."""
    return {"payors": database.get_all_payers()}


@app.post("/api/admin/payor", status_code=201)
async def create_payor(payload: dict):
    """Create a new payor."""
    name = payload.get("name", "").strip()
    aliases = payload.get("aliases", [])
    if not name:
        raise HTTPException(status_code=400, detail="Payor name is required.")
    
    existing = database.get_all_payers()
    if any(p["name"].lower() == name.lower() for p in existing):
        raise HTTPException(status_code=409, detail=f"Payor '{name}' already exists.")
    
    database.save_payer(name, aliases)
    return {"status": "ok", "name": name}


@app.put("/api/admin/payor/{name}")
async def update_payor(name: str, payload: dict):
    """Update an existing payor."""
    aliases = payload.get("aliases", [])
    existing = database.get_all_payers()
    matching = next((p for p in existing if p["name"].lower() == name.lower()), None)
    if not matching:
        raise HTTPException(status_code=404, detail=f"Payor '{name}' not found.")
    
    database.save_payer(matching["name"], aliases)
    return {"status": "ok", "name": matching["name"]}


@app.delete("/api/admin/payor/{name}")
async def delete_payor(name: str):
    """Delete a payor."""
    existing = database.get_all_payers()
    matching = next((p for p in existing if p["name"].lower() == name.lower()), None)
    if not matching:
        raise HTTPException(status_code=404, detail=f"Payor '{name}' not found.")
    
    database.delete_payer(matching["name"])
    return {"status": "ok", "name": matching["name"]}


@app.post("/api/admin/payors/bulk")
async def bulk_import_payors(payload: dict):
    """Bulk import payors from a list."""
    payors = payload.get("payors", [])
    if not payors:
        raise HTTPException(status_code=400, detail="No payors provided.")
    
    result = database.bulk_import_payers(payors)
    return {
        "status": "ok",
        "created": result["created"],
        "updated": result["updated"],
        "errors": result["errors"]
    }


@app.post("/api/admin/payors/bulk-csv")
async def bulk_import_payors_csv(file: UploadFile = File(...)):
    """Bulk import payors from a names/possible names CSV or Excel file."""
    return await _bulk_import_entity_file(file, "payors")


# ============ Practice Admin API ============

@app.get("/api/admin/practices")
async def get_admin_practices():
    """Get all practices from database."""
    return {"practices": database.get_all_practices()}


@app.post("/api/admin/practice", status_code=201)
async def create_practice(payload: dict):
    """Create a new practice."""
    name = payload.get("name", "").strip()
    aliases = payload.get("aliases", [])
    if not name:
        raise HTTPException(status_code=400, detail="Practice name is required.")
    
    existing = database.get_all_practices()
    if any(p["name"].lower() == name.lower() for p in existing):
        raise HTTPException(status_code=409, detail=f"Practice '{name}' already exists.")
    
    database.save_practice(name, aliases)
    return {"status": "ok", "name": name}

@app.get("/api/debug/database")
async def debug_database():
    import app.database as dbmod
    return {
        "file": dbmod.__file__,
        "has_get_all_payers": hasattr(dbmod, "get_all_payers"),
        "has_get_all_practices": hasattr(dbmod, "get_all_practices"),
        "has_bulk_import_payers": hasattr(dbmod, "bulk_import_payers"),
        "all_attrs": [a for a in dir(dbmod) if not a.startswith("_")],
    }


@app.put("/api/admin/practice/{name}")
async def update_practice(name: str, payload: dict):
    """Update an existing practice."""
    aliases = payload.get("aliases", [])
    existing = database.get_all_practices()
    matching = next((p for p in existing if p["name"].lower() == name.lower()), None)
    if not matching:
        raise HTTPException(status_code=404, detail=f"Practice '{name}' not found.")
    
    database.save_practice(matching["name"], aliases)
    return {"status": "ok", "name": matching["name"]}


@app.delete("/api/admin/practice/{name}")
async def delete_practice(name: str):
    """Delete a practice."""
    existing = database.get_all_practices()
    matching = next((p for p in existing if p["name"].lower() == name.lower()), None)
    if not matching:
        raise HTTPException(status_code=404, detail=f"Practice '{name}' not found.")
    
    database.delete_practice(matching["name"])
    return {"status": "ok", "name": matching["name"]}


@app.post("/api/admin/practices/bulk")
async def bulk_import_practices(payload: dict):
    """Bulk import practices from a list."""
    practices = payload.get("practices", [])
    if not practices:
        raise HTTPException(status_code=400, detail="No practices provided.")
    
    result = database.bulk_import_practices(practices)
    return {
        "status": "ok",
        "created": result["created"],
        "updated": result["updated"],
        "errors": result["errors"]
    }

@app.post("/api/admin/practices/bulk-csv")
async def bulk_import_practices_csv(file: UploadFile = File(...)):
    """Bulk import practices from a names/possible names CSV or Excel file."""
    return await _bulk_import_entity_file(file, "practices")