.PHONY: up down logs health smoke ci-smoke

COMPOSE = docker compose -f infra/docker-compose.yml
BASE_URL ?= http://localhost:8001

up:
	$(COMPOSE) up -d --build

down:
	$(COMPOSE) down -v

logs:
	$(COMPOSE) logs -f --tail=200

health:
	@echo "Checking health at $(BASE_URL)/api/health/ ..."
	@for i in $$(seq 1 60); do \
		if curl -fsS $(BASE_URL)/api/health/ >/dev/null 2>&1; then \
			echo "Health OK"; \
			curl -fsS $(BASE_URL)/api/health/; echo; \
			exit 0; \
		fi; \
		echo "Waiting for health... ($$i/60)"; \
		sleep 1; \
	done; \
	echo "Health check failed"; \
	$(COMPOSE) ps; \
	exit 1

smoke:
	@python3 tests/smoke_test.py

ci-smoke:
	@set -e; \
	trap 'echo "cleanup: docker compose down"; $(COMPOSE) down -v' EXIT; \
	$(COMPOSE) up -d --build; \
	$(MAKE) health; \
	$(MAKE) smoke
