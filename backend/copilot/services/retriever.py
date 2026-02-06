import re
from typing import List, Dict, Any
from django.db.models import Q
from copilot.models import EmbeddingChunk

STOPWORDS = {
    "the","a","an","and","or","of","to","in","on","for","with","is","are","was","were",
    "this","that","it","as","at","by","from","be"
}

_WORD_BOUNDARY = r"(?<![0-9A-Za-zА-Яа-яЁё_])"
_WORD_BOUNDARY_END = r"(?![0-9A-Za-zА-Яа-яЁё_])"


def _word_boundary_regex(term: str) -> str:
    """Safe whole-word regex for Cyrillic/Latin/digits/underscore."""
    return _WORD_BOUNDARY + re.escape(term) + _WORD_BOUNDARY_END


def tokenize(query: str) -> List[str]:
    # Support Cyrillic + Latin + digits (Russian queries must work)
    words = re.findall(r"[0-9A-Za-zА-Яа-яЁё]+", (query or "").lower())
    # Minimal RU stopwords (small, safe set)
    ru_stop = {
        "и","а","но","или","что","это","как","к","ко","в","во","на","по","о","об","обо","от","до",
        "для","с","со","у","из","за","над","под","при","без","же","ли","то","не","ни","бы","мы","вы","я","он","она","они",
        "про","чем","эта","этот","эти","книга","книге","книгу","книги"
        ,
        # question words / fillers (RU) — they create false matches in keyword retrieval
        "кто","где","когда","почему","зачем","какой","какая","какое","какие",
        "какого","какой","какому","каким","какими","каком",
        "какую","какие","каких",
        "каков","какова","каково",
        "сколько","насколько",
        "какая-то","какой-то","какие-то",
        "какую-то","каких-то",
        "либо","или же",
        # meta words часто встречаются в вопросах и не помогают найти факты в тексте
        "автор","автора","авторы","автору","автором","авторе",
        "сказать","говорит","говорят","сказал","сказала",
        "пишет","написал","написала","упоминает","упомянул","упомянула",
        "фраза","фразу","фразы","цитата","цитату","цитаты",
        "имеет","значит"
    }
    stop = STOPWORDS | ru_stop
    words = [w for w in words if len(w) >= 3 and w not in stop]
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
        # If we cannot extract terms, do NOT return random chunks.
        return []

    # OR filter for any term (whole-word match)
    q_obj = Q()
    for t in terms:
        pat = _word_boundary_regex(t)
        q_obj |= Q(text__iregex=pat) | Q(document__title__iregex=pat)

    candidates = (
        EmbeddingChunk.objects
        .select_related("document")
        .filter(document__workspace_id=workspace_id)
        .filter(q_obj)
    )

    if document_id is not None:
        candidates = candidates.filter(document_id=int(document_id))

    candidates = candidates.order_by("-id")[:50]

    # Fallback: if term-match returns no candidates, return latest chunks (demo-friendly, still grounded).
    if not candidates.exists():
        fb = (
            EmbeddingChunk.objects
            .select_related("document")
            .filter(document__workspace_id=workspace_id)
        )
        if document_id is not None:
            fb = fb.filter(document_id=int(document_id))

        fb = fb.order_by("-id")[:top_k]

        out: List[Dict[str, Any]] = []
        for ch in fb:
            out.append({
                "document_id": ch.document_id,
                "document_title": ch.document.title,
                "chunk_id": ch.id,
                "chunk_index": ch.chunk_index,
                "matched_terms": [],
                "score": 0,
                "snippet": ch.text[:300],
                "text": ch.text,
            })
        return out


    results: List[Dict[str, Any]] = []
    for ch in candidates:
        text_raw = ch.text or ""
        title_raw = ch.document.title or ""

        matched = []
        score = 0
        for t in terms:
            pat = _word_boundary_regex(t)
            flags = re.IGNORECASE
            in_text = re.findall(pat, text_raw, flags)
            in_title = re.findall(pat, title_raw, flags)
            if in_text or in_title:
                matched.append(t)
                score += len(in_text) * 2
                score += len(in_title) * 4

        results.append({
            "document_id": ch.document_id,
            "document_title": ch.document.title,
            "chunk_id": ch.id,
            "chunk_index": ch.chunk_index,
            "matched_terms": matched,
            "score": score,
            "snippet": ch.text[:300],
            "text": ch.text,
        })

    results.sort(key=lambda r: (r["score"], r["chunk_id"]), reverse=True)
    return results[:top_k]
