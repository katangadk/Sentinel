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
exec .venv/bin/python scripts/phone-link.py
