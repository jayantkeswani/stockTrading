---
name: update-docs
description: Sync all CLAUDE.md and documentation files with the current codebase state
user_invocable: true
---

Update all AI documentation files in the stockTrading project to match the actual code.

## Steps

1. `cd /Users/cadenceproinc/projects/stockTrading`
2. Scan the current directory structure using glob/find
3. Read each documentation file:
   - `CLAUDE.md` (root — project overview, directory map, commands)
   - `backend/CLAUDE.md` (module map, conventions, how-to guides)
   - `frontend/CLAUDE.md` (component map, theme, conventions, how-to guides)
   - `ARCHITECTURE.md` (system design, data flows)
4. For each doc, compare documented state vs actual files on disk:
   - New files/modules not documented → add them
   - Removed files still listed → remove them
   - Changed conventions → update them
   - New API endpoints → add to endpoint list
   - New components/pages → add to component map
5. Update `docs/strategies/*.md` if any strategy logic has changed
6. Verify these are accurate across all docs:
   - Python 3.11, venv at `backend/.venv`
   - Backend port: 8080
   - All 5 indices: NIFTY, BANKNIFTY, FINNIFTY, SENSEX, MIDCPNIFTY
   - PostgreSQL port 5433, Redis port 6380
7. Report what was updated

## Why This Matters

These CLAUDE.md files are the primary context for AI assistants working on this codebase. If they're wrong, AI makes wrong assumptions. Keep them accurate — they're more important than README.md.
