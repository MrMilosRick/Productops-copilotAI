"""
ARLC pipeline: single-question flow with retrieval, generation, telemetry, and submission entry.

Uses in-memory chunk list (from legal ingestion) or optional Django workspace.
TTFT = from question received to first token of final answer. Telemetry is mandatory.
"""

from __future__ import annotations

import re
import time
from typing import Any, Callable, Dict, List, Optional

from arlc.config import ARLCConfig
from arlc.schema import SubmissionEntry, build_telemetry
from arlc.telemetry import TelemetryRecorder


ANSWER_TYPES = ("boolean", "number", "date", "name", "names", "free_text")
UNANSWERABLE_PHRASES = (
    "not found in provided legal sources",
    "no information on this question",
    "information is not available",
    "not in corpus",
)


def _normalize_answer_by_type(value: Any, answer_type: str) -> Any:
    if value is None:
        return None
    if answer_type == "boolean":
        s = (str(value).strip().lower() if value is not None else "")
        if s in ("true", "yes", "1", "да"): return True
        if s in ("false", "no", "0", "нет"): return False
        return value
    if answer_type == "number":
        try:
            return float(value) if isinstance(value, str) and "." in value else int(value)
        except (ValueError, TypeError):
            return value
    if answer_type == "date":
        s = str(value).strip()
        if re.match(r"\d{4}-\d{2}-\d{2}", s):
            return s[:10]
        return s
    if answer_type == "names":
        if isinstance(value, list):
            return [str(x).strip() for x in value]
        return [str(value).strip()]
    return value


def _is_unanswerable(text: str) -> bool:
    if not (text or "").strip():
        return True
    t = text.strip().lower()
    return any(p in t for p in UNANSWERABLE_PHRASES) or t.startswith("there is no information")


def _extract_cited_indices(answer_text: str) -> set:
    cited = set()
    for m in re.findall(r"\[(\d+)\]", answer_text or ""):
        try:
            cited.add(int(m))
        except ValueError:
            continue
    return cited


def _chunks_to_page_ids(chunks: List[Dict[str, Any]], cited_1based: Optional[set] = None) -> List[str]:
    """
    Build competition-compliant retrieved_chunk_ids: unique, sorted page IDs as doc_slug_page (1-based).
    Uses normalize_page_id for consistent format (v14).
    """
    from arlc.config import normalize_page_id
    seen: set = set()
    out: List[str] = []
    for idx, c in enumerate(chunks, start=1):
        if cited_1based is not None and idx not in cited_1based:
            continue
        meta = c.get("meta") or {}
        slug = meta.get("doc_slug") or ""
        page = meta.get("page_num", 1)
        pid = normalize_page_id(slug, page)
        if pid not in seen:
            seen.add(pid)
            out.append(pid)
    return sorted(out)


def run_single_question(
    question_id: str,
    question: str,
    answer_type: str,
    chunks: List[Dict[str, Any]],
    *,
    config: Optional[ARLCConfig] = None,
    retrieve_fn: Optional[Callable[[str, int], List[Dict[str, Any]]]] = None,
    generate_fn: Optional[Callable[[str, List[Dict[str, Any]], bool], Dict[str, Any]]] = None,
) -> SubmissionEntry:
    """
    Run retrieval + generation for one question; return SubmissionEntry with telemetry.
    chunks: full list of chunks (text, meta: {page_num, doc_slug}).
    retrieve_fn(question, top_k) -> list of chunk dicts. If None, simple keyword filter.
    generate_fn(question, retrieved_chunks, streaming) -> {answer, prompt_tokens, completion_tokens}.
    """
    cfg = config or ARLCConfig.from_env()
    telemetry_rec = TelemetryRecorder()

    if retrieve_fn is None:
        def _simple_retrieve(q: str, k: int) -> List[Dict[str, Any]]:
            q_lower = (q or "").lower()
            words = [w for w in re.findall(r"[a-zA-Z0-9]+", q_lower) if len(w) >= 2]
            scored = []
            for c in chunks:
                text = (c.get("text") or "").lower()
                score = sum(1 for w in words if w in text)
                if score > 0:
                    scored.append((score, c))
            scored.sort(key=lambda x: (-x[0], x[1].get("chunk_index", 0)))
            return [c for _, c in scored[:k]]
        retrieve_fn = _simple_retrieve

    top_candidates = retrieve_fn(question, cfg.retrieval_top_k_candidates)
    if cfg.use_reranker and len(top_candidates) > cfg.retrieval_top_k_after_rerank:
        top_candidates = top_candidates[: cfg.retrieval_top_k_after_rerank]
    retrieved = top_candidates[: cfg.retrieval_top_k_after_rerank]

    telemetry_rec.set_retrieved_chunks(_chunks_to_page_ids(retrieved))

    if generate_fn is None:
        def _placeholder_gen(q: str, ret: List[Dict[str, Any]], streaming: bool) -> Dict[str, Any]:
            if not ret:
                return {"answer": "Not found in provided legal sources.", "prompt_tokens": 0, "completion_tokens": 0}
            parts = []
            for i, c in enumerate(ret[:3], start=1):
                parts.append((c.get("text") or "")[:200] + f" [{i}]")
            return {"answer": " ".join(parts), "prompt_tokens": 100, "completion_tokens": 50}
        generate_fn = _placeholder_gen

    t0 = time.perf_counter()
    gen_out = generate_fn(question, retrieved, cfg.streaming)
    answer_raw = gen_out.get("answer") or ""
    telemetry_rec.mark_first_token()
    telemetry_rec.mark_complete()
    telemetry_rec.set_tokens(gen_out.get("prompt_tokens", 0), gen_out.get("completion_tokens", 0))

    cited = _extract_cited_indices(answer_raw)
    if cited:
        used_page_ids = _chunks_to_page_ids(retrieved, cited_1based=cited)
    else:
        used_page_ids = _chunks_to_page_ids(retrieved)
    telemetry_rec.set_retrieved_chunks(used_page_ids)

    if _is_unanswerable(answer_raw) or (not retrieved and answer_type != "free_text"):
        answer_final = None
        used_page_ids = []
    else:
        answer_final = _normalize_answer_by_type(answer_raw, answer_type)
        if answer_type == "free_text" and isinstance(answer_final, str) and len(answer_final) > cfg.free_text_max_chars:
            answer_final = answer_final[: cfg.free_text_max_chars].rsplit(" ", 1)[0] + "…"

    ttft_ms = telemetry_rec.ttft_ms if telemetry_rec.ttft_ms is not None else (telemetry_rec.latency_ms or 0)
    latency_ms = telemetry_rec.latency_ms or 0

    return SubmissionEntry(
        question_id=question_id,
        answer=answer_final,
        retrieved_chunk_ids=used_page_ids,
        telemetry=build_telemetry(
            ttft_ms=ttft_ms,
            latency_ms=latency_ms,
            prompt_tokens=telemetry_rec.prompt_tokens,
            completion_tokens=telemetry_rec.completion_tokens,
            retrieved_chunks=used_page_ids,
        ),
        model_name=gen_out.get("model_name"),
        time_per_output_token_ms=gen_out.get("time_per_output_token_ms"),
    )
