from __future__ import annotations

import re
from typing import Any, Dict, List

from copilot.services.embeddings import embed_texts
from copilot.services.vector_retriever import vector_retrieve
from copilot.services.retriever import keyword_retrieve

_WORD_RE = re.compile(r"[A-Za-z0-9_]{2,}")


def _query_terms(question: str) -> List[str]:
    terms = [t.lower() for t in _WORD_RE.findall(question or "")]
    stop = {
        "what", "is", "inside", "the", "document", "return", "exact", "keyword",
        "a", "an", "and", "or", "to", "in", "of",
    }
    return [t for t in terms if t not in stop]


def hybrid_retrieve(workspace_id: int, question: str, top_k: int = 5, document_id: int | None = None) -> List[Dict[str, Any]]:
    top_k = max(1, int(top_k))
    expand = max(10, top_k * 5)

    terms = _query_terms(question)
    terms_set = set(terms)

    query_vec = embed_texts([question])[0] if (question or "").strip() else []
    v_res = vector_retrieve(workspace_id, query_vec, top_k=expand, document_id=document_id) if query_vec else []
    k_res = []
    if document_id is None:
            k_res = []  # HYBRID_DOCID_GUARD
    if document_id is None:
        k_res = keyword_retrieve(workspace_id, question, top_k=expand)

    merged: Dict[int, Dict[str, Any]] = {}

    def upsert(item: Dict[str, Any], source: str) -> None:
        cid = int(item.get("chunk_id"))
        if cid not in merged:
            merged[cid] = dict(item)
            merged[cid]["retriever_hint"] = source
        else:
            merged[cid]["retriever_hint"] = "hybrid"

        if source == "vector":
            merged[cid]["vector_score"] = float(item.get("score", 0.0) or 0.0)
            if "distance" in item:
                merged[cid]["distance"] = item.get("distance")
        elif source == "keyword":
            merged[cid]["keyword_score"] = float(item.get("score", 0.0) or 0.0)
            merged[cid]["matched_terms"] = item.get("matched_terms", merged[cid].get("matched_terms", []))

    for it in v_res:
        upsert(it, "vector")
    for it in k_res:
        upsert(it, "keyword")

    out: List[Dict[str, Any]] = []
    for it in merged.values():
        text = (it.get("snippet") or "").lower()
        hits = 0
        if terms_set and text:
            hits = sum(1 for t in terms_set if t in text)

        keyword_bonus = min(0.25, 0.05 * hits)

        v = float(it.get("vector_score", 0.0) or 0.0)
        k_raw = float(it.get("keyword_score", 0.0) or 0.0)

        # normalize keyword score to [0..1)
        k_norm = k_raw / (k_raw + 4.0) if k_raw > 0 else 0.0

        # weighted blend (vector dominates)
        final_score = (0.75 * v) + (0.25 * k_norm) + keyword_bonus

        it["keyword_bonus"] = keyword_bonus
        it["vector_score"] = v
        it["keyword_score"] = k_raw
        it["keyword_norm"] = k_norm
        it["final_score"] = final_score
        it["score"] = final_score

        out.append(it)

    out.sort(key=lambda x: float(x.get("final_score", 0.0) or 0.0), reverse=True)
    return out[:top_k]
