# ProductOps Copilot (MVP)

Django + DRF + Postgres(pgvector) + Redis + Celery.

## Run
1) Copy env
   cp .env.example .env

2) Start
   cd infra
   docker compose up --build

3) Check
   http://localhost:8000/api/health/
