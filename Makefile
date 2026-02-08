.PHONY: up down logs health smoke qa-mvp lint ci-smoke dev-smoke ui-dev ui-build ui-sync

COMPOSE = docker compose -f infra/docker-compose.yml
BASE_URL ?= http://localhost:8001

up:
	@$(COMPOSE) up -d --build

down:
	@$(COMPOSE) down

logs:
	@$(COMPOSE) logs -f --tail=200

health:
	@echo "Checking health at $(BASE_URL)/api/health/ ..."
	@i=0; \
	while [ $$i -lt 60 ]; do \
		resp=$$(curl -fsS --max-time 2 "$(BASE_URL)/api/health/" 2>/dev/null || true); \
		if echo "$$resp" | grep -E -q '"status"\s*:\s*"ok"'; then \
			echo "Health OK"; echo "$$resp"; exit 0; \
		fi; \
		i=$$((i+1)); \
		echo "Waiting for health... ($$i/60)"; \
		sleep 1; \
	done; \
	echo "Health FAILED"; exit 1

smoke:
	@python3 tests/smoke_test.py

qa-mvp:
	@BASE_URL="$(BASE_URL)" python3 tests/qa_mvp_v1.py

lint:
	@python3 -m py_compile backend/app/urls.py
	@python3 -m py_compile backend/copilot/api/views.py
	@python3 -m py_compile backend/copilot/services/retriever.py backend/copilot/services/hybrid_retriever.py backend/copilot/services/vector_retriever.py
	@python3 -m py_compile tests/smoke_test.py
	@echo "OK: lint"

ci-smoke:
	@set -e; \
	trap 'echo "cleanup: docker compose down"; $(COMPOSE) down -v || true' EXIT; \
	$(COMPOSE) up -d --build; \
	$(MAKE) health; \
	$(MAKE) smoke; \
	$(MAKE) qa-mvp

dev-smoke:
	@set -e; \
	$(COMPOSE) up -d --build; \
	$(MAKE) health; \
	$(MAKE) smoke; \
	echo "OK: dev-smoke (containers left running)"

# --- UI (React) ---

ui-dev:
	@cd frontend && npm run dev

ui-build:
	@cd frontend && npm run build

ui-sync:
	@./scripts/ui_sync.sh
