#!/usr/bin/env bash
set -euo pipefail
sentinel_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$sentinel_root"
umask 077
if [[ -f .env ]]; then
  set -a
  source .env
  set +a
fi
export PYTHONPATH="$sentinel_root/backend"
exec .venv/bin/uvicorn app.main:create_app --factory --host 127.0.0.1 --port "${SENTINEL_API_PORT:-8100}" --workers 1 --proxy-headers --forwarded-allow-ips=127.0.0.1 --no-access-log
