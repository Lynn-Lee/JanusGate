#!/usr/bin/env bash
# JanusGate Cloud Agent install: refresh repository dependencies after checkout.
# Idempotent and safe to re-run. Does NOT start long-running services (see
# .cursor/start.sh) and does NOT run migrations (they need a running DB).
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

export PATH="$HOME/.local/bin:$PATH"

# uv is the backend package manager (pinned by backend/uv.lock). Install it if the
# base image did not provide it (e.g. default image instead of .cursor/Dockerfile).
if ! command -v uv >/dev/null 2>&1; then
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="$HOME/.local/bin:$PATH"
fi

# Local development .env (gitignored). Created once with a generated SECRET_KEY
# and localhost service URLs; never committed.
if [ ! -f .env ]; then
  echo "[install] creating local .env from .env.example"
  SECRET_KEY="$(python3 -c 'import secrets; print(secrets.token_hex(32))')"
  sed \
    -e "s|^POSTGRES_PASSWORD=.*|POSTGRES_PASSWORD=janusgate|" \
    -e "s|^DATABASE_URL=.*|DATABASE_URL=postgresql+asyncpg://janusgate:janusgate@127.0.0.1:5432/janusgate|" \
    -e "s|^REDIS_URL=.*|REDIS_URL=redis://127.0.0.1:6379/0|" \
    -e "s|^SECRET_KEY=.*|SECRET_KEY=${SECRET_KEY}|" \
    .env.example > .env
fi

# Backend: create/refresh the virtualenv from the pinned lockfile (incl. dev tools).
echo "[install] syncing backend dependencies with uv"
cd "$REPO_ROOT/backend"
ln -sf ../.env .env
uv sync --extra dev

# Frontend: install locked dependencies.
echo "[install] installing frontend dependencies with npm ci"
cd "$REPO_ROOT/frontend"
npm ci

echo "[install] done"
