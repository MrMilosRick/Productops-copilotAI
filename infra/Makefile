URL ?= http://localhost:8001
COMPOSE ?= docker compose

.PHONY: up down logs wait smoke

up:
	@$(COMPOSE) up -d --build web worker

down:
	@$(COMPOSE) down -v

logs:
	@$(COMPOSE) ps || true
	@$(COMPOSE) logs --no-color --tail=300 web worker db redis || true

wait:
	@for i in $$(seq 1 120); do \
	  curl -fsS "$(URL)/api/health/" >/dev/null 2>&1 && echo "web ready" && exit 0; \
	  sleep 1; \
	done; \
	echo "web not ready"; exit 1

smoke: up wait
	@python3 smoke.py "$(URL)"
