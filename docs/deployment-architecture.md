# Deployment Architecture

## Overview

Single GCE VM deployment on Google Cloud Platform. All services run as Docker containers managed by Docker Compose. Infrastructure is defined as code via Terraform and can be created or destroyed with a single command.

## Architecture Diagram

```
                    ┌──────────────────────────────────────────────┐
                    │  GCE VM (e2-small, asia-south1-a)            │
                    │  Debian 12 · Docker · IST timezone           │
                    │                                              │
   HTTP :80        │  ┌──────────────────────────────────────┐    │
  ──────────────►  │  │  Nginx (reverse proxy)               │    │
                    │  │  /api/*, /ws → backend:8080          │    │
                    │  │  /*         → frontend:3000          │    │
                    │  └──────┬──────────────┬────────────────┘    │
                    │         │              │                      │
                    │  ┌──────▼──────┐ ┌────▼─────────┐           │
                    │  │  Backend    │ │  Frontend     │           │
                    │  │  FastAPI    │ │  Next.js      │           │
                    │  │  :8080      │ │  :3000        │           │
                    │  └──────┬──────┘ └──────────────┘           │
                    │         │                                    │
                    │  ┌──────▼──────┐ ┌──────────────┐           │
                    │  │ PostgreSQL  │ │  Redis        │           │
                    │  │  :5432      │ │  :6379        │           │
                    │  └─────────────┘ └──────────────┘           │
                    │                                              │
                    └──────────────────────────────────────────────┘
                              │                    │
                              ▼                    ▼
                    Fyers API (WS + REST)    Telegram Bot API
```

## GCP Resources (Terraform-managed)

| Resource | Type | Details |
|----------|------|---------|
| `stock-trading-vm` | GCE Instance | e2-small (2 vCPU, 2GB RAM), 30GB SSD, Debian 12 |
| `stock-trading-ip` | Static IP | asia-south1, attached to VM |
| `stock-trading-allow-http` | Firewall | Allow TCP 80 from 0.0.0.0/0 |
| `stock-trading-allow-ssh` | Firewall | Allow TCP 22 from 0.0.0.0/0 |
| `stock-trading-vm` | Service Account | Minimal privilege for VM |
| `gemini-api-key` | API Key | Restricted to Generative Language API (AI Studio fallback) |
| `vm_vertex_user` | IAM Binding | `roles/aiplatform.user` on VM service account (Vertex AI) |
| `stock-trading-prod-terraform-state` | GCS Bucket | Terraform remote state (versioned) |

## Container Layout

| Container | Image | Port | Volume | Restart |
|-----------|-------|------|--------|---------|
| `st-nginx` | nginx:alpine | 80→80 | nginx.conf (bind); DNS re-resolution via `resolver 127.0.0.11 valid=5s` | unless-stopped |
| `st-backend` | ghcr.io/.../backend (Python 3.11) | 8080 (internal) | backend_logs; healthcheck: `curl /api/v1/tasks` (60s start_period) | unless-stopped |
| `st-frontend` | ghcr.io/.../frontend (Node 20) | 3000 (internal) | — | unless-stopped |
| `st-postgres` | postgres:16-alpine | 5432 (internal) | st_postgres_data | unless-stopped |
| `st-redis` | redis:7-alpine | 6379 (internal) | st_redis_data | unless-stopped |

In production, PostgreSQL and Redis are on container-internal ports only (not exposed to the host). The backend connects to them via Docker service names (`postgres:5432`, `redis:6379`).

## CI/CD Pipeline (Tag-Based Releases)

> **SAFE TO PUSH TO MASTER.** Master pushes only build images (CI validation). Deploys are triggered by semver tags.

### Build (master push — no deploy) — `ci.yml`

```
git push origin master
        │
        ▼
GitHub Actions (ci.yml): build job
        ├── Docker Buildx + GitHub Actions cache (GHA)
        ├── Build backend → ghcr.io/.../backend:<sha> + :latest
        └── Build frontend → ghcr.io/.../frontend:<sha> + :latest
```

### Deploy (tag push — no rebuild) — `deploy.yml`

```
make release v=1.0.0   (creates + pushes tag)
        │
        ▼
GitHub Actions (deploy.yml): retag job
        ├── docker buildx imagetools create (registry-side, ~5s)
        ├── Retag :<sha> as :v1.0.0 for both images
        │
        ▼
GitHub Actions (deploy.yml): deploy job
        ├── SCP docker-compose.prod.yml + nginx.conf to VM
        ├── SSH into VM as deploy@{IP}
        ├── Write .env from GitHub Secrets (includes APP_VERSION=v1.0.0)
        ├── docker pull :v1.0.0 images
        ├── alembic upgrade head (migrations)
        ├── docker compose up -d
        └── Health check (verifies version in /api/v1/health)
```

Images are built on the GitHub runner (7GB RAM, free) and pushed to GitHub Container Registry (ghcr.io, currently free for containers). The VM only pulls pre-built images — no builds on the e2-small. Docker layer caching via GitHub Actions cache (`type=gha`) makes subsequent builds fast (~30s when only source changes). Releases reuse existing images (retag only), so deploys take ~1 min. Manual `workflow_dispatch` available as an escape hatch.

### Version Visibility

- `GET /api/v1/health` returns `{"version": "v1.0.0", "deployed_at": "..."}` in production
- Frontend Header shows the deployed version next to the WS indicator
- `make show-version` prints the health response from local machine
- `APP_VERSION` env var written to `.env` on VM during each deploy

## Secrets Flow

```
GitHub Repo Secrets (manually added once)
        │
        ▼ (deploy workflow)
.env file on VM (written each deploy)
        │
        ▼ (docker compose env_file)
Backend container environment variables
```

Secrets **never** live in the codebase or in GCP Secret Manager. They flow from GitHub Secrets to the VM's `.env` file on every deploy.

| Secret | Source | Used By |
|--------|--------|---------|
| `SSH_PRIVATE_KEY` | Terraform output `deploy_private_key` | GitHub Actions → SSH |
| `GCP_VM_IP` | Terraform output `vm_external_ip` | GitHub Actions → SSH target |
| `DB_PASSWORD` | User-chosen | PostgreSQL + backend |
| `FYERS_APP_ID` | Fyers developer portal | Backend data feed |
| `FYERS_SECRET_KEY` | Fyers developer portal | Backend data feed |
| `FYERS_REDIRECT_URI` | Fyers developer portal | Backend OAuth |
| `FYERS_USERNAME` | User's Fyers login | Backend auto-login |
| `FYERS_PIN` | User's Fyers PIN | Backend auto-login |
| `FYERS_TOTP_SECRET` | User's TOTP seed | Backend auto-login |
| `TELEGRAM_BOT_TOKEN` | Telegram BotFather | Backend notifications |
| `TELEGRAM_CHAT_ID` | Telegram | Backend notifications |
| `GOOGLE_API_KEY` | Terraform output `gemini_api_key` | Backend AI/research (AI Studio fallback, local dev) |

Non-secret env vars hardcoded in the deploy workflow:

| Variable | Value | Used By |
|----------|-------|---------|
| `GCP_PROJECT_ID` | `stock-trading-prod` | Backend Vertex AI mode (ADC via GCE metadata server) |
| `VERTEX_AI_LOCATION` | `global` | Gemini API global endpoint (auto-routes to nearest region) |

## Network

- **Live URL**: http://8.231.84.44
- **External access**: HTTP on port 80 only (no HTTPS — single user, paper trading)
- **WebSocket**: nginx proxies `/ws` to backend:8080 with upgrade headers
- **Nginx DNS re-resolution**: `resolver 127.0.0.11 valid=5s` + variable-based `proxy_pass` — nginx picks up new container IPs after deploys without manual `nginx -s reload`
- **Fyers data feed**: outbound WebSocket + REST to Fyers servers
- **Telegram**: outbound HTTPS to api.telegram.org

## Cost Estimate

| Resource | Monthly | Notes |
|----------|---------|-------|
| e2-small VM (24/7) | ~$15 | asia-south1 |
| 30GB SSD disk | ~$5 | pd-ssd |
| Static IP (attached) | $0 | Free while VM runs |
| Network egress | ~$1 | Minimal (single user) |
| **Total** | **~$21/mo** | **~$63 for 90-day trial** |

## Production Debugging

### Container Names

All production containers use the `st-` prefix (defined in `docker-compose.prod.yml`):

| Container | Service |
|-----------|---------|
| `st-backend` | FastAPI backend |
| `st-frontend` | Next.js frontend |
| `st-postgres` | PostgreSQL 16 |
| `st-redis` | Redis 7 |
| `st-nginx` | Nginx reverse proxy |

### Viewing Logs

Backend logs go to **stdout only** — there is no `app.log` file on disk. Use `docker logs`:

```bash
# SSH in first
make ssh
# or: ssh -i ~/.ssh/st-deploy deploy@8.231.84.44

# Tail live logs
docker logs -f st-backend

# Last 200 lines
docker logs --tail 200 st-backend

# Search for errors
docker logs st-backend 2>&1 | grep -i ERROR | tail -50

# Filter by module (e.g., AI confidence failures)
docker logs st-backend 2>&1 | grep 'AI confidence overlay failed'

# All containers via compose (from /opt/stock-trading)
cd /opt/stock-trading && docker compose -f docker-compose.prod.yml logs -f --tail=100
```

Or without SSH (single-command from local machine):

```bash
# Quick error scan
ssh -i ~/.ssh/st-deploy deploy@8.231.84.44 "docker logs st-backend 2>&1 | grep -i ERROR | tail -50"

# Check container status
ssh -i ~/.ssh/st-deploy deploy@8.231.84.44 "docker ps --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}'"
```

### Common Issues

| Symptom | Check |
|---------|-------|
| AI confidence overlay failures | `docker logs st-backend 2>&1 \| grep 'AI confidence overlay failed'` — usually Gemini rate limits (429) |
| Backend not starting | `docker logs st-backend 2>&1 \| head -50` — check for import errors or missing env vars |
| DB connection errors | `docker exec st-postgres pg_isready` and check `DATABASE_URL` in `.env` |
| Redis connection errors | `docker exec st-redis redis-cli ping` — should return PONG |
| Nginx 502 Bad Gateway | `docker logs st-nginx` — backend container may be restarting; check backend logs |

## Teardown

One command destroys everything:
```bash
make infra-down
# or: cd infrastructure/terraform && terraform destroy
```

This removes the VM, disk, firewall rules, static IP, service account, and Gemini API key. The Terraform state bucket must be deleted manually (it's the bootstrap resource that Terraform itself depends on).

## Local vs Production Differences

| Aspect | Local (dev) | Production (VM) |
|--------|-------------|-----------------|
| PostgreSQL port | 5433 (host) | 5432 (container-internal) |
| Redis port | 6380 (host) | 6379 (container-internal) |
| Backend | Host process (uvicorn --reload) | Docker container |
| Frontend | Host process (next dev) | Docker container (standalone) |
| Reverse proxy | None (direct ports) | Nginx on port 80 |
| API URL detection | `http://{host}:8080` | `http://{host}` (port 80) |
| WebSocket URL | `ws://{host}:8080/ws` | `ws://{host}/ws` |
