.DEFAULT_GOAL := help

help:
	@echo "Targets:"
	@echo "  make up        - build+start docker compose"
	@echo "  make down      - stop"
	@echo "  make logs      - tail logs"
	@echo "  make wait      - wait until /api/health/ is ready"
	@echo "  make health    - print /api/health/"
	@echo "  make demo      - upload demo doc + list docs"
	@echo "  make psql      - open psql inside db"

up:
	cd infra && docker compose up -d --build

down:
	cd infra && docker compose down

logs:
	cd infra && docker compose logs -f --tail=200 web worker

wait:
	@echo "Waiting for web..."
	@for i in $$(seq 1 30); do \
	  curl -fsS http://localhost:8001/api/health/ >/dev/null 2>&1 && echo "OK" && exit 0; \
	  echo "  ... ($$i)"; \
	  sleep 1; \
	done; \
	echo "ERROR: web not ready"; \
	exit 1

health: wait
	curl -s http://localhost:8001/api/health/ && echo

demo: wait
	curl -s -X POST "http://localhost:8001/api/kb/upload_text/" \
	  -H "Content-Type: application/json" \
	  -d "{\"title\":\"Demo Doc $(shell date +%s)\",\"content\":\"Hello world. This is a demo document for chunking.\"}" && echo
	sleep 1
	curl -s "http://localhost:8001/api/kb/documents/" && echo

psql:
	cd infra && docker compose exec db psql -U copilot -d copilot
