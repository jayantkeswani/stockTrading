.PHONY: dev stop reset infra backend frontend migrate test clean

# ── One touch ──────────────────────────────────────────────
dev:                 ## Start everything (infra + backend + frontend)
	@./scripts/dev.sh

stop:                ## Stop all running services
	@./scripts/stop.sh

reset:               ## Full reset (delete venv, node_modules, DB volumes)
	@./scripts/reset.sh

# ── Individual services ───────────────────────────────────
infra:               ## Start PostgreSQL + Redis only
	docker compose up -d --wait

backend:             ## Start backend only (assumes infra is up)
	cd backend && source .venv/bin/activate && uvicorn app.main:app --reload --host 0.0.0.0 --port 8080

frontend:            ## Start frontend only
	cd frontend && npm run dev

# ── Database ──────────────────────────────────────────────
migrate:             ## Run pending migrations
	cd backend && source .venv/bin/activate && alembic upgrade head

migration:           ## Generate new migration (usage: make migration msg="add users table")
	cd backend && source .venv/bin/activate && alembic revision --autogenerate -m "$(msg)"

# ── Testing ───────────────────────────────────────────────
test:                ## Run backend tests
	cd backend && source .venv/bin/activate && python -m pytest tests/ -v

# ── Cleanup ───────────────────────────────────────────────
clean:               ## Stop containers and remove volumes
	docker compose down -v

help:                ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-20s\033[0m %s\n", $$1, $$2}'
