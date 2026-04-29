# Cross-Machine Setup

Development happens on the **primary laptop** (Jays-MacBook-Pro-2, Tailscale: 100.89.89.118).
The backend + frontend run on the **remote laptop** (Jays-MacBook-Pro, Tailscale: 100.95.114.17, LAN: 192.168.68.128).

Both machines are on the same Tailscale mesh and local Wi-Fi.

## Architecture

```
Primary Laptop (dev)                    Remote Laptop (server)
 ┌──────────────────┐                   ┌──────────────────────────┐
 │  Claude Code     │    git push/pull  │  Backend  :8080          │
 │  VS Code         │ ───────────────── │  Frontend :3000          │
 │  Browser ────────│──── HTTP/WS ────→ │  Postgres :5433          │
 │                  │    Tailscale VPN  │  Redis    :6380          │
 └──────────────────┘                   │  Docker Desktop          │
                                        └──────────────────────────┘
```

## Access Points

| Service       | URL from primary laptop               |
|---------------|---------------------------------------|
| Frontend UI   | http://100.95.114.17:3000             |
| Backend API   | http://100.95.114.17:8080             |
| WebSocket     | ws://100.95.114.17:8080/ws            |
| SSH           | ssh jaykeswani@100.95.114.17          |

## Regular Day (No Code Changes)

Nothing to do. As long as Docker Desktop and the backend/frontend processes are running on the remote laptop, everything works automatically — morning jobs (Fyers login at 7:45, screener at 8:30, briefing at 8:00) all run on their own.

Just open http://100.95.114.17:3000 from this laptop and watch.

### After a laptop restart (remote)
Docker Desktop auto-starts (if configured), but the backend and frontend processes do not survive a reboot. Start them:
```bash
ssh jaykeswani@100.95.114.17 "source ~/.zshrc; cd ~/projects/stockTrading/backend && source .venv/bin/activate && nohup uvicorn app.main:app --host 0.0.0.0 --port 8080 > /tmp/backend.log 2>&1 &"
ssh jaykeswani@100.95.114.17 "source ~/.zshrc; cd ~/projects/stockTrading/frontend && nohup npm run dev -- -H 0.0.0.0 > /tmp/frontend.log 2>&1 &"
```

## After Pushing Code Changes

Pull on the remote laptop and restart whichever service changed:
```bash
# Always pull first
ssh jaykeswani@100.95.114.17 "source ~/.zshrc; cd ~/projects/stockTrading && git pull"

# Restart backend (only if backend/ changed)
ssh jaykeswani@100.95.114.17 "source ~/.zshrc; cd ~/projects/stockTrading/backend && source .venv/bin/activate && pkill -f 'uvicorn app.main'; nohup uvicorn app.main:app --host 0.0.0.0 --port 8080 > /tmp/backend.log 2>&1 &"

# Restart frontend (only if frontend/ changed)
ssh jaykeswani@100.95.114.17 "source ~/.zshrc; cd ~/projects/stockTrading/frontend && pkill -f 'next dev'; nohup npm run dev -- -H 0.0.0.0 > /tmp/frontend.log 2>&1 &"
```

### Pull + restart everything in one command
```bash
ssh jaykeswani@100.95.114.17 "source ~/.zshrc; cd ~/projects/stockTrading && git pull && cd backend && source .venv/bin/activate && pkill -f 'uvicorn app.main'; nohup uvicorn app.main:app --host 0.0.0.0 --port 8080 > /tmp/backend.log 2>&1 & cd ../frontend && pkill -f 'next dev'; nohup npm run dev -- -H 0.0.0.0 > /tmp/frontend.log 2>&1 &"
```

### Useful check commands
```bash
# Check logs
ssh jaykeswani@100.95.114.17 "tail -30 /tmp/backend.log"
ssh jaykeswani@100.95.114.17 "tail -30 /tmp/frontend.log"

# Check if services are running
ssh jaykeswani@100.95.114.17 "pgrep -fa 'uvicorn|next dev'"

# Check Docker containers
ssh jaykeswani@100.95.114.17 "/usr/local/bin/docker ps"
```

## Inspecting Data from Primary Laptop

### Redis (port 6380)
```bash
# Direct redis-cli via SSH
ssh jaykeswani@100.95.114.17 "/usr/local/bin/docker exec st-redis redis-cli"

# Specific queries
ssh jaykeswani@100.95.114.17 "/usr/local/bin/docker exec st-redis redis-cli KEYS 'strat5:*'"
ssh jaykeswani@100.95.114.17 "/usr/local/bin/docker exec st-redis redis-cli GET 'strat5:phase:2026-04-29'"
ssh jaykeswani@100.95.114.17 "/usr/local/bin/docker exec st-redis redis-cli HGETALL 'watchlist:items'"
ssh jaykeswani@100.95.114.17 "/usr/local/bin/docker exec st-redis redis-cli GET 'fyers:access_token'" # check auth
ssh jaykeswani@100.95.114.17 "/usr/local/bin/docker exec st-redis redis-cli DBSIZE"  # total keys
```

### PostgreSQL (port 5433)
```bash
# Interactive psql
ssh jaykeswani@100.95.114.17 "/usr/local/bin/docker exec -it st-postgres psql -U trader stocktrading"

# One-shot queries
ssh jaykeswani@100.95.114.17 "/usr/local/bin/docker exec st-postgres psql -U trader stocktrading -c 'SELECT count(*) FROM trades WHERE date(created_at AT TIME ZONE '\''Asia/Kolkata'\'') = CURRENT_DATE'"
ssh jaykeswani@100.95.114.17 "/usr/local/bin/docker exec st-postgres psql -U trader stocktrading -c 'SELECT symbol, direction, entry_price, exit_price, pnl, status FROM trades ORDER BY created_at DESC LIMIT 10'"
ssh jaykeswani@100.95.114.17 "/usr/local/bin/docker exec st-postgres psql -U trader stocktrading -c 'SELECT count(*) FROM market_data_1m WHERE date(timestamp AT TIME ZONE '\''Asia/Kolkata'\'') = CURRENT_DATE'"
```

### Backend API (direct curl from primary laptop)
```bash
# Health check
curl -s http://100.95.114.17:8080/api/v1/health | python3 -m json.tool

# Market prices
curl -s http://100.95.114.17:8080/api/v1/market/prices | python3 -m json.tool

# Today's trades
curl -s http://100.95.114.17:8080/api/v1/trades?limit=10 | python3 -m json.tool

# Strategy 5 watchlist
curl -s http://100.95.114.17:8080/api/v1/intraday-futures/watchlist | python3 -m json.tool

# Background tasks status
curl -s http://100.95.114.17:8080/api/v1/tasks | python3 -m json.tool

# Fyers auth status
curl -s http://100.95.114.17:8080/api/v1/auth/fyers/status | python3 -m json.tool
```

## Key Differences from Localhost

1. **Fyers token**: The remote laptop runs its own `fyers_login_task` (daily 7:45 AM). The token is in its own Redis. Both laptops have the same Fyers credentials in `.env` but each maintains its own token.
2. **CORS**: Backend allows all origins (`allow_origins=["*"]`).
3. **Frontend host detection**: `getApiBase()` and `getWsUrl()` use `window.location.hostname` at runtime, so the browser always hits whichever server it loaded from.
4. **Next.js dev origins**: `next.config.ts` has `allowedDevOrigins: ["192.168.*.*", "100.*.*.*"]` to allow HMR from LAN/Tailscale IPs.
5. **Node.js version**: Remote laptop uses Node 22 via nvm (Next.js 15 requires >= 20.9.0).
6. **Docker path**: SSH non-login shells don't have Docker in PATH. Use `/usr/local/bin/docker`.
7. **Python version**: Remote venv uses Python 3.11.11 (ARM64) installed via pyenv at `~/.pyenv/versions/3.11.11`. The Homebrew default (`/opt/homebrew/bin/python3.11`) is 3.11.15 — **do not use it** to recreate the venv; curl_cffi's bundled libcurl fails with TLS resets on macOS 15.2 under that build (same root cause as the Fyers anyio regression). Always use `/Users/jaykeswani/.pyenv/versions/3.11.11/bin/python3.11` to recreate the remote venv.
8. **Yahoo Finance / curl_cffi**: curl_cffi's bundled libcurl fails on macOS 15.2 (remote) but works on macOS 15.7.3 (primary) — macOS version difference in system TLS frameworks. The global market task bypasses this by using httpx directly against Yahoo Finance's v8 chart API.

## Data Persistence

Both PostgreSQL and Redis use Docker named volumes:
- `st_postgres_data` — survives `docker compose down` / restarts
- `st_redis_data` — Redis data persists across Docker restarts

## Rebuilding the Remote venv

If the remote venv ever needs to be recreated (e.g. after a Python upgrade):

```bash
ssh jaykeswani@100.95.114.17 "
  cd ~/Projects/stockTrading/backend
  rm -rf .venv
  /Users/jaykeswani/.pyenv/versions/3.11.11/bin/python3.11 -m venv .venv
  source .venv/bin/activate
  pip install -e '.[dev]'
"
```

**Do not** use `/opt/homebrew/bin/python3.11` (3.11.15) — curl_cffi TLS fails under that build on macOS 15.2.

pyenv is installed at `/opt/homebrew/Cellar/pyenv`. To reinstall Python 3.11.11 if needed:
```bash
ssh jaykeswani@100.95.114.17 "
  export PYENV_ROOT=\$HOME/.pyenv
  export PATH=/opt/homebrew/bin:\$PYENV_ROOT/bin:\$PATH
  eval \"\$(/opt/homebrew/bin/pyenv init -)\"
  pyenv install 3.11.11
"
```

## Troubleshooting

| Symptom | Check |
|---------|-------|
| "WS OFF" in header | Backend running? `curl http://100.95.114.17:8080/api/v1/health` |
| No prices updating | Fyers token valid? `curl http://100.95.114.17:8080/api/v1/auth/fyers/status` |
| Frontend not loading | Node version? `ssh jaykeswani@100.95.114.17 "source ~/.zshrc; node -v"` (needs >= 20.9) |
| SSH fails | Tailscale up? `tailscale status` on both machines |
| Docker commands fail via SSH | Use full path: `/usr/local/bin/docker` |
| Global market cues all None | macOS TLS issue; httpx-based fetcher should handle it — check backend logs for 429s |
| yfinance fundamentals failing | curl_cffi TLS on macOS 15.2; non-critical, CAN SLIM degrades gracefully |
