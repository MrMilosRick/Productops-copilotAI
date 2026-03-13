"""
Evaluate ARLC submission.json: SLM metrics, grounding F-score, latency, telemetry presence.

Usage:
  python scripts/eval_arlc.py submission.json [--reference reference.json]
If reference.json is provided, compute grounding F-score (beta=2.5) and answer match for deterministic types.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def load_json(path: str | Path) -> dict | list:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def grounding_f_score(pred_pages: list, gold_pages: list, beta: float = 2.5) -> float:
    """Weighted F-score with beta=2.5 (recall ~6x more important than precision)."""
    pred_set = set(pred_pages or [])
    gold_set = set(gold_pages or [])
    if not gold_set:
        return 1.0 if not pred_set else 0.0
    tp = len(pred_set & gold_set)
    recall = tp / len(gold_set) if gold_set else 0.0
    precision = tp / len(pred_set) if pred_set else 0.0
    if precision + recall == 0:
        return 0.0
    return (1 + beta**2) * precision * recall / (beta**2 * precision + recall)


def has_telemetry(entry: dict) -> bool:
    t = entry.get("telemetry") or {}
    return all(
        k in t for k in ("ttft", "latency", "prompt_tokens", "completion_tokens", "retrieved_chunks")
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("submission", help="submission.json path")
    ap.add_argument("--reference", type=str, default="", help="Optional reference answers for grounding/accuracy")
    ap.add_argument("--beta", type=float, default=2.5, help="Grounding F-score beta")
    args = ap.parse_args()

    data = load_json(args.submission)
    answers = data.get("answers", data) if isinstance(data, dict) else data
    if not isinstance(answers, list):
        print("No 'answers' list in submission.", file=sys.stderr)
        return 1

    n = len(answers)
    telemetry_ok = sum(1 for a in answers if has_telemetry(a))
    ttft_list = []
    latency_list = []
    for a in answers:
        t = (a.get("telemetry") or {})
        if "ttft" in t:
            v = float(t["ttft"])
            ttft_list.append(v * 1000 if v < 100 else v)  # assume seconds if < 100
        if "latency" in t:
            v = float(t["latency"])
            latency_list.append(v * 1000 if v < 100 else v)

    avg_ttft = sum(ttft_list) / len(ttft_list) if ttft_list else 0
    avg_latency = sum(latency_list) / len(latency_list) if latency_list else 0

    print("=== ARLC submission evaluation ===")
    print(f"Total answers: {n}")
    print(f"Telemetry present: {telemetry_ok}/{n} ({100 * telemetry_ok / n:.1f}%)")
    print(f"Avg TTFT (ms): {avg_ttft:.0f}")
    print(f"Avg latency (ms): {avg_latency:.0f}")

    if args.reference and Path(args.reference).exists():
        ref_data = load_json(args.reference)
        ref_answers = ref_data.get("answers", ref_data)
        if isinstance(ref_answers, list):
            ref_by_id = {str(a.get("question_id", a.get("id", i))): a for i, a in enumerate(ref_answers)}
            f_scores = []
            for a in answers:
                qid = str(a.get("question_id", ""))
                ref = ref_by_id.get(qid)
                if ref is None:
                    continue
                pred_pages = a.get("retrieved_chunk_ids", [])
                gold_pages = ref.get("retrieved_chunk_ids", ref.get("reference_pages", []))
                f_scores.append(grounding_f_score(pred_pages, gold_pages, args.beta))
            if f_scores:
                print(f"Grounding F-score (beta={args.beta}): {sum(f_scores)/len(f_scores):.4f} (n={len(f_scores)})")

    return 0


if __name__ == "__main__":
    sys.exit(main())
