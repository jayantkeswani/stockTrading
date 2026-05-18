.PHONY: dev stop reset infra backend frontend migrate test clean hooks \
       infra-up infra-down infra-plan ssh db-export db-import prod-up prod-down prod-logs

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

hooks:               ## Activate graphify git hooks (run once after fresh clone)
	git config core.hooksPath .githooks
	@echo "Git hooks activated from .githooks/"

# ── GCP / Terraform ──────────────────────────────────────
infra-up:            ## Terraform apply (create/update GCP infrastructure)
	cd infrastructure/terraform && terraform apply

infra-down:          ## Terraform destroy (tear down ALL GCP infrastructure)
	cd infrastructure/terraform && terraform destroy

infra-plan:          ## Terraform plan (preview changes)
	cd infrastructure/terraform && terraform plan

# ── Production (VM) ──────────────────────────────────────
VM_IP := $(shell cd infrastructure/terraform 2>/dev/null && terraform output -raw vm_external_ip 2>/dev/null)

ssh:                 ## SSH into the production VM
	ssh -i ~/.ssh/st-deploy deploy@$(VM_IP)

prod-up:             ## Start production stack on VM
	ssh -i ~/.ssh/st-deploy deploy@$(VM_IP) "cd /opt/stock-trading && docker compose -f docker-compose.prod.yml up -d"

prod-down:           ## Stop production stack on VM
	ssh -i ~/.ssh/st-deploy deploy@$(VM_IP) "cd /opt/stock-trading && docker compose -f docker-compose.prod.yml down"

prod-logs:           ## Tail production logs on VM
	ssh -i ~/.ssh/st-deploy deploy@$(VM_IP) "cd /opt/stock-trading && docker compose -f docker-compose.prod.yml logs -f --tail=100"

# ── Database Migration ───────────────────────────────────
db-export:           ## Export local DB to dump.sql
	docker exec st-postgres pg_dump -U trader stocktrading > dump.sql
	@echo "Exported to dump.sql"

db-import:           ## Import dump.sql to production VM DB
	scp -i ~/.ssh/st-deploy dump.sql deploy@$(VM_IP):/opt/stock-trading/
	ssh -i ~/.ssh/st-deploy deploy@$(VM_IP) "cd /opt/stock-trading && docker exec -i st-postgres psql -U trader -d stocktrading < dump.sql"
	@echo "Import complete"

help:                ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-20s\033[0m %s\n", $$1, $$2}'
