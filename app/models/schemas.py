"""Pydantic response schemas for the extraction API."""
from typing import Optional
from pydantic import BaseModel


class FieldResult(BaseModel):
    value: str
    confidence: float
    alias_used: Optional[str] = None


class CPTResult(BaseModel):
    cpt_codes: list[str]
    cpt_count: int
    cpt_total_occurrences: int
    extraction_confidence: float
    cpt_occurrences: dict[str, int]


class ExtractionMeta(BaseModel):
    total_pages: int
    header_pages_searched: int
    candidate_searched: list[int]


class ExtractionResponse(BaseModel):
    filename: str
    check_number: FieldResult
    check_date: FieldResult
    check_amount: FieldResult
    practice_name: FieldResult
    insurance_name: FieldResult
    cpt_codes: CPTResult
    meta: ExtractionMeta


class BatchExtractionResponse(BaseModel):
    results: list[ExtractionResponse]
    failed: list[dict]  # [{"filename": ..., "error": ...}]

class Record(BaseModel):
    checkNumber: str
    checkDate: str
    checkAmount: str
    insuranceName: str
    practiceName: str
    cptCodes: str
    cptCount: str


class PayerEntry(BaseModel):
    name: str
    aliases: list[str]


class PayersPayload(BaseModel):
    payers: list[PayerEntry]