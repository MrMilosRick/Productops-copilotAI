#!/usr/bin/env python3
"""
Submit answers.json + code archive to the evaluation platform via arlc EvaluationClient.

Requires: pip install -e path/to/starter_kit (so arlc is available).
Env: EVAL_API_KEY, EVAL_BASE_URL (optional; default platform URL).

Usage:
  python scripts/submit_to_platform.py --submission answers.json --archive code_archive.zip
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

try:
    from arlc.client import EvaluationClient
except ImportError:
    print("arlc not found. Install the starter kit: pip install -e path/to/starter_kit", file=sys.stderr)
    sys.exit(1)


def main() -> int:
    parser = argparse.ArgumentParser(description="Submit submission.json + code archive to evaluation platform")
    parser.add_argument("--submission", required=True, help="Path to submission JSON (e.g. answers.json)")
    parser.add_argument("--archive", required=True, help="Path to code archive ZIP")
    args = parser.parse_args()

    submission_path = Path(args.submission)
    archive_path = Path(args.archive)
    if not submission_path.exists():
        print(f"Error: submission file not found: {submission_path}", file=sys.stderr)
        return 1
    if not archive_path.exists():
        print(f"Error: archive not found: {archive_path}", file=sys.stderr)
        return 1

    client = EvaluationClient.from_env()
    print("Submitting...")
    response = client.submit_submission(submission_path, archive_path)
    uuid = response.get("uuid")
    status = response.get("status", "")
    status_response = response
    print(f"uuid: {uuid}")
    print(f"status: {status}")

    while status in ("queued", "processing"):
        time.sleep(10)
        status_response = client.get_submission_status(uuid)
        status = status_response.get("status", "")
        print(f"status: {status}")

    if status == "completed":
        metrics = status_response.get("metrics") or {}
        print("Final scores:")
        for k, v in metrics.items():
            print(f"  {k}: {v}")
    elif status == "error":
        print("Submission ended with status: error", file=sys.stderr)
        if status_response:
            print(status_response, file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
