#!/usr/bin/env bash
set -euo pipefail

BASE="${BASE:-http://localhost:8000}"
KW="UNICORN_B_123_$(date +%s)"

DOC_ID="$(curl -fsS -X POST "$BASE/api/kb/upload_text/" -H "Content-Type: application/json" \
  -d "{\"title\":\"Smoke Doc $KW\",\"content\":\"alpha alpha. $KW. omega omega.\"}" \
  | python -c "import sys,json; print(json.load(sys.stdin)[\"document_id\"])")"
echo "DOC_ID=$DOC_ID"

for i in $(seq 1 60); do
  STATUS="$(curl -fsS "$BASE/api/kb/documents/$DOC_ID/" | python -c "import sys,json; print(json.load(sys.stdin).get(\"status\"))")"
  echo "status=$STATUS ($i)"
  [ "$STATUS" = "embedded" ] && break
  sleep 1
done

RESP="$(curl -fsS -X POST "$BASE/api/ask/" -H "Content-Type: application/json" \
  -d "{\"question\":\"What keyword is in the doc?\",\"retriever\cho "$RESP" | python -c "import sys,json; r=json.load(sys.stdin); s=r[\"sources\"][0] if r.get(\"sources\") else {}; print(\"retriever_used=\", r.get(\"retriever_used\")); print(\"top_doc=\", s.get(\"document_id\")); print(\"snippet=\", (s.get(\"snippet\") or \"\")[:220])"

TOP_DOC="$(echo "$RESP" | python -c "import sys,json; r=json.load(sys.stdin); print(r[\"sources\"][0].get(\"document_id\"))")"
SNIP="$(echo "$RESP" | python -c "import sys,json; r=json.load(sys.stdin); print(r[\"sources\"][0].get(\"snippet\") or \"\")")"

[ "$TOP_DOC" = "$DOC_ID" ] || { echo "FAIL: top_doc=$TOP_DOC != DOC_ID=$DOC_ID"; exit 1; }
echo "$SNIP" | grep -Fq "$KW" || { echo "FAIL: keyword not found in snippet"; exit 1; }

echo "OK: smoke passed"
