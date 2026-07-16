from typing import Literal

from pydantic import BaseModel, Field


class AskRequest(BaseModel):
    """Payload permintaan untuk endpoint /ask."""

    question: str = Field(
        ...,
        min_length=1,
        max_length=2000,
        description="Pertanyaan karyawan dalam Bahasa Indonesia.",
        examples=["Berapa lama SLA pengakuan tiket P1?"],
    )


class Citation(BaseModel):
    """Referensi ke satu chunk sumber yang mendasari jawaban."""

    doc_id: str
    chunk_id: str
    section_title: str
    doc_version: str
    is_active: bool


class AskResponse(BaseModel):
    """Kontrak respons wajib: answer, confidence_label, reason_code."""

    answer: str
    confidence_label: Literal["high", "medium", "low"]
    reason_code: str
    citations: list[Citation] = Field(default_factory=list)


class HealthResponse(BaseModel):
    status: str
    chunks_indexed: int
    embedding_model: str
    llm_model: str
