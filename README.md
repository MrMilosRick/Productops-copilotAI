# ProductOps Copilot

ProductOps Copilot — сервис "спроси свои документы", который дает **проверяемые ответы с источниками (sources)** для продактов и инженеров.

**Два режима (MVP):**
- **DOC mode**: ответ по загруженному документу → `route=doc_rag`, `sources[] != []`
- **NO-DOC mode**: если в документах нет опоры → честно "нет в документе" → `route=general`, `sources=[]`

✅ MVP зафиксирован тегом: **`v1.0-mvp`**  
✅ Источник истины по запуску: **Docker Compose** (`infra/docker-compose.yml`)

---

## 2-minute demo (Dockerized E2E)

### Quickstart (Docker Compose = single source of truth)
```bash
docker compose -f infra/docker-compose.yml up -d --build
```

Проверка, что сервис отвечает:
```bash
curl -sS -X POST "http://127.0.0.1:8001/api/ask/" \
  -H "Content-Type: application/json" \
  -H "Idempotency-Key: readme-ping-1" \
  -d '{"question":"ping"}' | python3 -c 'import sys,json; d=json.load(sys.stdin); print("route",d.get("route"),"sources",len(d.get("sources") or []),"run",d.get("run_id"))'
```

### MVP contract (ask)
`POST /api/ask/` возвращает (минимум):
- `answer` (строка)
- `sources[]` (список фрагментов)
- `run_id` (int)
- `route` (`doc_rag` | `general`)
- `retriever_used` (для диагностики)

---

## Proof: anti-hallucination QA (12/12)
В репозитории используется QA-скрипт, который проверяет два режима:
- 6 вопросов **DOC** → ожидаем `route=doc_rag` и `sources_n>0`
- 6 вопросов **NO-DOC** → ожидаем `route=general` и `sources_n==0`

Запуск:
```bash
python3 /tmp/qa_mvp_v1.py
```

Ожидаемый результат:
```text
PASS 12 / 12
```

Файл с результатами:
```bash
tail -n 12 /tmp/qa_mvp_v1.jsonl
```

---

## Minimal curl examples (DOC vs NO-DOC)

### DOC mode (route=doc_rag, sources>0)
```bash
curl -sS -X POST "http://127.0.0.1:8001/api/ask/" \
  -H "Content-Type: application/json" \
  -H "Idempotency-Key: smoke-doc-v1" \
  -d '{"question":"что автор говорит про пассивный доход?"}' \
  | python3 -c 'import sys,json; d=json.load(sys.stdin); print("route",d.get("route"),"sources",len(d.get("sources") or []),"run",d.get("run_id"))'
```

### NO-DOC mode (route=general, sources=[])
```bash
curl -sS -X POST "http://127.0.0.1:8001/api/ask/" \
  -H "Content-Type: application/json" \
  -H "Idempotency-Key: smoke-nodoc-v1" \
  -d '{"question":"как выбрать велосипед?"}' \
  | python3 -c 'import sys,json; d=json.load(sys.stdin); print("route",d.get("route"),"sources",len(d.get("sources") or []),"run",d.get("run_id"))'
```

### Idempotency replay
Повторите тот же запрос с тем же `Idempotency-Key` — вернется replay того же `run_id` (если реализовано в текущем контракте).

---

## Debug: runs / steps
Для диагностики используется трассировка `runs/steps` (retrieval → generate_answer).

Пример:
```bash
RID="$(curl -sS -X POST "http://127.0.0.1:8001/api/ask/" \
  -H "Content-Type: application/json" \
  -H "Idempotency-Key: readme-run-1" \
  -d '{"question":"ping"}' | python3 -c 'import sys,json; print(json.load(sys.stdin)["run_id"])')"
echo "RID=$RID"
curl -sS "http://127.0.0.1:8001/api/runs/${RID}/steps/" | head -c 2000; echo
```

---

## Scope (MVP v1.0-mvp)
**IN:**
- DOC/NO-DOC gating без галлюцинаций (через `route` + `sources`)
- стабильный ask-контракт (answer/sources/run_id/route)
- Docker Compose запуск
- QA 12/12 как доказательство поведения

**OUT:**
- follow-up диалоги (v1.1)
- "идеальный" retrieval
- тяжелая админка/enterprise UI

---

### CI-grade smoke (build → up → health → e2e smoke → cleanup)
```bash
make ci-smoke
```

---

## Source of truth
**Runtime:** `infra/docker-compose.yml`  
**MVP snapshot:** git tag `v1.0-mvp`
