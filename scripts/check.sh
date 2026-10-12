#!/usr/bin/env bash
set -euo pipefail
sentinel_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$sentinel_root"
PYTHONPATH=backend .venv/bin/pytest backend/tests -q
cargo test --locked --manifest-path agent/Cargo.toml
cargo build --release --locked --manifest-path agent/Cargo.toml
cd frontend
npm run typecheck
npm run build
