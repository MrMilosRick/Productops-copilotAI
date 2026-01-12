.DEFAULT_GOAL := help

help:
	@echo "Targets:"
	@echo "  make up        - build+start docker compose"
	@echo "  make down      - stop"
	@echo "  make logs      - tail logs"
	@echo "  make wait      - wait until /api/health/ is ready"
	@echo "  make health    - print /api/health/"
	@echo "  make demo      - upload demo doc + list docs
	@echo "  make demo-clean - delete demo docs from DB""
	@echo "  make psql      - open psql inside db"

up:
	cd infra && docker compose up -d --build

down:
	cd infra && docker compose down

logs:
	cd infra && docker compose logs -f --tail=200 web worker

wait:
	@echo "Waiting for web..."
	@cd infra && \
	for i in $$(seq 1 120); do \
	  if docker compose exec -T web curl --connect-timeout 1 --max-time 2 -fsS http://localhost:8000/api/health/ >/dev/null 2>&1; then \
	    echo "OK"; exit 0; \
	  fi; \
	  echo "  ... ($$i)"; \
	  sleep 1; \
	done; \
	echo "ERROR: web not ready"; \
	docker compose logs --no-color --tail=200 web; \
	exit 1

health: wait
	@cd infra && docker compose exec -T web curl -fsS http://localhost:8000/api/health/ && echo

demo: wait
	@cd infra && docker compose exec -T web curl -fsS -X POST "http://localhost:8000/api/kb/upload_text/" \
	  -H "Content-Type: application/json" \
	  -d "{\"title\":\"Demo Doc $$(date +%s)-$$$$\",\"content\":\"Hello world. This is a demo document for chunking.\"}" && echo
	@sleep 1
	@cd infra && docker compose exec -T web curl -fsS "http://localhost:8000/api/kb/documents/" && echo
psql:
	cd infra && docker compose exec db psql -U copilot -d copilot
reset:
	cd infra && docker compose down -v
fresh: reset up demo

all: fresh

smoke: fresh
	@echo "SMOKE: DB counts"
	@cd infra && docker compose exec -T db psql -U copilot -d copilot -Atc "select count(*) from copilot_document;" | grep -qx "1"
	@cd infra && docker compose exec -T db psql -U copilot -d copilot -Atc "select count(*) from copilot_embeddingchunk where document_id=1;" | grep -qx "1"
	@echo "SMOKE: worker succeeded doc=1"
	@cd infra && docker compose logs --no-color --tail=200 worker | egrep "succeeded.*document_id.: 1" >/dev/null
	@echo "OK: smoke passed"

ci-up:
	cd infra && docker compose up -d --build

ci: ci-up health demo
demo-clean: wait
	@echo "Deleting demo docs..."
	@cd infra && docker compose exec -T db psql -U copilot -d copilot -Atc \
	  "delete from copilot_document where title like 'Demo Doc %';" >/dev/null
	@echo "OK"

