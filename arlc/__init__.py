"""
Agentic RAG Legal Challenge 2026 — submission schema and utilities.

Submission format per competition:
- answer: str | null (for unanswerable)
- retrieved_chunk_ids: list of page IDs (doc_slug_page, 1-based)
- telemetry: ttft_ms, latency_ms, prompt_tokens, completion_tokens, retrieved_chunks

References: notes_from.txt, tech-discussion (Discord).
"""

from .schema import (
    TelemetryPayload,
    SubmissionEntry,
    SubmissionPayload,
    build_telemetry,
)
from .config import ARLCConfig, SUBMISSION_VERSION, normalize_page_id
from .telemetry import TelemetryRecorder, capture_telemetry

__all__ = [
    "TelemetryPayload",
    "SubmissionEntry",
    "SubmissionPayload",
    "build_telemetry",
    "ARLCConfig",
    "SUBMISSION_VERSION",
    "normalize_page_id",
    "TelemetryRecorder",
    "capture_telemetry",
]

# Optional imports (may fail if deps missing)
try:
    from .client import EvaluationClient
    __all__.append("EvaluationClient")
except ImportError:
    EvaluationClient = None  # type: ignore
try:
    from .pipeline import run_single_question
    __all__.append("run_single_question")
except ImportError:
    run_single_question = None  # type: ignore
try:
    from .ingestion import ingest_document_legal, page_ids_from_chunks
    __all__.extend(["ingest_document_legal", "page_ids_from_chunks"])
except ImportError:
    ingest_document_legal = None  # type: ignore
    page_ids_from_chunks = None  # type: ignore
