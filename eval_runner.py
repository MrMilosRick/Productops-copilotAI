"""
Legal RAG - Eval Runner
Runs all questions from questions.json against /api/ask/
and saves results to eval_results.json

Usage:
    python eval_runner.py --questions questions.json --output eval_results.json
    python eval_runner.py --questions questions.json --limit 10  # test first 10
"""

import json
import time
import argparse
import requests
from pathlib import Path
from datetime import datetime


API_URL = "http://127.0.0.1:18001/api/ask/"


def ask(question: str, timeout: int = 30) -> dict:
    try:
        t0 = time.time()
        resp = requests.post(
            API_URL,
            json={"question": question},
            timeout=timeout,
        )
        elapsed = time.time() - t0
        data = resp.json()
        data["_http_latency_ms"] = round(elapsed * 1000)
        return data
    except Exception as e:
        return {"error": str(e), "_http_latency_ms": 0}


def evaluate(questions: list, limit: int = None) -> dict:
    if limit:
        questions = questions[:limit]

    total = len(questions)
    results = []

    # Counters
    has_chunks = 0
    has_answer = 0
    errors = 0
    by_type = {}

    print(f"\n{'='*60}")
    print(f"EVAL RUN — {total} questions")
    print(f"Started: {datetime.now().strftime('%H:%M:%S')}")
    print(f"{'='*60}\n")

    for i, q in enumerate(questions, 1):
        qid = q["id"]
        question = q["question"]
        answer_type = q.get("answer_type", "unknown")

        print(f"[{i:3}/{total}] {answer_type:10} | {question[:70]}")

        resp = ask(question)

        # Extract key fields
        answer = resp.get("answer", "")
        chunk_ids = resp.get("retrieved_chunk_ids", [])
        error = resp.get("error", "")
        latency = resp.get("_http_latency_ms", 0)
        route = resp.get("route", "")
        retriever = resp.get("retriever_used", "")

        # Stats
        got_chunks = len(chunk_ids) > 0
        got_answer = bool(answer) and "error" not in resp
        is_error = bool(error)

        if got_chunks:
            has_chunks += 1
        if got_answer:
            has_answer += 1
        if is_error:
            errors += 1

        # By type stats
        if answer_type not in by_type:
            by_type[answer_type] = {"total": 0, "has_chunks": 0, "has_answer": 0}
        by_type[answer_type]["total"] += 1
        if got_chunks:
            by_type[answer_type]["has_chunks"] += 1
        if got_answer:
            by_type[answer_type]["has_answer"] += 1

        # Status indicator
        status = "✅" if got_chunks else "❌"
        print(f"         {status} chunks={len(chunk_ids)} route={route} latency={latency}ms")
        if answer:
            print(f"         💬 {answer[:100]}")
        if error:
            print(f"         ⚠️  ERROR: {error[:80]}")
        print()

        # Save result
        results.append({
            "id": qid,
            "question": question,
            "answer_type": answer_type,
            "answer": answer,
            "retrieved_chunk_ids": chunk_ids,
            "route": route,
            "retriever_used": retriever,
            "latency_ms": latency,
            "has_chunks": got_chunks,
            "error": error,
            "telemetry": resp.get("telemetry", {}),
        })

        # Small delay to avoid overwhelming the API
        time.sleep(0.3)

    # Summary
    print(f"\n{'='*60}")
    print(f"SUMMARY")
    print(f"{'='*60}")
    print(f"Total questions:     {total}")
    print(f"Got chunks:          {has_chunks}/{total} ({100*has_chunks//total}%)")
    print(f"Got answer:          {has_answer}/{total} ({100*has_answer//total}%)")
    print(f"Errors:              {errors}")
    print(f"\nBy answer_type:")
    for atype, stats in sorted(by_type.items()):
        t = stats["total"]
        c = stats["has_chunks"]
        print(f"  {atype:12} {c:3}/{t:3} chunks ({100*c//t if t else 0}%)")

    return {
        "meta": {
            "total": total,
            "has_chunks": has_chunks,
            "has_answer": has_answer,
            "errors": errors,
            "run_at": datetime.now().isoformat(),
            "by_type": by_type,
        },
        "results": results,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Legal RAG Eval Runner")
    parser.add_argument("--questions", default="questions.json")
    parser.add_argument("--output", default="eval_results.json")
    parser.add_argument("--limit", type=int, default=None,
                        help="Limit to first N questions (for testing)")
    args = parser.parse_args()

    # Load questions
    with open(args.questions) as f:
        questions = json.load(f)
    print(f"Loaded {len(questions)} questions from {args.questions}")

    # Run eval
    report = evaluate(questions, limit=args.limit)

    # Save results
    with open(args.output, "w") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)
    print(f"\nResults saved: {args.output}")
    print(f"Total: {report['meta']['has_chunks']}/{report['meta']['total']} questions retrieved chunks")
