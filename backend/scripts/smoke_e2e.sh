#!/usr/bin/env bash
set -euo pipefail

BASE="${BASE:-http://localhost:8000}"
KW="UNICORN_B_123_$(date +%s)"

UPLOAD_JSON="$(curl -fsS -X POST "${BASE}/api/kb/upload_text/" \
  -H "Content-Type: application/json" \
  -d "{\"title\":\"Smoke Doc ${KW}\",\"content\":\"alpha alpha. ${KW}. omega omega.\"}")"

DOC_ID="$(printf "%s" "$UPLOAD_JSON" | python -c 'import sys,json; print(json.load(sys.stdin)["document_id"])')"
echo "DOC_ID=$DOC_ID"

for i in $(seq 1 60); do
  DOC_JSON="$(curl -fsS "${BASE}/api/kb/documents/${DOC_ID}/")"
  STATUS="$(printf "%s" "$DOC_JSON" | python -c 'import sys,json; print(json.load(sys.stdin).get("status"))')"
  echo "status=$STATUS ($i)"
  [ "$STATUS" = "embedded" ] && break
  sleep 1
done

ASK_JSON="$(curl -fsS -X POST "${BASE}/api/ask/" \
  -H "Content-Type: application/json" \
  -d "{\"question\":\"What keyword is in the doc?\",\"retriever\":\"auto\",\"top_k\":1,\"document_id\":${DOC_ID}}")"

printf "%s\n" "$ASK_JSON" | python -c 'import sys,json; r=json.load(sys.stdin); s=(r.get("sources") or [None])[0] or {}; print("retriever_used=", r.get("retriever_used")); print("top_doc=", s.get("document_id")); print("snippet=", (s.get("snippet") or "")[:220])'

TOP_DOC="$(printf "%s" "$ASK_JSON" | python -c 'import sys,json; r=json.load(sys.stdin); print((r.get("sources") or [{}])[0].get("document_id"))')"
SNIP="$(printf "%s" "$ASK_JSON" | python -c 'import sys,json; r=json.load(sys.stdin); print(((r.get("sources") or [{}])[0].get("snippet")) or "")')"

[ "$TOP_DOC" = "$DOC_ID" ] || { echo "FAIL: top_doc=$TOP_DOC != DOC_ID=$DOC_ID"; exit 1; }
printf "%s" "$SNIP" | grep -Fq "$KW" || { echo "FAIL: keyword not found in snippet"; exit 1; }

echo "OK: smoke passed"
