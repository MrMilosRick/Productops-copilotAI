# ProductOps Copilot AI

Django + Postgres (pgvector) + Redis + Celery.  
Local dev runs via Docker Compose.

## Quick start

```bash
cd infra
docker compose up -d --build
curl -s http://localhost:8001/api/health/ && echo
```
