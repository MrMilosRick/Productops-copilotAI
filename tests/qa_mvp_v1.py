#!/usr/bin/env python3
import json
import os
import subprocess
import sys
import time
from typing import Dict, Any, Tuple, List


BASE_URL = os.getenv("BASE_URL", "http://localhost:8001").rstrip("/")
API_UPLOAD_TEXT = f"{BASE_URL}/api/kb/upload_text/"
API_DOC_DETAIL = f"{BASE_URL}/api/kb/documents/{{document_id}}/"
API_ASK = f"{BASE_URL}/api/ask/"

OUT_PATH = "/tmp/qa_mvp_v1.jsonl"

LEGACY_NO_DOC_HEADINGS = (
    "Проверка по документу:",
    "Что именно отсутствует:",
    "Общий ответ (не из документа):",
    "Как получить точный ответ по документу:",
)


def _curl_json(url: str, method: str = "GET", headers: Dict[str, str] | None = None, data: Dict[str, Any] | None = None) -> Dict[str, Any]:
    headers = headers or {}
    h_parts = []
    for k, v in headers.items():
        h_parts.append("-H")
        h_parts.append(f"{k}: {v}")

    cmd = ["curl", "-sS", "-X", method, url, *h_parts]
    if data is not None:
        payload = json.dumps(data, ensure_ascii=False)
        cmd.extend(["-d", payload])

    raw = subprocess.check_output(cmd).decode("utf-8", "ignore")
    try:
        return json.loads(raw)
    except Exception as e:
        raise RuntimeError(f"Non-JSON response from {url}: {raw[:500]}") from e


def _idem(prefix: str) -> str:
    # deterministic enough + avoids collisions inside CI
    return f"{prefix}-{int(time.time()*1000)}"


def upload_text(title: str, content: str) -> int:
    resp = _curl_json(
        API_UPLOAD_TEXT,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Idempotency-Key": _idem("qa-upload"),
        },
        data={"title": title, "content": content},
    )
    doc_id = resp.get("document_id") or resp.get("id") or resp.get("doc_id")
    if not doc_id:
        raise RuntimeError(f"upload_text: no document id in response: {resp}")
    return int(doc_id)


def wait_embedded(document_id: int, timeout_s: int = 120) -> Dict[str, Any]:
    url = API_DOC_DETAIL.format(document_id=document_id)
    t0 = time.time()
    last = {}
    while True:
        last = _curl_json(
            url,
            method="GET",
            headers={
                "Content-Type": "application/json",
                "Idempotency-Key": _idem("qa-doc"),
            },
            data=None,
        )
        st = (last.get("status") or "").lower()
        if st == "embedded":
            return last
        if st in ("failed", "error"):
            raise RuntimeError(f"document {document_id} failed: {last}")
        if time.time() - t0 > timeout_s:
            raise RuntimeError(f"timeout waiting embedded for {document_id}, last={last}")
        time.sleep(0.5)


def ask(question: str, document_id: int | None, answer_mode: str = "deterministic") -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "question": question,
        "answer_mode": answer_mode,
        "retriever": "auto",
        "top_k": 5,
    }
    if document_id is not None:
        payload["document_id"] = int(document_id)

    return _curl_json(
        API_ASK,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Idempotency-Key": _idem("qa-ask"),
        },
        data=payload,
    )


def main() -> int:
    # minimal doc that reliably contains a plot-like structure
    # (keeps QA stable even if user has other docs in DB)
    seed_text = (
        "Глава 1. Это короткий тестовый документ для QA.\n"
        "Он описывает историю: героиня хочет изменить жизнь, ищет свободу и действует смело.\n"
        "Дальше — несколько абзацев для эмбеддингов.\n"
        "Глава 2. Конфликт: она сталкивается с ограничениями и выбирает путь действий.\n"
        "Глава 3. Итог: она принимает решение и меняет подход.\n"
    )

    doc_id = upload_text("QA_MVP_DOC", seed_text)
    _ = wait_embedded(doc_id, timeout_s=120)

    intro_text = (
        "Меня зовут Арина. Немного фактов обо мне: я веду команду продуктовых аналитиков, люблю документировать процессы "
        "и рассказывать о подходах к работе. О себе я говорю честно и перечисляю сильные стороны."
    )
    intro_doc_id = upload_text("QA_MVP_INTRO", intro_text)
    _ = wait_embedded(intro_doc_id, timeout_s=120)

    tests: List[Tuple[str, str]] = [
        # DOC (expect doc_rag + sources>0)
        ("DOC", "приведи точную цитату про «героиня хочет изменить жизнь»"),
        ("DOC", "что говорится про конфликт и выбор?"),
        ("DOC", "какое решение принимает героиня?"),
        ("DOC", "где в тексте упоминается свобода?"),
        ("DOC", "какие главы есть в документе?"),
        ("DOC", "приведи точную цитату: «она принимает решение»"),
        ("DOC_INTRO", "Что автор говорит о себе?"),
        # SUMMARY (expect route summary + sources>0)
        ("SUM", "о чем книга?"),
        # NO-DOC (expect general + sources==0)
        ("NO", "как выбрать велосипед?"),
        ("NO", "как выбрать ноутбук?"),
        ("NO", "как улучшить сон?"),
        ("NO", "как выбрать кроссовки для бега?"),
        ("NO", "как составить резюме?"),
        ("NO", "как поднять docker compose проект на сервере?"),
    ]

    rows = []
    ok = 0
    for kind, q in tests:
        if kind in ("DOC", "SUM"):
            resp = ask(q, document_id=doc_id, answer_mode="deterministic")
        elif kind == "DOC_INTRO":
            resp = ask(q, document_id=intro_doc_id, answer_mode="deterministic")
        else:
            resp = ask(q, document_id=None, answer_mode="deterministic")

        route = resp.get("route")
        sources_n = len(resp.get("sources") or [])
        retr = resp.get("retriever_used")
        run_id = resp.get("run_id")
        answer = resp.get("answer") or ""
        has_disclaimer = "В этом документе нет информации" in answer
        has_hint = "Если вам нужен ответ именно по документу" in answer
        has_legacy = any(h in answer for h in LEGACY_NO_DOC_HEADINGS)
        new_general_contract = has_disclaimer and has_hint and not has_legacy

        # expectations
        if kind in ("DOC", "DOC_INTRO"):
            # DOC tests: two valid outcomes:
            # 1) doc_rag with sources (grounded in document)
            # 2) general with zero sources ONLY if answer explicitly says "not in document"
            if route == "doc_rag":
                good = (sources_n > 0)
            elif route == "general":
                good = (sources_n == 0 and new_general_contract)
            else:
                good = False
        elif kind == "SUM":
            good = (route == "summary") and (sources_n > 0)
        else:
            good = (route == "general") and (sources_n == 0) and new_general_contract

        ok += int(good)
        rows.append(
            {
                "kind": kind,
                "q": q,
                "document_id": doc_id if kind in ("DOC", "SUM") else None,
                "run_id": run_id,
                "route": route,
                "sources_n": sources_n,
                "retriever_used": retr,
            }
        )
        print(
            f'{("✅" if good else "❌")} {kind:3} route={str(route):<7} '
            f"src={sources_n:<2} run={run_id} retr={str(retr):<8} :: {q}"
        )
        time.sleep(0.12)

    with open(OUT_PATH, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    print("wrote", OUT_PATH)
    print("PASS", ok, "/", len(rows))
    return 0 if ok == len(rows) else 1


if __name__ == "__main__":
    raise SystemExit(main())
