import re
from typing import List, Dict, Any

def normalize_text(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()

def chunk_text(
    text: str,
    max_chars: int = 3500,
    overlap_chars: int = 300,
) -> List[Dict[str, Any]]:
    """
    MVP chunker: char-based, stable & fast.
    Later we can switch to token-based chunking.
    """
    text = normalize_text(text)
    if not text:
        return []

    chunks = []
    start = 0
    idx = 0
    n = len(text)

    while start < n:
        end = min(start + max_chars, n)

        # try to cut on paragraph boundary
        cut = text.rfind("\n\n", start, end)
        if cut != -1 and cut > start + int(max_chars * 0.6):
            end = cut

        chunk = text[start:end].strip()
        if chunk:
            chunks.append({
                "chunk_index": idx,
                "text": chunk,
                "meta": {"start": start, "end": end, "max_chars": max_chars, "overlap_chars": overlap_chars},
            })
            idx += 1

        if end >= n:
            break

        start = max(0, end - overlap_chars)

    return chunks
