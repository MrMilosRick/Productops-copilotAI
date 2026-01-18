URL ?= http://localhost:8001

wait:
	@for i in $$(seq 1 120); do \
	  curl -fsS "$(URL)/api/health/" >/dev/null 2>&1 && echo "web ready" && exit 0; \
	  sleep 1; \
	done; \
	echo "web not ready"; exit 1

smoke: wait
	@python3 smoke.py "$(URL)"
