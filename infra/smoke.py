from __future__ import annotations
import json, time, sys, urllib.request

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8001"
BASE = BASE.rstrip("/")

def req(method: str, path: str, data: dict | None = None, timeout: int = 15):
    url = f"{BASE}{path}"
    body = None
    headers = {"Accept": "application/json"}
    if data is not None:
        body = json.dumps(data).encode("utf-8")
        headers["Content-Type"] = "application/json"
    r = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            raw = resp.read()
            ct = resp.headers.get("Content-Type", "")
            if "application/json" not in ct:
                txt = raw.decode("utf-8", "replace")
                raise SystemExit(f"NON-JSON RESPONSE from {url}\nContent-Type: {ct}\n{txt[:800]}")
            return json.loads(raw.decode("utf-8"))
    except Exception as e:
        raise SystemExit(f"REQUEST FAILED {method} {url}: {e}")

def wait_health():
    for _ in range(120):
        try:
            req("GET", "/api/health/", None, timeout=5)
            print("web ready")
            return
        except SystemExit:
            time.sleep(1)
    raise SystemExit("web not ready")

def poll_embedded(doc_id: int):
    for _ in range(120):
        d = req("GET", f"/api/kb/documents/{doc_id}/")
        status = (d.get("status") or "").lower()
        chunk_count = int(d.get("chunk_count") or 0)
        if status in ("embedded", "done", "ready") or chunk_count > 0:
            print("doc embedded")
            return
        time.sleep(1)
    raise SystemExit("doc not embedded")

def main():
    wait_health()

    keyword = f"RAGTEST_{int(time.time())}"
    title = f"E2E Doc {keyword}"
    content = f"SMOKE_E2E UNIQUE_DOC={title} UNIQUE_KEY={keyword}. Keyword: {keyword}. Keyword: {keyword}. End."
    up = req("POST", "/api/kb/upload_text/", {"title": title, "content": content})
    doc_id = int(up["document_id"])
    print(f"uploaded doc_id={doc_id} keyword={keyword}")

    poll_embedded(doc_id)
    q = f"From document titled '{title}' return the exact keyword: {keyword}. Return only the keyword."
    a1 = req("POST", "/api/ask/", {"question": q, "retriever": "hybrid", "top_k": 3, "document_id": doc_id})
    run1 = int(a1["run_id"])
    used1 = a1.get("retriever_used")
    if used1 != "hybrid":
        raise SystemExit(f"expected retriever_used=hybrid, got {used1}")

    a2 = req("POST", "/api/ask/", {"question": q, "retriever": "auto", "top_k": 3, "document_id": doc_id})
    # --- assertions: top-1 must contain our unique keyword ---
    sources = (a2 or {}).get("sources") or []
    if not sources:
        raise SystemExit("ask returned empty sources")

    top = sources[0]
    top_snip = (top.get("snippet") or "")
    if keyword not in top_snip:
        raise SystemExit(f"top-1 snippet does not contain our keyword: {keyword} | got: {top_snip!r}")
    # --- /assertions ---

    run2 = int(a2["run_id"])
    used2 = a2.get("retriever_used")
    if used2 != "hybrid":
        raise SystemExit(f"expected retriever_used=hybrid for auto, got {used2}")

    runs = req("GET", "/api/runs/")
    if not isinstance(runs, list) or not runs:
        raise SystemExit("runs list empty / invalid")

    steps = req("GET", f"/api/runs/{run2}/steps/")
    if not isinstance(steps, list) or not steps:
        raise SystemExit("steps list empty / invalid")
    if not any(s.get("name") == "retrieve_context" for s in steps):
        raise SystemExit("retrieve_context step not found in steps")

    print("SMOKE OK:", {"hybrid": used1, "auto": used2, "run_id": run2, "steps": len(steps)})

if __name__ == "__main__":
    main()
