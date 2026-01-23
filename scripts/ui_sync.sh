#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

if [ ! -d "frontend/dist" ]; then
  echo "ERROR: frontend/dist not found. Run: (cd frontend && npm run build)"
  exit 1
fi

echo "Syncing frontend/dist -> backend/ui/static/ui ..."
mkdir -p backend/ui/static/ui
rm -rf backend/ui/static/ui/*
cp -R frontend/dist/* backend/ui/static/ui/

CSS="$(ls -1 frontend/dist/assets/*.css | head -n 1 | xargs -n1 basename)"
JS="$(ls -1 frontend/dist/assets/*.js  | head -n 1 | xargs -n1 basename)"

echo "CSS=$CSS"
echo "JS=$JS"

cat > backend/ui/templates/ui/index.html <<HTML
{% load static %}
<!doctype html>
<html lang="en">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>ProductOps Copilot</title>
    <link rel="stylesheet" href="{% static 'ui/assets/$CSS' %}">
  </head>
  <body>
    <div id="root"></div>
    <script type="module" src="{% static 'ui/assets/$JS' %}"></script>
  </body>
</html>
HTML

echo "OK: ui-sync"
