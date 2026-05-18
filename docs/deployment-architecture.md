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
| `gemini-api-key` | API Key | Restricted to Generative Language API |
| `stock-trading-prod-terraform-state` | GCS Bucket | Terraform remote state (versioned) |

## Container Layout

| Container | Image | Port | Volume | Restart |
|-----------|-------|------|--------|---------|
| `st-nginx` | nginx:alpine | 80→80 | nginx.conf (bind) | unless-stopped |
| `st-backend` | custom (Python 3.11) | 8080 (internal) | backend_logs | unless-stopped |
| `st-frontend` | custom (Node 20) | 3000 (internal) | — | unless-stopped |
| `st-postgres` | postgres:16-alpine | 5432 (internal) | st_postgres_data | unless-stopped |
| `st-redis` | redis:7-alpine | 6379 (internal) | st_redis_data | unless-stopped |

In production, PostgreSQL and Redis are on container-internal ports only (not exposed to the host). The backend connects to them via Docker service names (`postgres:5432`, `redis:6379`).

## CI/CD Pipeline

```
git push origin master
        │
        ▼
GitHub Actions (.github/workflows/deploy.yml)
        │
        ├── SSH into VM as deploy@{IP}
        ├── git pull latest code
        ├── Write .env from GitHub Secrets
        ├── docker compose build + up
        ├── alembic upgrade head (migrations)
        └── Health check
```

Triggered on every push to `master` and via manual `workflow_dispatch`. Uses ~1-2 GitHub Actions minutes per deploy (well within the 500 min/month free tier for private repos).

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
| `GOOGLE_API_KEY` | Terraform output `gemini_api_key` | Backend AI/research |

## Network

- **External access**: HTTP on port 80 only (no HTTPS — single user, paper trading)
- **WebSocket**: nginx proxies `/ws` to backend:8080 with upgrade headers
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
