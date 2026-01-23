#!/usr/bin/env bash
set -euo pipefail

COMPOSE=(docker compose -f infra/docker-compose.yml)
BASE_URL="${BASE_URL:-http://localhost:8001}"
PDF_PATH="${PDF_PATH:-/tmp/wave.pdf}"

echo "[1/6] up"
"${COMPOSE[@]}" up -d --build

echo "[2/6] wait health"
for i in {1..60}; do
  code="$(curl -s -o /dev/null -w "%{http_code}" "$BASE_URL/api/health/" || true)"
  if [ "$code" = "200" ]; then
    echo "health OK"
    break
  fi
  sleep 1
done

echo "[3/6] upload pdf"
resp="$(
  curl -fsS -X POST "$BASE_URL/api/kb/upload_file/" \
    -F "file=@${PDF_PATH};type=application/pdf" \
    -F "title=Волна упаковки"
)"
echo "$resp"

doc_id="$(echo "$resp" | jq -r '.document_id // empty')"
if [ -z "$doc_id" ]; then
  echo "ERR: document_id not found in upload response"
  exit 1
fi

echo "[4/6] wait embedded doc_id=$doc_id"
for i in {1..90}; do
  st="$(
    curl -fsS "$BASE_URL/api/kb/documents/" \
      | jq -r --arg id "$doc_id" '.[] | select(.id == ($id|tonumber)) | .status' \
      | head -n 1
  )"
  if [ "$st" = "embedded" ]; then
    echo "embedded OK"
    break
  fi
  sleep 1
done

echo "[5/6] ask"
ask_resp="$(
  curl -fsS -X POST "$BASE_URL/api/ask/" \
    -H 'Content-Type: application/json' \
    -d "{\"question\":\"О чем документ Волна упаковки?\",\"retriever\":\"keyword\",\"top_k\":5,\"answer_mode\":\"deterministic\",\"document_id\":$doc_id}"
)"
echo "$ask_resp" | jq .

echo "$ask_resp" | jq -e '.answer and (.answer|length>0) and (.sources|type=="array") and (.sources|length>0)' >/dev/null
echo "$ask_resp" | jq -e --arg id "$doc_id" 'all(.sources[]; (.document_id|tostring)==$id)' >/dev/null || { echo "ERR: sources not scoped to doc_id"; exit 1; }
echo "ASK OK"

echo "[6/6] logs (tail)"
"${COMPOSE[@]}" logs --no-color --tail=80 web
"${COMPOSE[@]}" logs --no-color --tail=80 worker

echo "SMOKE PASSED"
