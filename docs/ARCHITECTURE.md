# ProductOps Copilot — Architecture (overview)

This is a short, living architecture overview.  
For full file-level details, see: `docs/ARCHITECTURE_SNAPSHOT.md`.

## Repo map
- `backend/` — Django backend: settings/urls/celery + copilot domain + REST API
  - `backend/app/` — Django project entrypoints (settings/urls/celery)
  - `backend/copilot/` — domain models + API views/serializers + RAG services + Celery tasks
  - `backend/ui/` — server-side UI wrapper (serves built React)
- `frontend/` — React (Vite) SPA that calls backend APIs
- `infra/` — docker-compose stack (db/redis/web/worker + volumes)
- `tests/` — end-to-end smoke tests (`tests/smoke_test.py`)
- `scripts/` — helper scripts (smoke shell runner, UI sync)
- `docs/` — architecture docs, notes, prototypes

## Runtime topology (docker-compose)
Services:
- `db` — PostgreSQL + pgvector
- `redis` — broker/cache
- `uploads-init` — prepares uploads volume/permissions
- `web` — Django via gunicorn (container port 8000, host exposed as 8001)
- `worker` — Celery worker (`celery -A app worker -l INFO`)
Shared volume:
- `copilot_uploads` — shared between web/worker for document uploads

Key Makefile flows:
- `make up` / `make down` / `make logs`
- `make health` → checks `GET /api/health/` until OK
- `make smoke` → runs `tests/smoke_test.py`
- `make ci-smoke` → compose up → health → smoke → compose down -v
- `make lint` → `py_compile` of key backend files and smoke script

## Runtime entrypoints
- Django (web): `backend/manage.py` + `backend/app/urls.py`
- Celery (worker): `backend/app/celery.py` + tasks in `backend/copilot/tasks/`
- UI:
  - `/` → redirect to `/ui/` (see `backend/app/urls.py`)
  - `/ui/` → `backend/ui/views.py:index` → template `backend/ui/templates/ui/index.html`
  - built frontend is synced into backend via `make ui-sync` (`scripts/ui_sync.sh`)

## URL routing
- Root urls: `backend/app/urls.py`
- API urls: `backend/copilot/api/urls.py`
- UI urls: `backend/ui/urls.py`

## Core domain (DB models)
`backend/copilot/models.py`:
- `Workspace` / `KnowledgeSource`
- `Document` (stores content + metadata)
- `EmbeddingChunk` (pgvector chunks)
- `IdempotencyKey` (replay + conflict)
- `AgentRun` / `AgentStep` (trace & observability)

## Public API (high-level)
Core endpoints in `backend/copilot/api/views.py`:
- `GET /api/health/` → `{ "status": "ok" }`
- Knowledge base:
  - `POST /api/kb/upload_text/` → create Document from text → enqueue ingestion
  - `POST /api/kb/upload_file/` → upload file → enqueue ingestion
  - `GET /api/kb/documents/` → list recent documents
  - `GET /api/kb/documents/<id>/` → document detail (status/chunks/etc)
- Ask / Observability:
  - `POST /api/ask/` → retrieval + routing + answer (doc/general/summary)
  - `GET /api/runs/`, `/api/runs/<id>/`, `/api/runs/<id>/steps/` → traces

## RAG pipeline (end-to-end)
### Ingestion (async)
- Entry: `/api/kb/upload_text/` or `/api/kb/upload_file/`
- Task: `backend/copilot/tasks/ingestion.py:process_document`
- Chunking: `backend/copilot/services/chunking.py`
- Embeddings: `backend/copilot/services/embeddings.py`
- Storage: `EmbeddingChunk.embedding` (pgvector)

### Retrieval
- Keyword: `backend/copilot/services/retriever.py`
- Vector: `backend/copilot/services/vector_retriever.py`
- Hybrid/auto: `backend/copilot/services/hybrid_retriever.py`
`/api/ask/` chooses retriever based on request (or auto) and evaluates relevance.

### Answer modes + routing
- `/api/ask/` orchestrator: `backend/copilot/api/views.py:ask`
- Modes:
  - `sources_only` → return sources only (no answer)
  - `deterministic` → deterministic synthesis (no LLM)
  - `langchain_rag` → LLM-backed RAG answer (see `backend/copilot/services/llm.py`)
- Routing (response field `route`):
  - `summary` (fast path when summary trigger is detected)
  - `general` (no relevant document evidence)
  - `doc_rag` (document evidence present)

### Response contract & safety
Responses include: `run_id`, `answer`, `sources` (sanitized), `answer_mode`, `route`, plus debug metadata (when enabled).
Security invariant: API must never return full chunk text in sources — enforced by `sanitize_sources()` and smoke tests.

## Observability & idempotency
- `AgentRun` + `AgentStep` persist run/step metadata and support trace UI (`/api/runs/*`).
- Idempotency: repeated requests with same Idempotency-Key replay the same `run_id`; conflicts return 409.

## Frontend (React)
- Entry: `frontend/src/main.jsx` mounts `<App />`
- Main logic: `frontend/src/App.jsx`:
  - uploads / selects documents
  - sends `/api/ask/` requests (with Idempotency-Key)
  - renders answer + sources
- HTTP wrapper: `frontend/src/services/api.js`

## Infra
- Compose: `infra/docker-compose.yml`
- Web image: `backend/Dockerfile`
- E2E smoke: `tests/smoke_test.py` (used by `make smoke`)