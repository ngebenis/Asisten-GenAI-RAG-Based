"""FastAPI entry point untuk Asisten GenAI RAG NusantaraCare."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from app.schemas import AskRequest, AskResponse, HealthResponse
from app.services.agent import LLM_MODEL, answer_question
from app.services.rag import EMBEDDING_MODEL, rag_pipeline

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Membangun index RAG dari knowledge base...")
    rag_pipeline.build()
    logger.info("Index RAG siap (%d chunk).", len(rag_pipeline.chunks))
    yield


app = FastAPI(
    title="NusantaraCare Asisten GenAI (RAG)",
    description=(
        "Asisten internal berbasis Retrieval-Augmented Generation untuk "
        "Panduan Operasional Layanan Internal NusantaraCare. Jawaban hanya "
        "berdasarkan dokumen, selalu menyertakan kutipan sumber, dan menolak "
        "pertanyaan di luar cakupan atau berpotensi prompt injection."
    ),
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/", tags=["meta"])
def root() -> dict:
    return {
        "service": "NusantaraCare Asisten GenAI",
        "docs": "/docs",
        "ask_endpoint": "POST /ask",
        "health_endpoint": "GET /health",
    }


@app.get("/health", response_model=HealthResponse, tags=["meta"])
def health() -> HealthResponse:
    return HealthResponse(
        status="ok",
        chunks_indexed=len(rag_pipeline.chunks),
        embedding_model=EMBEDDING_MODEL,
        llm_model=LLM_MODEL,
    )


@app.post("/ask", response_model=AskResponse, tags=["rag"])
def ask(payload: AskRequest) -> AskResponse:
    if not payload.question.strip():
        raise HTTPException(status_code=422, detail="question tidak boleh kosong")
    return answer_question(payload.question)
