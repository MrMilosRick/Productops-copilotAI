import re
from typing import List, Dict, Any
from django.db.models import Q
from copilot.models import EmbeddingChunk

STOPWORDS = {
    "the","a","an","and","or","of","to","in","on","for","with","is","are","was","were",
    "this","that","it","as","at","by","from","be"
}

def tokenize(query: str) -> List[str]:
    words = re.findall(r"[a-zA-Z0-9]+", (query or "").lower())
    words = [w for w in words if len(w) >= 3 and w not in STOPWORDS]
    # dedupe preserving order
    seen = set()
    out = []
    for w in words:
        if w not in seen:
            seen.add(w)
            out.append(w)
    return out

def keyword_retrieve(workspace_id: int, query: str, top_k: int = 5, document_id: int | None = None) -> List[Dict[str, Any]]:
    """
    MVP keyword retrieval:
    - split query into terms
    - OR-match over chunks/text/title
    - rank in python by term occurrences
    """
    q_raw = (query or "").strip()
    if not q_raw:
        return []

    terms = tokenize(q_raw)
    if not terms:
        # fallback: try raw query as-is
        terms = [q_raw.lower()]

    # OR filter for any term
    q_obj = Q()
    for t in terms:
        q_obj |= Q(text__icontains=t) | Q(document__title__icontains=t)

    candidates = (
        EmbeddingChunk.objects
        .select_related("document")
        .filter(document__workspace_id=workspace_id)
        .filter(q_obj)
    )

    if document_id is not None:
        candidates = candidates.filter(document_id=int(document_id))

    candidates = candidates.order_by("-id")[:50]

    results: List[Dict[str, Any]] = []
    for ch in candidates:
        text_l = ch.text.lower()
        title_l = ch.document.title.lower()

        matched = [t for t in terms if (t in text_l) or (t in title_l)]
        score = 0
        for t in matched:
            score += text_l.count(t) * 2
            score += title_l.count(t) * 4

        results.append({
            "document_id": ch.document_id,
            "document_title": ch.document.title,
            "chunk_id": ch.id,
            "chunk_index": ch.chunk_index,
            "matched_terms": matched,
            "score": score,
            "snippet": ch.text[:300],
        })

    results.sort(key=lambda r: (r["score"], r["chunk_id"]), reverse=True)
    return results[:top_k]
