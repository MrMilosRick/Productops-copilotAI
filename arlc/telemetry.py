"""
Telemetry capture for ARLC: TTFT, total latency, token counts.

TTFT = time from "question received" to first token of final answer.
Latency = time from "question received" to full response.
"""

import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Generator, List, Optional


@dataclass
class TelemetryRecorder:
    """Records timings and token usage for one answer."""
    start_time: float = field(default_factory=time.perf_counter)
    ttft_ms: Optional[float] = None
    latency_ms: Optional[float] = None
    prompt_tokens: int = 0
    completion_tokens: int = 0
    retrieved_chunks: List[str] = field(default_factory=list)

    def mark_first_token(self) -> None:
        if self.ttft_ms is None:
            self.ttft_ms = (time.perf_counter() - self.start_time) * 1000

    def mark_complete(self) -> None:
        self.latency_ms = (time.perf_counter() - self.start_time) * 1000

    def set_tokens(self, prompt: int = 0, completion: int = 0) -> None:
        self.prompt_tokens = prompt
        self.completion_tokens = completion

    def set_retrieved_chunks(self, page_ids: List[str]) -> None:
        self.retrieved_chunks = list(page_ids)


@contextmanager
def capture_telemetry(
    retrieved_chunks: Optional[List[str]] = None,
) -> Generator[TelemetryRecorder, None, None]:
    """
    Context manager for one QA call. Yields a TelemetryRecorder.
    Call mark_first_token() when the first token of the final answer is received.
    Call mark_complete() when the full answer is ready.
    """
    rec = TelemetryRecorder()
    if retrieved_chunks:
        rec.set_retrieved_chunks(retrieved_chunks)
    try:
        yield rec
    finally:
        rec.mark_complete()
