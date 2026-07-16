"""Pipeline RAG: parsing dokumen, chunking dengan metadata, dan retrieval vektor.

Desain chunking dan alasan tiap keputusan didokumentasikan di README.md
(bagian "RAG Design & Data Preparation").
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import numpy as np
import yaml

logger = logging.getLogger(__name__)

DOC_PATH = Path(
    os.getenv(
        "KB_PATH",
        "data/raw_docs/nusantaracare_panduan_operasional_internal_v2.md",
    )
)
EMBEDDING_MODEL = os.getenv(
    "EMBEDDING_MODEL", "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
)
MAX_CHUNK_CHARS = int(os.getenv("MAX_CHUNK_CHARS", "1200"))
CHUNK_OVERLAP_CHARS = int(os.getenv("CHUNK_OVERLAP_CHARS", "200"))

# Penanda H2/H3 yang menunjukkan konten arsip kebijakan v1.4 (nonaktif).
# Dokumen sumber sendiri menegaskan metadata arsip ini di bagian
# "Riwayat Perubahan dan Arsip Kebijakan" > "Arsip Kebijakan v1.4 — NONAKTIF".
ARCHIVE_SECTION_MARKERS = ("arsip kebijakan v1.4",)
ARCHIVE_DOC_VERSION = "1.4"
ARCHIVE_EFFECTIVE_DATE = "2025-01-01"
ARCHIVE_EFFECTIVE_UNTIL = "2026-06-30"


@dataclass
class Chunk:
    chunk_id: str
    text: str
    doc_id: str
    doc_title: str
    section_title: str
    subsection_title: Optional[str]
    doc_version: str
    is_active: bool
    effective_date: Optional[str]
    source_path: str


def _parse_frontmatter(raw: str) -> tuple[dict, str]:
    """Pisahkan YAML frontmatter dari isi markdown."""
    if not raw.startswith("---"):
        return {}, raw
    end = raw.find("\n---", 3)
    if end == -1:
        return {}, raw
    frontmatter_raw = raw[3:end].strip()
    body = raw[end + 4 :].lstrip("\n")
    metadata = yaml.safe_load(frontmatter_raw) or {}
    return metadata, body


def _split_into_sections(body: str) -> list[dict]:
    """Kelompokkan paragraf berdasarkan header H2 (##) dan H3 (###) terdekat."""
    sections: list[dict] = []
    current_h2: Optional[str] = None
    current_h3: Optional[str] = None
    buffer: list[str] = []

    def flush() -> None:
        text = "\n".join(buffer).strip()
        if text:
            sections.append({"h2": current_h2, "h3": current_h3, "text": text})
        buffer.clear()

    for line in body.split("\n"):
        h2_match = re.match(r"^##\s+(.*)", line)
        h3_match = re.match(r"^###\s+(.*)", line)
        if h2_match:
            flush()
            current_h2 = h2_match.group(1).strip()
            current_h3 = None
            continue
        if h3_match:
            flush()
            current_h3 = h3_match.group(1).strip()
            continue
        # Baris judul H1 (#) diabaikan sebagai konten, tapi tidak memutus section.
        if re.match(r"^#\s+", line):
            continue
        buffer.append(line)
    flush()
    return sections


def _split_long_text(text: str, max_chars: int, overlap: int) -> list[str]:
    """Pecah teks section yang panjang menjadi beberapa chunk dengan overlap.

    Alasan: section pendek (mayoritas dokumen ini) tetap jadi satu chunk utuh
    supaya konteks SOP tidak terpotong. Section panjang (mis. FAQ, contoh
    lengkap) dipecah per-paragraf dengan overlap agar kalimat penutup satu
    chunk tidak kehilangan konteks pembuka chunk berikutnya.
    """
    if len(text) <= max_chars:
        return [text]

    paragraphs = [p for p in text.split("\n\n") if p.strip()]
    if not paragraphs:
        paragraphs = [text]

    chunks: list[str] = []
    current = ""
    for para in paragraphs:
        candidate = f"{current}\n\n{para}".strip() if current else para
        if len(candidate) > max_chars and current:
            chunks.append(current)
            tail = current[-overlap:] if overlap else ""
            current = f"{tail}\n\n{para}".strip()
        else:
            current = candidate
    if current:
        chunks.append(current)
    return chunks


def _is_archive_section(h2: Optional[str], h3: Optional[str]) -> bool:
    haystack = f"{h2 or ''} {h3 or ''}".lower()
    return any(marker in haystack for marker in ARCHIVE_SECTION_MARKERS)


def load_chunks(doc_path: Optional[Path] = None) -> list[Chunk]:
    """Parse dokumen KB menjadi daftar chunk dengan metadata per chunk.

    Metadata level dokumen (doc_version=2.0, is_active=True) diwariskan ke
    semua chunk KECUALI chunk yang berasal dari bagian arsip kebijakan v1.4,
    yang di-override menjadi doc_version=1.4 dan is_active=False sesuai
    metadata arsip yang dinyatakan eksplisit oleh dokumen itu sendiri.
    """
    path = doc_path or DOC_PATH
    raw = path.read_text(encoding="utf-8")
    metadata, body = _parse_frontmatter(raw)

    doc_id = metadata.get("doc_id", "UNKNOWN")
    doc_title = metadata.get("doc_title", path.stem)
    doc_version = str(metadata.get("doc_version", ""))
    is_active_default = bool(metadata.get("is_active", True))
    effective_date_default = metadata.get("effective_date")

    sections = _split_into_sections(body)
    chunks: list[Chunk] = []
    idx = 0
    for section in sections:
        archived = _is_archive_section(section["h2"], section["h3"])
        doc_version_chunk = ARCHIVE_DOC_VERSION if archived else doc_version
        is_active_chunk = False if archived else is_active_default
        effective_date_chunk = (
            ARCHIVE_EFFECTIVE_DATE if archived else effective_date_default
        )

        parts = _split_long_text(section["text"], MAX_CHUNK_CHARS, CHUNK_OVERLAP_CHARS)
        for part in parts:
            idx += 1
            chunks.append(
                Chunk(
                    chunk_id=f"{doc_id}-{idx:03d}",
                    text=part,
                    doc_id=doc_id,
                    doc_title=doc_title,
                    section_title=section["h2"] or doc_title,
                    subsection_title=section["h3"],
                    doc_version=doc_version_chunk,
                    is_active=is_active_chunk,
                    effective_date=(
                        str(effective_date_chunk) if effective_date_chunk else None
                    ),
                    source_path=str(path),
                )
            )
    return chunks


class RagPipeline:
    """Membangun index vektor in-memory dan melakukan retrieval top-k."""

    def __init__(self) -> None:
        self._model = None
        self.chunks: list[Chunk] = []
        self._embeddings: Optional[np.ndarray] = None

    def _load_model(self):
        if self._model is None:
            from sentence_transformers import SentenceTransformer

            logger.info("Memuat model embedding: %s", EMBEDDING_MODEL)
            self._model = SentenceTransformer(EMBEDDING_MODEL)
        return self._model

    def build(self) -> None:
        self.chunks = load_chunks()
        if not self.chunks:
            raise RuntimeError(f"Tidak ada chunk yang berhasil di-parse dari {DOC_PATH}")

        model = self._load_model()
        texts = [c.text for c in self.chunks]
        embeddings = model.encode(
            texts, normalize_embeddings=True, show_progress_bar=False
        )
        self._embeddings = np.asarray(embeddings, dtype="float32")
        self._build_index()
        logger.info("RAG index dibangun dengan %d chunk.", len(self.chunks))

    def _build_index(self) -> None:
        import faiss

        dim = self._embeddings.shape[1]
        self._index = faiss.IndexFlatIP(dim)
        self._index.add(self._embeddings)

    def retrieve(self, query: str, top_k: int) -> list[tuple[Chunk, float]]:
        if self._embeddings is None:
            raise RuntimeError("RAG index belum dibangun. Panggil .build() terlebih dahulu.")

        model = self._load_model()
        query_vec = model.encode([query], normalize_embeddings=True)
        query_vec = np.asarray(query_vec, dtype="float32")

        scores, indices = self._index.search(query_vec, min(top_k, len(self.chunks)))
        results: list[tuple[Chunk, float]] = []
        for score, idx in zip(scores[0], indices[0]):
            if idx == -1:
                continue
            results.append((self.chunks[idx], float(score)))
        return results


rag_pipeline = RagPipeline()
