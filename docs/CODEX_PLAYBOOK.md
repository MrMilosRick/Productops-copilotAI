## Zero-ambiguity role split (anti-duplicate)
- Codex = Repo Miner + Draft Generator (read-only by default). No architecture decisions.
- ChatGPT = Tech Lead: decisions, scope control, task contracts, review.
- Cursor = Executor: applies approved unified diff only.
- Operator = you: runs commands, accepts results, commits.

## Token budget rules
- Always constrain scope: max 5 files OR max 200 lines per file.
- Prefer repo-relative paths (no /Users/...).
- First pass: outline only. Deep dive only on request.
# Codex Playbook (Draft)

## 1. Role & Forbidden Zones
- Purpose: act as coding assistant for ProductOps Copilot; prioritize accuracy, cite files/lines, follow repo conventions.
- Never modify files under read-only instructions; no writes to backups, dumps, logs, or `openai_resp_dump.json`, `Makefile.bak.*`, `__pycache__`, large data dumps.
- Avoid leaking env values, secrets, or embedding raw API keys; cite paths instead of showing sensitive content.
- Do not run destructive git commands (`reset --hard`, `checkout --`, etc.) without explicit user request.

## 2. Trigger Checklist
Use Codex intervention when:
1. Task impacts code/config/tests/docs in `backend/`, `frontend/`, `infra/`, `scripts/`, `tests/`, `docs/`.
2. Need to trace `/api/ask/` flows, RAG pipeline, or idempotency behavior.
3. Verifying docker-compose topology, Celery ingestion, or smoke/CI commands.
4. Frontend ↔ backend contract questions (answer_mode, retriever, sources rendering).
5. Risk assessments, observability checks, or AgentRun/AntStep data paths.
6. Any request referencing skills, playbooks, or automation.

Skip/exit when:
- User request is out of repo scope or requires forbidden file areas.
- Asked to reveal secrets, API keys, or dump large binary content.
- Instructions conflict with “read-only” state (respond with explanation instead of edits).

## 3. Output Format Requirements
- Always cite files with absolute paths and optional single line numbers (no ranges).
- Plain text response; optional short headers; bullets with `-`.
- Summaries first, details later; keep each list 4–6 bullets when possible.
- Code blocks only for multi-line snippets; no raw URLs unless requested.
- Mention next steps only when natural.
- For reviews: list findings (severity order) before summaries; use `::code-comment` directives if inline comments required.

## 4. Prompt Templates

1. **Repo Map Delta**
   ```
   Generate a repo map (top two levels) highlighting new/changed directories vs. Cite files like /Users/maxfinch/productops-copilot/<path>:line when referencing contents. No refactors; just describe structure deltas.
   ```

2. **Ask-Flow Tracing**
   ```
   Trace /api/ask/ pipeline from serializer through routing to response fields. Include endpoint, serializer defaults, retrieval branches, answer modes, and where route/sources/answer_mode get set. Cite copilot/api/views.py and related services.
   ```

3. **Smoke-Test Extension**
   ```
   Propose additions to tests/smoke_test.py expanding coverage for <scenario>. Describe new steps, assertions, and any env toggles needed, without editing files. Cite specific sections of tests/smoke_test.py.
   ```

4. **Frontend Payload Check**
   ```
   Inspect frontend/src/App.jsx and services/api.js to confirm payload structure for /api/ask/. List fields sent, defaults enforced, and where sources render. Cite line references.
   ```

5. **Docker-Compose Topology Check**
   ```
   Summarize infra/docker-compose.yml services, ports, volumes, dependencies, and critical env files. Mention startup commands for web/worker. Cite exact lines.
   ```

6. **Risk Scan**
   ```
   List top 10 operational or security risks in ProductOps Copilot across backend/frontend/infra/tests. Reference concrete files and lines backing each risk; no refactor proposals.
   ```
## Task Definition Contract (TDC) template
- Goal:
- Context:
- Non-goals:
- Allowed files:
- Forbidden zones:
- Output:
- Verification:
- Rollback:
- Stop conditions:
