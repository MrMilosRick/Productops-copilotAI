# ProductOps Copilot

AI-native RAG backend with async ingestion (Celery), pgvector retrieval, grounded answers with sources, and run/step tracing.

---

## 2-minute demo (Dockerized E2E)

### CI-grade smoke (build → up → health → e2e smoke → cleanup)
```bash
make ci-smoke
