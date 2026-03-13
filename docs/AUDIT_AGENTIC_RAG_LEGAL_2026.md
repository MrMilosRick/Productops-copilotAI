# Phase 1 — Full Repository Audit: Agentic RAG Legal Challenge 2026

**Objective:** Verify readiness for the Agentic RAG Legal Challenge 2026 and identify gaps against target metrics (Final Score > 0.80, SLM > 0.95, LLM Judge > 0.70, Avg TTFT < 1500 ms, TTFT Bonus > 1.05).

**Reference:** Leaderboard baseline — Final: 0.76, SLM: 0.94, LLM Judge: 0.61, Avg TTFT: 1945 ms.

---

## 1. Repository Structure Summary

| Layer | Current State |
|-------|----------------|
| **Backend** | Django 5.1 + DRF, Celery, PostgreSQL + pgvector |
| **RAG** | Hybrid retrieval (keyword + vector), single-stage, top_k=5 |
| **Chunking** | Char-based (1000 chars in ingestion, 150 overlap); chunking.py allows 3500/300 |
| **Embeddings** | Stub only (deterministic hash); no real embedding API configured |
| **LLM** | OpenAI Responses API (gpt-5-mini), RU/EN routing |
| **Graph/Agent** | Placeholder only (`# later: LangGraph nodes`) |
| **Submission** | No ARLC submission schema; no telemetry; no EvaluationClient |

---

## 2. Weaknesses Identified

### 2.1 Retrieval Quality

- **No reranking:** Single-stage retrieval; no cross-encoder or top-50 → rerank → top-5.
- **No BM25:** Keyword path uses regex term match + occurrence scoring, not true BM25.
- **No query expansion:** No legal synonym expansion or clause references.
- **No multi-hop:** No detection of multi-document queries or recursive retrieval.
- **top_k fixed at 5:** Competition benefits from larger candidate set then rerank.

### 2.2 Chunking Strategy

- **Not legal-aware:** Char-based, no section/clause detection, no hierarchical chunking.
- **Wrong size:** Competition specifies 300–500 **tokens** per chunk, 50 token overlap; current is 1000 chars, 150 chars.
- **No page preservation:** Chunks are not tied to PDF page numbers; competition requires chunk IDs as `pdf_id_page` (e.g. `abc123_3`), 1-based.
- **No section metadata:** Section headings, clause boundaries not extracted or stored.

### 2.3 Legal Citation Grounding

- **Chunk IDs are DB IDs:** API returns `chunk_id` (Django PK), not page IDs. Submission must use only **pages actually used** in the form `doc_slug_page` (1-based).
- **No citation verification:** No step to ensure citations in the answer come from retrieved chunks.
- **Grounding F-score β=2.5:** Recall is ~6× more important than precision; missing required pages is heavily penalized.

### 2.4 Latency Bottlenecks (TTFT)

- **No TTFT measurement:** No timing from "question received" to "first token of final answer."
- **No streaming:** LLM calls are non-streaming; TTFT = total response time if not using streaming.
- **Sync retrieval:** No async retrieval or parallel fetch.
- **No embedding cache:** Every query recomputes query embedding.
- **No preloaded index:** Index is in DB; no warm-up or preloading mentioned.

### 2.5 Token Inefficiency

- **Large context blocks:** Up to 3500 chars per snippet in RAG prompt; no context pruning.
- **No compressed prompts:** No structured minimal templates for legal QA.
- **No token counting in API:** AgentRun has prompt_tokens/completion_tokens but they are not populated in the current flow.

### 2.6 Telemetry Logging

- **Missing entirely:** No `ttft`, `latency`, `prompt_tokens`, `completion_tokens`, `retrieved_chunks` in response.
- **Submission format:** Competition expects per-answer JSON with answer, citations, and telemetry. Missing telemetry → T factor 0.9 (10% penalty).

### 2.7 Answer Quality & Competition Rules

- **Deterministic types:** Competition has boolean (true/false), number (±1% tolerance), date (ISO 8601), name(s) (strip + lower). Current pipeline is free-text / RAG only; no answer_type handling.
- **Unanswerable questions:** Must return explicit "not in corpus" and empty reference list.
- **Free-text format:** 1–3 sentences, max 280 characters.
- **Names:** Comparison is s.strip().lower(); "Chief Justice" = "chief justice" ≠ "the Chief Justice".

---

## 3. Architectural Recommendations

### 3.1 Document Ingestion (Legal-Aware)

- **PDF page-level extraction:** Extract text per page; store `page_num` (1-based) in chunk metadata.
- **Chunk ID for submission:** Store or derive `(document_slug, page_num)` so submission can output `doc_slug_page`.
- **Token-based chunking:** 300–500 tokens per chunk, 50 token overlap; preserve section boundaries.
- **Section metadata:** Detect "Article X", "Section Y", "Clause Z" and store in chunk meta.

### 3.2 Indexing

- **Keep hybrid:** Combine BM25 (or improved keyword) + dense embeddings.
- **Add reranker:** Two-stage: retrieve top 50 → cross-encoder rerank → top 5.
- **Embedding provider:** Replace stub with a real embedding API for semantic search.

### 3.3 Retrieval Pipeline

- **Multi-stage:** Retrieve candidates (e.g. 50) → optional query expansion → rerank → top 5.
- **Grounding validation:** Map model citations to page IDs; return only pages **actually used**.
- **Multi-hop:** For multi-document queries, run recursive retrieval and merge.

### 3.4 Telemetry (Mandatory)

- **Every answer must include:** `ttft`, `latency`, `prompt_tokens`, `completion_tokens`, `retrieved_chunks` (page IDs).
- **Response shape:** Align with competition: `answer`, `citations`, `telemetry` object.

### 3.5 Latency Optimization

- **Streaming responses** for final answer.
- **Async retrieval** (vector and keyword in parallel).
- **Cache query embeddings.**
- **Minimal prompt tokens.**

---

## 4. Gap Summary vs Competition Requirements

| Requirement | Current | Needed |
|-------------|---------|--------|
| Chunk IDs in submission | DB chunk_id | Page IDs: `doc_slug_page` (1-based) |
| Telemetry | None | ttft, latency, prompt_tokens, completion_tokens, retrieved_chunks |
| Chunking | 1000 chars, 150 overlap | 300–500 tokens, 50 overlap, legal sections |
| Reranking | No | Yes (top 50 → rerank → top 5) |
| TTFT measurement | No | From request start to first token of final answer |
| Answer types | Free-text only | boolean, number, date, name, names, free_text |
| Unanswerable | Generic message | Explicit "not in corpus" + empty refs |
| Submission | N/A | submission.json + code_archive.zip |

---

## 5. Conclusion

The repository is **not** ready for the Agentic RAG Legal Challenge 2026 as-is. Implement an **ARLC-dedicated pipeline** (separate module or entrypoints) that adds legal chunking, page IDs, reranking, telemetry, and submission generation, while keeping the existing ProductOps Copilot API unchanged.
ency`, `prompt_tokens`, `completion_tokens`, `retrieved_chunks` (page IDs).
- **Response shape:** Align with competition: `answer`, `citations` (or equivalent), `telemetry` object. Never omit telemetry.

### 3.7 Latency Optimization

- **Streaming responses** for final answer.
- **Async retrieval** (e.g. run vector and keyword in parallel).
- **Cache query embeddings** for repeated/similar questions.
- **Preload indexes** / connection pooling.
- **Minimal prompt tokens** (compressed system prompt, limit context).
- **Fast LLM** for first-token speed (e.g. small/fast model for final answer).

---

## 4. Gap Summary vs Competition Requirements

| Requirement | Current | Needed |
|-------------|---------|--------|
| Chunk IDs in submission | DB chunk_id | Page IDs: `doc_slug_page` (1-based) |
| Telemetry | None | ttft, latency, prompt_tokens, completion_tokens, retrieved_chunks |
| Chunking | 1000 chars, 150 overlap | 300–500 tokens, 50 overlap, legal sections |
| Reranking | No | Yes (e.g. top 50 → rerank → top 5) |
| TTFT measurement | No | From request start to first token of final answer |
| Answer types | Free-text only | boolean, number, date, name, names, free_text |
| Unanswerable | Generic message | Explicit "not in corpus" + empty refs |
| Submission | N/A | submission.json + code_archive.zip |

---

## 5. Conclusion

The repository is **not** ready for the Agentic RAG Legal Challenge 2026 as-is. It provides a solid general RAG base (Django, pgvector, hybrid retrieval, LLM integration) but lacks:

1. **Competition-specific contract:** Submission schema, telemetry, page-based citations.
2. **Legal-oriented ingestion:** Page-level chunking, token-based sizes, section metadata.
3. **Retrieval maximization:** Reranking, query expansion, multi-hop, grounding validation.
4. **Latency discipline:** TTFT measurement, streaming, async, caching.
5. **Answer quality controls:** answer_type handling, unanswerable handling, 280-char limit, citation verification.

Recommended direction: implement an **ARLC-dedicated pipeline** (separate module or entrypoints) that uses the existing DB and retrieval where possible, but adds legal chunking, page IDs, reranking, telemetry, and submission generation, so the existing ProductOps Copilot API remains unchanged while the competition submission is produced by the new pipeline.

---

## 6. Technical Audit Report (Summary)

**Verdict:** Repository is **not** competition-ready. The `arlc` module provides schema, config, and telemetry types but lacked (at audit time): **EvaluationClient**; **pipeline**; **legal ingestion**; **page ID mapping**; **reranker**; **answer_type** and unanswerable handling; **run script**. Implemented at audit: submission dataclasses, `ARLCConfig`, `TelemetryRecorder`/`capture_telemetry`. **Post-audit implementation:** EvaluationClient (`arlc/client.py`), legal ingestion with per-page PDF and token chunking (`arlc/ingestion.py`), pipeline with TTFT/telemetry and citation-based page IDs (`arlc/pipeline.py`), run script (`scripts/run_arlc_submission.py`), eval script (`scripts/eval_arlc.py`), and deployment guide (`docs/DEPLOY_ARLC.md`).
