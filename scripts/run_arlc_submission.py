#!/usr/bin/env python3
"""
Generate ARLC submission.json from questions and document corpus.

Usage:
  # From platform (requires ARLC_API_KEY):
  python scripts/run_arlc_submission.py --phase eval --out submission.json

  # From local files:
  python scripts/run_arlc_submission.py --questions path/to/questions.json --docs path/to/docs_corpus --out submission.json

Documents: directory of PDFs (or use --docs with folder from platform download).
Questions: JSON array of {id, question, answer_type} or {"questions": [...]}.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

# Add repo root for imports
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from arlc import ARLCConfig, SubmissionPayload, run_single_question
from arlc.config import SUBMISSION_VERSION
from arlc.ingestion import ingest_document_legal


def load_questions(path: str | Path) -> list:
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, list):
        return data
    return data.get("questions", data.get("data", []))


def load_all_chunks(docs_dir: str | Path, config: ARLCConfig) -> list:
    docs_path = Path(docs_dir)
    if not docs_path.is_dir():
        return []
    all_chunks = []
    for pdf in sorted(docs_path.glob("**/*.pdf")):
        try:
            chunks = ingest_document_legal(
                str(pdf),
                max_tokens=config.chunk_max_tokens,
                overlap_tokens=config.chunk_overlap_tokens,
            )
            all_chunks.extend(chunks)
        except Exception as e:
            print(f"Warning: skip {pdf}: {e}", file=sys.stderr)
    return all_chunks


def main() -> int:
    ap = argparse.ArgumentParser(description="Generate ARLC submission.json")
    ap.add_argument("--phase", type=str, default="", help="Phase dir (eval); if set, use phase/questions.json and phase/docs_corpus")
    ap.add_argument("--questions", type=str, default="", help="Path to questions.json")
    ap.add_argument("--docs", type=str, default="", help="Path to documents directory (PDFs)")
    ap.add_argument("--out", type=str, default="submission.json", help="Output submission path")
    ap.add_argument("--limit", type=int, default=0, help="Limit number of questions (0 = all)")
    args = ap.parse_args()

    if args.phase:
        phase_dir = Path(args.phase)
        questions_path = phase_dir / "questions.json"
        docs_dir = phase_dir / "docs_corpus"
        if not questions_path.exists():
            print(f"Downloading questions to {phase_dir}...", file=sys.stderr)
            try:
                from arlc import EvaluationClient
                client = EvaluationClient.from_env()
                client.download_questions(str(phase_dir))
            except Exception as e:
                print(f"Download failed: {e}. Place questions.json in {phase_dir}.", file=sys.stderr)
                return 1
        if not docs_dir.exists():
            print(f"Downloading documents to {docs_dir}...", file=sys.stderr)
            try:
                from arlc import EvaluationClient
                client = EvaluationClient.from_env()
                client.download_documents(str(docs_dir))
            except Exception as e:
                print(f"Download failed: {e}. Extract documents to {docs_dir}.", file=sys.stderr)
                return 1
    else:
        questions_path = args.questions
        docs_dir = args.docs
        if not questions_path or not Path(questions_path).exists():
            print("Provide --questions path or --phase dir.", file=sys.stderr)
            return 1
        if not docs_dir or not Path(docs_dir).is_dir():
            print("Provide --docs directory or --phase dir.", file=sys.stderr)
            return 1

    questions = load_questions(questions_path)
    if not questions:
        print("No questions loaded.", file=sys.stderr)
        return 1

    config = ARLCConfig.from_env()
    chunks = load_all_chunks(docs_dir, config)
    if not chunks:
        print("Warning: no chunks from documents; retrieval will be empty.", file=sys.stderr)

    if args.limit > 0:
        questions = questions[: args.limit]

    payload = SubmissionPayload()
    payload.submission_version = SUBMISSION_VERSION
    for i, q in enumerate(questions):
        qid = q.get("id") or q.get("question_id") or str(i + 1)
        question_text = q.get("question") or q.get("text") or ""
        answer_type = q.get("answer_type", "free_text")
        entry = run_single_question(qid, question_text, answer_type, chunks, config=config)
        payload.answers.append(entry)
        if (i + 1) % 10 == 0:
            print(f"Processed {i + 1}/{len(questions)}", file=sys.stderr)

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    payload.save(str(out_path))
    print(f"Wrote {out_path}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
