#!/usr/bin/env bash
set -euo pipefail
sentinel_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$sentinel_root"
if [[ -f .env ]]; then
  set -a
  source .env
  set +a
fi
# Backend secrets must not enter the dashboard process environment.
unset GOOGLE_GEOLOCATION_API_KEY SENTINEL_VAPID_PRIVATE_KEY
export NEXT_TELEMETRY_DISABLED=1
cd frontend
exec node node_modules/next/dist/bin/next start --hostname 127.0.0.1 --port "${SENTINEL_FRONTEND_PORT:-3100}"
