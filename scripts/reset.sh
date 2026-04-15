#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

echo "[reset] Stopping everything..."
"$ROOT/scripts/stop.sh"

echo "[reset] Removing Docker volumes (database data)..."
docker compose down -v

echo "[reset] Removing backend venv..."
rm -rf "$ROOT/backend/.venv"

echo "[reset] Removing frontend build cache..."
rm -rf "$ROOT/frontend/.next" "$ROOT/frontend/node_modules"

echo "[reset] Clean slate. Run ./scripts/dev.sh to start fresh."
