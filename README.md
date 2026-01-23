# ProductOps Copilot (AI-native RAG backend)

Backend-платформа для AI-native copilots: ingestion → embeddings → retrieval → grounded answers с источниками, трассировкой и идемпотентностью.  
UI намеренно отсутствует: репозиторий — это “engine-first” продукт, готовый к встраиванию.

## Demo in 2 minutes

Требования: Docker + Docker Compose.

Запуск:
- `make up`
- `make smoke`

Если `make smoke` проходит — значит работает end-to-end:
- upload документа
- ожидание ingestion/embeddings (Celery)
- `/api/ask` возвращает `answer + sources`
- проверяется idempotency replay и `409 Conflict` при несовпадении payload
- cleanup

Остановить и удалить окружение:
- `make down`

## What’s inside (architecture)

- **Knowledge Base**: Document + E/ask` → answer + sources + retriever_used + answer_mode
- **Observability**: AgentRun + AgentStep (`retrieve_context` → `generate_answer`)
- **Reliability**: Idempotency-Key + replay; `409 Conflict` при несовпадении payload

## Non-goals (by design)

- ❌ UI / Chat-like интерфейс
- ❌ Auth / Billing
- ❌ “показушные” фичи без инженерного контракта

Цель проекта — продакшн-дисциплина ядра, которое легко оборачивается любым UI.

## Useful commands

- `make health` — health checks
- `make logs` — tail logs
- `make smoke` — end-to-end тест
- `make ci-smoke` — smoke + автоклинап для CI

## Interview pitch

“I build AI-native copilots as backend platforms: ingestion, deterministic embeddings, hybrid retrieval, grounded answers with traceability and idempotency. UI is intentionally thin — the engine is the product.”
