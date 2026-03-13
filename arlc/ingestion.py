"""
Legal-aware chunking for ARLC: token-based sizes, section preservation, page-level metadata.

Rules (from spec): 300–500 tokens per chunk, 50 token overlap, preserve legal sections.
Chunk ID for submission: doc_slug_page (1-based page).
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


def _approx_tokens(text: str) -> int:
    """Approximate token count (4 chars per token if no tiktoken)."""
    try:
        import tiktoken
        enc = tiktoken.get_encoding("cl100k_base")
        return len(enc.encode(text))
    except Exception:
        return max(0, (len((text or "").strip()) + 3) // 4)


SECTION_PATTERNS = [
    re.compile(r"\n\s*(Article\s+\d+[.:])\s*", re.IGNORECASE),
    re.compile(r"\n\s*(Section\s+\d+[.:])\s*", re.IGNORECASE),
    re.compile(r"\n\s*(Clause\s+\d+[.:])\s*", re.IGNORECASE),
    re.compile(r"\n\s*(Part\s+[IVXLCDM0-9]+[.:])\s*", re.IGNORECASE),
    re.compile(r"\n\s*(\d+\.\s+[A-Z])"),
]


def _find_section_breaks(text: str) -> List[int]:
    positions = [0]
    for pat in SECTION_PATTERNS:
        for m in pat.finditer(text):
            positions.append(m.start())
    positions.sort()
    return positions


def chunk_text_legal(
    text: str,
    max_tokens: int = 450,
    overlap_tokens: int = 50,
    page_num: int = 1,
    doc_slug: str = "",
) -> List[Dict[str, Any]]:
    """Split text into token-sized chunks with overlap, preferring section boundaries."""
    text = (text or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if not text:
        return []

    section_breaks = _find_section_breaks(text)
    chunks: List[Dict[str, Any]] = []
    start = 0
    idx = 0
    n = len(text)

    while start < n:
        target_end = min(start + max_tokens * 4, n)
        end = min(target_end, n)
        para = text.rfind("\n\n", start, end)
        section_at = None
        for b in section_breaks:
            if start < b <= end:
                section_at = b
                break
        if section_at is not None and section_at > start + (max_tokens // 2) * 4:
            end = section_at
        elif para != -1 and para > start + int(max_tokens * 0.6 * 4):
            end = para

        chunk_text = text[start:end].strip()
        if chunk_text:
            tokens_approx = _approx_tokens(chunk_text)
            chunks.append({
                "chunk_index": idx,
                "text": chunk_text,
                "meta": {
                    "page_num": page_num,
                    "doc_slug": doc_slug,
                    "start": start,
                    "end": end,
                    "tokens_approx": tokens_approx,
                },
            })
            idx += 1
        if end >= n:
            break
        start = max(0, end - overlap_tokens * 4)
    return chunks


def extract_text_by_page_pdf(pdf_path: str) -> List[Tuple[int, str]]:
    """Extract text per page from a PDF. Returns list of (page_num_1based, text)."""
    try:
        from pdfminer.high_level import extract_pages
        from pdfminer.layout import LTTextContainer
    except ImportError:
        try:
            import pypdf
            reader = pypdf.PdfReader(pdf_path)
            return [(i + 1, (p.extract_text() or "").strip()) for i, p in enumerate(reader.pages)]
        except Exception as e:
            raise RuntimeError(f"PDF extraction failed: {e}") from e

    result: List[Tuple[int, str]] = []
    for page_num, page in enumerate(extract_pages(pdf_path), start=1):
        parts = []
        for obj in page:
            if isinstance(obj, LTTextContainer):
                parts.append(obj.get_text())
        result.append((page_num, "\n".join(parts).replace("\x00", "").strip()))
    return result


def ingest_document_legal(
    pdf_path: str,
    doc_slug: Optional[str] = None,
    max_tokens: int = 450,
    overlap_tokens: int = 50,
) -> List[Dict[str, Any]]:
    """Ingest one PDF: per-page extraction, then legal chunking per page."""
    path = Path(pdf_path)
    slug = doc_slug or path.stem
    pages = extract_text_by_page_pdf(str(path))
    all_chunks: List[Dict[str, Any]] = []
    global_idx = 0
    for page_num, page_text in pages:
        if not page_text.strip():
            all_chunks.append({
                "chunk_index": global_idx,
                "text": "",
                "meta": {"page_num": page_num, "doc_slug": slug},
            })
            global_idx += 1
            continue
        page_chunks = chunk_text_legal(
            page_text,
            max_tokens=max_tokens,
            overlap_tokens=overlap_tokens,
            page_num=page_num,
            doc_slug=slug,
        )
        for c in page_chunks:
            c["chunk_index"] = global_idx
            global_idx += 1
        all_chunks.extend(page_chunks)
    return all_chunks


def page_ids_from_chunks(chunks: List[Dict[str, Any]]) -> List[str]:
    """Unique page IDs doc_slug_page (1-based), sorted. Uses v14 format (normalize_page_id)."""
    from arlc.config import normalize_page_id
    seen: set = set()
    out: List[str] = []
    for c in chunks:
        meta = c.get("meta") or {}
        slug = meta.get("doc_slug") or ""
        page = meta.get("page_num", 1)
        pid = normalize_page_id(slug, page)
        if pid not in seen:
            seen.add(pid)
            out.append(pid)
    return sorted(out)
