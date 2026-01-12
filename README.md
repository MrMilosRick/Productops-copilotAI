# ProductOps Copilot AI

Django + Postgres (pgvector) + Redis + Celery.
Local dev runs via Docker Compose.

## Quick start

```bash
cd infra
docker compose up -d --build
curl -s http://localhost:8001/api/health/ && echo
```

## Demo: upload text -> Celery -> DB

```bash
curl -s -X POST "http://localhost:8001/api/kb/upload_text/" \
  -H "Content-Type: application/json" \
  -d "{\"title\":\"Demo Doc\",\"content\":\"Hello world. This is a demo document for chunking.\"}" && echo

sleep 1
curl -s "http://localhost:8001/api/kb/documents/" && echo
```
