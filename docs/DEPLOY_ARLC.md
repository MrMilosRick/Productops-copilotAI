# Agentic RAG Legal Challenge 2026 — Deployment & Run Guide

This document describes how to run the ARLC pipeline and produce a competition submission.

## Target metrics

- **Final Score** > 0.80  
- **SLM Metrics** > 0.95  
- **LLM Judge Score** > 0.70  
- **Average TTFT** < 1500 ms  
- **TTFT Bonus** > 1.05 (average TTFT < 1.0 s)

## Prerequisites

- Python 3.10+
- `requests` (for EvaluationClient)
- PDF extraction: `pdfminer.six` or `pypdf`
- Optional: `tiktoken` for accurate token counts in chunking  
- Optional: OpenAI (or other) API for real LLM generation; otherwise pipeline uses placeholder answers

## Environment

```bash
export ARLC_API_KEY="your-platform-api-key"   # from competition platform
export ARLC_PLATFORM_URL="https://platform.agentic-challenge.ai"  # optional override
# Optional tuning
export ARLC_TOP_K_CANDIDATES=50
export ARLC_TOP_K_FINAL=5
export ARLC_USE_RERANKER=true
export ARLC_CHUNK_MAX_TOKENS=450
export ARLC_CHUNK_OVERLAP_TOKENS=50
export ARLC_FREE_TEXT_MAX_CHARS=280
export ARLC_STREAMING=true
```

## 1. Download data from platform

```bash
mkdir -p eval
python -c "
from arlc import EvaluationClient
c = EvaluationClient.from_env()
c.download_questions('eval')
c.download_documents('eval/docs_corpus')
"
```

Or use the run script with `--phase eval`; it will download if missing.

## 2. Generate submission

```bash
# Full run (questions from eval/questions.json, PDFs from eval/docs_corpus)
python scripts/run_arlc_submission.py --phase eval --out submission.json

# With local paths
python scripts/run_arlc_submission.py --questions path/to/questions.json --docs path/to/docs_corpus --out submission.json

# Limit to first N questions (e.g. testing)
python scripts/run_arlc_submission.py --phase eval --out submission.json --limit 20
```

Output: `submission.json` with one entry per question, each containing:

- `question_id`, `answer`, `retrieved_chunk_ids` (page IDs: `doc_slug_page`, 1-based)
- `telemetry`: `ttft`, `latency`, `prompt_tokens`, `completion_tokens`, `retrieved_chunks`
- Optional: `model_name`, `time_per_output_token_ms`

## 3. Evaluate submission locally

```bash
python scripts/eval_arlc.py submission.json
# With reference (if you have gold answers)
python scripts/eval_arlc.py submission.json --reference reference.json
```

Reports: number of answers, telemetry presence, average TTFT/latency, and (if reference given) grounding F-score (β=2.5).

## 4. Submit to platform

1. Zip your code: `zip -r code_archive.zip arlc scripts backend ...` (exclude venv, .git, eval data).
2. Upload `submission.json` and `code_archive.zip` via the competition platform per their instructions.

## Pipeline architecture (summary)

- **Ingestion** (`arlc/ingestion.py`): PDFs → per-page text → token-based chunks (300–500 tokens, 50 overlap), with `page_num` and `doc_slug` in meta.
- **Retrieval**: In-memory keyword-style retrieval over chunks; configurable `retrieve_fn` for hybrid/vector/reranker (e.g. Django + pgvector).
- **Generation**: Configurable `generate_fn`; default placeholder. Plug in streaming LLM and call `telemetry_rec.mark_first_token()` on first token for TTFT.
- **Telemetry**: Every answer includes TTFT, latency, token counts, and `retrieved_chunks` (page IDs actually used). Missing telemetry incurs 0.9 factor.

## Integrating real retrieval and LLM

- **Retrieval**: Implement a function `(question: str, top_k: int) -> List[dict]` where each dict has `text` and `meta: {page_num, doc_slug}`. Pass it as `retrieve_fn` to `run_single_question`. You can call the existing Django hybrid retriever and map `chunk_id` → page via chunk `meta` (ensure ingestion writes `page_num`/slug into meta).
- **LLM**: Implement `(question, retrieved_chunks, streaming) -> {answer, prompt_tokens, completion_tokens, model_name}`; use streaming and mark first token in the same process as `TelemetryRecorder.mark_first_token()` to report TTFT correctly.

## Repo layout (ARLC)

- `arlc/` — schema, config, telemetry, client, pipeline, ingestion  
- `scripts/run_arlc_submission.py` — entrypoint to generate submission.json  
- `scripts/eval_arlc.py` — local evaluation of submission  
- `docs/AUDIT_AGENTIC_RAG_LEGAL_2026.md` — full audit and gap analysis
