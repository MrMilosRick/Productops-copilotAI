#!/usr/bin/env python3
"""

Reads questions.json (list of {id, question, answer_type}), calls POST /api/ask/
for each question, collects results into answers.json in submission format.

- Retry once on timeout; null answer on failure.
- Progress: [42/100] boolean | 743ms | Fursa Consulting
- Schema: Adapt to starter kit after March 9 webinar (exact schema in starter kit).

Entry point: API http://127.0.0.1:18001/api/ask/

"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import requests

# --- Config (override via CLI) ---
DEFAULT_API_BASE = "http://127.0.0.1:18001"
DEFAULT_ASK_PATH = "/api/ask/"
DEFAULT_QUESTIONS_FILE = "questions.json"
DEFAULT_ANSWERS_FILE = "answers.json"
DEFAULT_TIMEOUT = 120

# --- Answer schema (align with starter kit after March 9) ---
# Organizer rules (from Rag_production.txt): retrieved_chunk_ids = only cited pages,
# format docid_page (1-based, e.g. abc123_3). Boolean: JSON true/false. Date: ISO YYYY-MM-DD.
# Number: numeric only. Unanswerable: answer null, retrieved_chunk_ids [].
ANSWER_SCHEMA = {
    "id": str,
    "answer": str | bool | int | float | None,
    "ttft": float | None,
    "time_per_output_token": float | None,
    "total_response_time": float | None,
    "retrieved_chunk_ids": list[str],
    "input_tokens": int | None,
    "output_tokens": int | None,
    "model": str | None,
}


def load_questions(path: Path) -> list[dict]:
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, list):
        raise ValueError("questions.json must be a list of {id, question, answer_type}")
    return data


def ask_one(
    session: requests.Session,
    base_url: str,
    path: str,
    question_id: str,
    question: str,
    answer_type: str,
    timeout: int,
) -> dict:
    """
    POST one question to /api/ask/. Returns answer dict for answers.json.
    On failure: returns same id with answer null and telemetry nulls; no exception.
    """
    url = f"{base_url.rstrip('/')}{path}"
    payload = {"question": question, "answer_type": answer_type, "id": question_id}
    headers = {"Content-Type": "application/json", "Idempotency-Key": question_id}

    def do_request() -> requests.Response:
        return session.post(url, json=payload, headers=headers, timeout=timeout)

    try:
        resp = do_request()
    except requests.exceptions.Timeout:
        try:
            resp = do_request()
        except requests.exceptions.Timeout:
            return _null_answer(question_id, "timeout")
        except Exception as e:
            return _null_answer(question_id, str(e))
    except Exception as e:
        return _null_answer(question_id, str(e))

    if not resp.ok:
        return _null_answer(question_id, f"HTTP {resp.status_code}")

    try:
        body = resp.json()
    except Exception:
        return _null_answer(question_id, "invalid JSON")

    return _build_answer(question_id, body)


def _null_answer(question_id: str, reason: str = "failure") -> dict:
    return {
        "id": question_id,
        "answer": None,
        "ttft": None,
        "time_per_output_token": None,
        "total_response_time": None,
        "retrieved_chunk_ids": [],
        "input_tokens": None,
        "output_tokens": None,
        "model": None,
        "_error": reason,
    }


def _build_answer(question_id: str, body: dict) -> dict:
    """Map API response to submission schema. Adapt when starter kit schema is known."""
    telemetry = body.get("telemetry") or {}
    ttft_ms = telemetry.get("ttft_ms") or body.get("ttft_ms")
    total_ms = (
        telemetry.get("total_time_ms")
        or telemetry.get("total_response_time_ms")
        or body.get("total_time_ms")
        or body.get("total_response_time_ms")
    )
    time_per_token = telemetry.get("time_per_output_token_ms") or body.get("time_per_output_token_ms")

    answer = body.get("answer")
    # retrieved_chunk_ids: only cited pages, format docid_page (1-based). Accept either key or sources.
    chunk_ids = list(body.get("retrieved_chunk_ids") or [])
    if not chunk_ids and body.get("sources"):
        for s in body.get("sources") or []:
            if isinstance(s, str) and "_" in s:
                chunk_ids.append(s)
            elif isinstance(s, dict):
                doc = s.get("doc_id") or s.get("document_id") or s.get("source_id", "")
                page = s.get("page") or s.get("page_num")
                if doc and page is not None:
                    chunk_ids.append(f"{doc}_{int(page)}")

    out = {
        "id": question_id,
        "answer": answer if answer is not None else None,
        "ttft": round(ttft_ms / 1000.0, 4) if ttft_ms is not None else None,
        "time_per_output_token": round(time_per_token / 1000.0, 6) if time_per_token is not None else None,
        "total_response_time": round(total_ms / 1000.0, 4) if total_ms is not None else None,
        "retrieved_chunk_ids": chunk_ids,
        "input_tokens": body.get("input_tokens") or telemetry.get("input_tokens"),
        "output_tokens": body.get("output_tokens") or telemetry.get("output_tokens"),
        "model": body.get("model") or telemetry.get("model"),
    }
    return out


def run(
    questions_path: Path,
    answers_path: Path,
    api_base: str = DEFAULT_API_BASE,
    ask_path: str = DEFAULT_ASK_PATH,
    timeout: int = DEFAULT_TIMEOUT,
) -> list[dict]:
    questions = load_questions(questions_path)
    total = len(questions)
    answers = []

    with requests.Session() as session:
        for i, q in enumerate(questions):
            qid = q.get("id") or f"q{i+1:03d}"
            question = q.get("question", "")
            answer_type = q.get("answer_type", "string")

            result = ask_one(
                session=session,
                base_url=api_base,
                path=ask_path,
                question_id=qid,
                question=question,
                answer_type=answer_type,
                timeout=timeout,
            )
            answers.append(result)

            # Progress: [42/100] boolean | 743ms | Fursa Consulting
            ttft_s = result.get("total_response_time") or result.get("ttft")
            ms_str = f"{int(ttft_s * 1000)}ms" if ttft_s is not None else "—"
            ans_preview = (result.get("answer") or "—")[:40]
            if result.get("_error"):
                ans_preview = f"[{result['_error']}]"
            print(f"[{i+1}/{total}] {answer_type} | {ms_str} | {ans_preview}")

    # Remove internal keys before writing
    for a in answers:
        a.pop("_error", None)

    answers_path.parent.mkdir(parents=True, exist_ok=True)
    with open(answers_path, "w", encoding="utf-8") as f:
        json.dump(answers, f, indent=2, ensure_ascii=False)

    return answers


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate answers.json from questions.json via /api/ask/")
    parser.add_argument("--questions", "-q", default=DEFAULT_QUESTIONS_FILE, help="Path to questions.json")
    parser.add_argument("--output", "-o", default=DEFAULT_ANSWERS_FILE, help="Path to answers.json")
    parser.add_argument("--api-base", default=DEFAULT_API_BASE, help="API base URL")
    parser.add_argument("--ask-path", default=DEFAULT_ASK_PATH, help="Ask endpoint path")
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT, help="Request timeout (seconds)")
    args = parser.parse_args()

    root = Path(__file__).resolve().parent.parent
    questions_path = Path(args.questions) if Path(args.questions).is_absolute() else root / args.questions
    answers_path = Path(args.output) if Path(args.output).is_absolute() else root / args.output

    if not questions_path.exists():
        print(f"Error: questions file not found: {questions_path}", file=sys.stderr)
        return 1

    run(
        questions_path=questions_path,
        answers_path=answers_path,
        api_base=args.api_base,
        ask_path=args.ask_path,
        timeout=args.timeout,
    )
    print(f"Wrote {answers_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
