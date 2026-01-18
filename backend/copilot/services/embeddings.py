import hashlib
import os
import random
from typing import List

DIM = int(os.getenv("EMBEDDINGS_DIM", "1536"))
PROVIDER = os.getenv("EMBEDDINGS_PROVIDER", "stub").lower()

def _stub_embed_one(text: str, dim: int) -> List[float]:
    """
    Deterministic pseudo-embedding:
    same text -> same vector (good for reproducible tests).
    """
    h = hashlib.sha256((text or "").encode("utf-8")).digest()
    seed = int.from_bytes(h[:8], "big")
    rnd = random.Random(seed)
    return [rnd.uniform(-1.0, 1.0) for _ in range(dim)]

def embed_texts(texts: List[str]) -> List[List[float]]:
    if PROVIDER == "stub":
        return [_stub_embed_one(t, DIM) for t in texts]
    raise RuntimeError(f"Unsupported EMBEDDINGS_PROVIDER={PROVIDER!r} (only 'stub' for now)")
