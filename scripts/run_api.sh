#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
exec .venv/bin/python -m uvicorn src.serve.api:app \
  --host "${API_HOST:-127.0.0.1}" \
  --port "${API_PORT:-8000}" \
  --workers 1
