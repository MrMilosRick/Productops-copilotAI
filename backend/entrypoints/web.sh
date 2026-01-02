#!/usr/bin/env bash
set -euo pipefail

python - <<'PY'
import os, time
import psycopg

host = os.environ.get("POSTGRES_HOST", "db")
port = int(os.environ.get("POSTGRES_PORT", "5432"))
user = os.environ.get("POSTGRES_USER", "postgres")
password = os.environ.get("POSTGRES_PASSWORD", "postgres")
dbname = os.environ.get("POSTGRES_DB", "copilot")

dsn = f"host={host} port={port} user={user} password={password} dbname={dbname}"
for i in range(60):
    try:
        conn = psycopg.connect(dsn, connect_timeout=2)
        conn.close()
        print("DB is ready")
        break
    except Exception as e:
        print(f"Waiting for DB... ({i+1}/60) {e.__class__.__name__}")
        time.sleep(1)
else:
    raise SystemExit("DB not ready after retries")
PY

python manage.py migrate --noinput
exec gunicorn app.wsgi:application --bind 0.0.0.0:8000
