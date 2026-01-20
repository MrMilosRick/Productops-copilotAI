#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import sys
import time
import urllib.request
import urllib.error
from typing import Any, Dict, Optional, Tuple

BASE_URL = os.environ.get("BASE_URL", "http://localhost:8001").rstrip("/")
TIMEOUT_S = int(os.environ.get("SMOKE_TIMEOUT", "90"))

UNICORN = f"UNICORN_SMOKE_{int(time.time())}"
DOC_TEXT = f"alpha alpha. {UNICORN}. omega omega."

def die(msg: str) -> None:
    print(f"\nFAIL: {msg}")
    sys.exit(1)

def ok(msg: str) -> None:
    print(f"OK: {msg}")

def http(method: str, path: str, *, json_body: Optional[Dict[str, Any]] = None, headers: Optional[Dict[str, str]] = None) -> Tuple[int, str]:
    url = f"{BASE_URL}{path}"
    data = None
    req_headers = {"Accept": "application/json"}
    if headers:
        req_headers.update(headers)

    if json_body is not None:
        data = json.dumps(json_body).encode("utf-8")
        req_headers["Content-Type"] = "application/json"

    req = urllib.request.Request(url, data=data, headers=req_headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            return resp.getcode(), body
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace") if e.fp else ""
        return e.code, body
    except Exception as e:
        return 0, str(e)

def get_json(method: str, path: str, **kwargs) -> Tuple[int, Any, str]:
    code, text = http(method, path, **kwargs)
    if not text:
        return code, None, text
    try:
        return code, json.loads(text), text
    except Exception:
        return code, None, text

def wait_health() -> None:
    start = time.time()
    while time.time() - start < TIMEOUT_S:
        code, data, raw = get_json("GET", "/api/health/")
        if code == 200:
            ok("Service healthy")
            return
        time.sleep(1)
    die(f"Service not healthy after {TIMEOUT_S}s")

def try_upload_text() -> Tuple[int, Dict[str, Any]]:
    candidates = [
        "/api/kb/upload_text/",
        "/api/kb/upload_text",
        "/api/documents/upload_text/",
        "/api/documents/upload_text",
        "/api/documents/",
        "/api/kb/documents/",
    ]
    payload = {"title": f"Smoke Doc {UNICORN}", "content": DOC_TEXT}

    last_err = None
    for path in candidates:
        code, data, raw = get_json("POST", path, json_body=payload)
        if code == 0:
            last_err = f"{path} -> transport error: {raw}"
            continue
        if code >= 400:
            last_err = f"{path} -> {code} {raw[:200]}"
            continue
        if isinstance(data, dict):
            for key in ("document_id", "id"):
                if isinstance(data.get(key), int):
                    return int(data[key]), data
            doc = data.get("document")
            if isinstance(doc, dict) and isinstance(doc.get("id"), int):
                return int(doc["id"]), data
        last_err = f"{path} -> {code} but no document_id in response: {data or raw[:200]}"
    die(
        "Cannot upload text doc via known endpoints.\n"
        f"Tried: {', '.join(candidates)}\n"
        f"Last error: {last_err}"
    )

def try_get_document(doc_id: int) -> Dict[str, Any]:
    candidates = [
        f"/api/documents/{doc_id}/",
        f"/api/documents/{doc_id}",
        f"/api/kb/documents/{doc_id}/",
        f"/api/kb/documents/{doc_id}",
    ]
    last_err = None
    for path in candidates:
        code, data, raw = get_json("GET", path)
        if code == 200 and isinstance(data, dict):
            return data
        last_err = f"{path} -> {code} {raw[:200]}"
    die(
        f"Cannot fetch document {doc_id} via known endpoints.\n"
        f"Tried: {', '.join(candidates)}\n"
        f"Last error: {last_err}"
    )

def extract_status(doc_json: Dict[str, Any]) -> Optional[str]:
    for k in ("status", "state", "processing_status"):
        v = doc_json.get(k)
        if isinstance(v, str):
            return v
    return None

def extract_chunk_count(doc_json: Dict[str, Any]) -> Optional[int]:
    for k in ("chunk_count", "chunks_count"):
        v = doc_json.get(k)
        if isinstance(v, int):
            return v
    return None

def wait_document_ready(doc_id: int) -> Dict[str, Any]:
    want = {"embedded", "ready", "processed"}
    start = time.time()
    while time.time() - start < TIMEOUT_S:
        doc = try_get_document(doc_id)
        st = (extract_status(doc) or "").lower()
        cc = extract_chunk_count(doc)
        if st in want or (cc is not None and cc > 0 and st not in {"error", "failed"}):
            ok(f"Document ready (status={st or 'n/a'}, chunk_count={cc})")
            return doc
        if st in {"error", "failed"}:
            die(f"Document processing failed: {doc}")
        time.sleep(1)
    die(f"Document {doc_id} not ready after {TIMEOUT_S}s")

def ask(question: str, doc_id: int, *, answer_mode: str, idem_key: Optional[str] = None, top_k: int = 1) -> Tuple[int, Any, str]:
    payload = {
        "question": question,
        "retriever": "auto",
        "top_k": top_k,
        "document_id": doc_id,
        "answer_mode": answer_mode,
    }
    headers = {}
    if idem_key:
        headers["Idempotency-Key"] = idem_key
    return get_json("POST", "/api/ask/", json_body=payload, headers=headers)

def main() -> None:
    print(f"BASE_URL={BASE_URL}")
    wait_health()

    doc_id, _ = try_upload_text()
    ok(f"Uploaded doc_id={doc_id}")
    wait_document_ready(doc_id)

    q = "What is the unicorn id?"
    code, data, raw = ask(q, doc_id, answer_mode="langchain_rag")
    if code != 200 or not isinstance(data, dict):
        die(f"/api/ask failed: {code} {raw[:400]}")

    if not data.get("answer"):
        die(f"Empty answer: {data}")
    if not isinstance(data.get("sources"), list) or len(data["sources"]) < 1:
        die(f"Missing sources: {data}")

    answer_text = str(data.get("answer", ""))
    snippets = " ".join([str(s.get("snippet", "")) for s in data.get("sources", []) if isinstance(s, dict)])
    if UNICORN not in answer_text and UNICORN not in snippets:
        die(f"Unicorn token not found. UNICORN={UNICORN}\nanswer={answer_text}\nsnippets={snippets}")

    ok(f"Ask OK: run_id={data.get('run_id')} llm_used={data.get('llm_used')} answer_mode={data.get('answer_mode')}")

    idem = f"smoke-idem-{int(time.time())}"
    c1, d1, r1 = ask(q, doc_id, answer_mode="langchain_rag", idem_key=idem, top_k=1)
    if c1 != 200 or not isinstance(d1, dict):
        die(f"Idem first call failed: {c1} {r1[:300]}")

    c2, d2, r2 = ask(q, doc_id, answer_mode="langchain_rag", idem_key=idem, top_k=1)
    if c2 != 200 or not isinstance(d2, dict):
        die(f"Idem replay call failed: {c2} {r2[:300]}")

    if d1.get("run_id") != d2.get("run_id"):
        die(f"Replay should return same run_id. got {d1.get('run_id')} vs {d2.get('run_id')}")
    if not d2.get("idempotent_replay"):
        die(f"Replay should set idempotent_replay=true. got: {d2}")
    ok(f"Idempotent replay OK: run_id={d2.get('run_id')}")

    c3, d3, r3 = ask(q, doc_id, answer_mode="langchain_rag", idem_key=idem, top_k=2)
    if c3 != 409:
        die(f"Expected 409 on idem conflict, got {c3}: {r3[:300]}")
    ok("Idempotency conflict (409) OK")

    print("\nSMOKE OK")

if __name__ == "__main__":
    main()
