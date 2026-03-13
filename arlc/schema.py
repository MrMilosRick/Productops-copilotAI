"""
ARLC submission schema: per-answer structure and full submission.json shape.

Competition rules (from notes_from.txt):
- retrieved_chunk_ids: only pages actually used to generate the answer; format doc_slug_page (1-based).
- Telemetry mandatory: ttft, latency, prompt_tokens, completion_tokens, retrieved_chunks.
- Answer types: boolean (true/false), number, date (ISO 8601), name, names (list), free_text (1-3 sentences, max 280 chars).
- Unanswerable: answer null or "Not found in provided legal sources.", retrieved_chunk_ids = [].
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Any, List, Optional


@dataclass
class TelemetryPayload:
    """Mandatory telemetry for each answer. Missing telemetry → 0.9 factor."""
    ttft_ms: float
    latency_ms: float
    prompt_tokens: int
    completion_tokens: int
    retrieved_chunks: List[str]  # page IDs actually used

    def to_dict(self) -> dict:
        return {
            "ttft": self.ttft_ms,
            "latency": self.latency_ms,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "retrieved_chunks": self.retrieved_chunks,
        }


@dataclass
class SubmissionEntry:
    """One question's answer for submission.json."""
    question_id: str
    answer: Any  # str | bool | int | float | list[str] | null
    retrieved_chunk_ids: List[str]  # page IDs: doc_slug_page (1-based)
    telemetry: TelemetryPayload
    model_name: Optional[str] = None
    time_per_output_token_ms: Optional[float] = None

    def to_dict(self) -> dict:
        d = {
            "question_id": self.question_id,
            "answer": self.answer,
            "retrieved_chunk_ids": self.retrieved_chunk_ids,
            "telemetry": self.telemetry.to_dict(),
        }
        if self.model_name is not None:
            d["model_name"] = self.model_name
        if self.time_per_output_token_ms is not None:
            d["time_per_output_token_ms"] = self.time_per_output_token_ms
        return d


@dataclass
class SubmissionPayload:
    """Full submission: list of answers (v14 = retrieved_chunk_ids as doc_slug_page, 1-based)."""
    answers: List[SubmissionEntry] = field(default_factory=list)
    submission_version: Optional[str] = None  # e.g. "v14" for page ID format

    def to_dict(self) -> dict:
        d = {"answers": [a.to_dict() for a in self.answers]}
        if self.submission_version is not None:
            d["submission_version"] = self.submission_version
        return d

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent)

    def save(self, path: str, indent: int = 2) -> None:
        with open(path, "w", encoding="utf-8") as f:
            f.write(self.to_json(indent=indent))


def build_telemetry(
    ttft_ms: float,
    latency_ms: float,
    prompt_tokens: int,
    completion_tokens: int,
    retrieved_chunks: List[str],
) -> TelemetryPayload:
    return TelemetryPayload(
        ttft_ms=ttft_ms,
        latency_ms=latency_ms,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        retrieved_chunks=list(retrieved_chunks),
    )
