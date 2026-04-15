#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

# Colors
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
CYAN='\033[0;36m'
NC='\033[0m'

log()  { echo -e "${GREEN}[dev]${NC} $1"; }
warn() { echo -e "${YELLOW}[dev]${NC} $1"; }
err()  { echo -e "${RED}[dev]${NC} $1"; }

cleanup() {
  log "Shutting down..."
  kill $BACKEND_PID $FRONTEND_PID 2>/dev/null || true
  wait $BACKEND_PID $FRONTEND_PID 2>/dev/null || true
  log "Done."
}
trap cleanup EXIT INT TERM

# ── 1. Infrastructure ──────────────────────────────────────
log "Starting PostgreSQL + Redis..."
docker compose up -d --wait 2>/dev/null || {
  err "Docker failed. Is Docker Desktop running?"
  exit 1
}
log "Infrastructure ready."

# ── 2. Backend venv ────────────────────────────────────────
if [ ! -d "$ROOT/backend/.venv" ]; then
  log "Creating Python venv..."
  /opt/homebrew/bin/python3.11 -m venv "$ROOT/backend/.venv"
  source "$ROOT/backend/.venv/bin/activate"
  pip install --quiet --upgrade pip setuptools
  pip install --quiet -e "$ROOT/backend[dev]"
else
  source "$ROOT/backend/.venv/bin/activate"
fi

# ── 3. Migrations ──────────────────────────────────────────
cd "$ROOT/backend"
MIGRATION_COUNT=$(ls alembic/versions/*.py 2>/dev/null | wc -l | tr -d ' ')
if [ "$MIGRATION_COUNT" = "0" ]; then
  log "Generating initial migration..."
  alembic revision --autogenerate -m "initial schema" 2>/dev/null || warn "Migration generation failed"
fi
log "Running database migrations..."
alembic upgrade head 2>/dev/null || warn "Migration failed — check DB connection"

# ── 4. .env ────────────────────────────────────────────────
if [ ! -f "$ROOT/.env" ]; then
  cp "$ROOT/.env.example" "$ROOT/.env"
  warn "Created .env from .env.example — edit it with your API keys"
fi
if [ ! -f "$ROOT/backend/.env" ]; then
  ln -sf "$ROOT/.env" "$ROOT/backend/.env"
fi

# ── 5. Frontend deps ──────────────────────────────────────
if [ ! -d "$ROOT/frontend/node_modules" ]; then
  log "Installing frontend dependencies..."
  cd "$ROOT/frontend" && npm install --silent
fi

# ── 6. Launch ──────────────────────────────────────────────
echo ""
echo -e "${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${CYAN}  StockTrading Dev Environment${NC}"
echo -e "${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "  ${GREEN}Dashboard${NC}  → http://localhost:3000"
echo -e "  ${GREEN}API${NC}        → http://localhost:8080"
echo -e "  ${GREEN}API Docs${NC}   → http://localhost:8080/docs"
echo -e "  ${GREEN}DB${NC}         → postgresql://trader:trader_dev_123@localhost:5432/stocktrading"
echo -e "  ${GREEN}Redis${NC}      → redis://localhost:6379"
echo -e "${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo ""

# Start backend
cd "$ROOT/backend"
source .venv/bin/activate
uvicorn app.main:app --reload --host 0.0.0.0 --port 8080 &
BACKEND_PID=$!

# Start frontend
cd "$ROOT/frontend"
npm run dev -- --port 3000 &
FRONTEND_PID=$!

log "Press Ctrl+C to stop everything."
wait
