from typing import Any, Dict, List

from pgvector.django import CosineDistance
from copilot.models import EmbeddingChunk


def vector_retrieve(workspace_id: int, query_vector: List[float], top_k: int = 5, document_id: int | None = None) -> List[Dict[str, Any]]:
    """
    Vector retrieval using pgvector cosine distance.
    Returns sources in the same shape as keyword_retrieve().
    score: similarity in [0..1] (1 is best). We compute score = 1 - distance.
    """
    if not query_vector:
        return []
    base_qs = (
        EmbeddingChunk.objects
        .select_related("document")
        .filter(document__workspace_id=workspace_id)
        .exclude(embedding__isnull=True)
    )

    if document_id is not None:
        base_qs = base_qs.filter(document_id=int(document_id))

    qs = (
        base_qs
        .annotate(distance=CosineDistance("embedding", query_vector))
        .order_by("distance")[: max(1, int(top_k))]
    )

    results: List[Dict[str, Any]] = []
    for ch in qs:
        dist = float(getattr(ch, "distance", 1.0) or 1.0)
        score = 1.0 - (dist / 2.0)
        results.append({
            "document_id": ch.document_id,
            "document_title": ch.document.title,
            "chunk_id": ch.id,
            "chunk_index": ch.chunk_index,
            "matched_terms": [],
            "distance": dist,
            "score": score,
            "snippet": ch.text[:300],
        })

    return results
