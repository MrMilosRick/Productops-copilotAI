import re

def normalize_idempotency_key(key: str) -> str:
    k = (key or "").strip()
    # allow letters/digits/._- up to 128
    k = re.sub(r"[^a-zA-Z0-9._-]+", "-", k)
    return k[:128]
