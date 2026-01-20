# ProductOps Copilot — Architecture (by files)

## Runtime entrypoints
- Web (Django): `backend/manage.py`
- Celery worker: `backend/app/celery.py` + tasks in `backend/copilot/tasks/`
- UI:
  - `/` -> redirect to `/ui/` (see `backend/app/urls.py`)
  - `/ui/` -> `backend/ui/views.py:index` -> template `ui/index.html` -> `backend/ui/templates/ui/index.html`

## URL routing
- Root urls: `backend/app/urls.py`
- API urls: `backend/copilot/api/urls.py`
- UI urls: `backend/ui/urls.py`

## Core domain (DB models)
`backend/copilot/models.py`
- Workspace
- KnowledgeSource
- Document
- EmbeddingChunk (pgvector)
- AgentRun / AgentStep (trace)
- IdempotencyKey

## Ingestion pipeline (async)
- Task: `backend/copilot/tasks/ingestion.py:process_document`
- Chunking: `backend/copilot/services/chunking.py`
- Embeddings: `backend/copilot/services/embeddings.py`
- Storage: `EmbeddingChunk.embedding`

## Retrieval
- Keyword: `backend/copilot/services/retriever.py:keyword_retrieve(..., document_id=None)`
- Vector: `backend/copilot/services/vector_retriever.py:vector_retrieve(..., document_id=None)`
- Hybrid: `backend/copilot/services/hybrid_retriever.py:hybrid_retrieve(.. document_id=None)`

## Answer generation
- OpenAI RAG: `backend/copilot/services/llm.py:rag_answer_openai`
- API orchestration + idempotency + traces: `backend/copilot/api/views.py:ask`

## Infra
- Compose: `infra/docker-compose.yml`
- Web image: `backend/Dockerfile`
- Smoke: `tests/smoke_test.py` (used by Makefile target `smoke`)
