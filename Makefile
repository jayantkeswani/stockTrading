.PHONY: dev stop reset infra backend frontend migrate test clean hooks \
       infra-up infra-down infra-plan ssh db-export db-import prod-up prod-down prod-logs \
       release deploy-version show-version

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
	cd backend && source .venv/bin/activate && uvicorn app.main:app --reload --host :: --port 8080

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

# ── Release (Tag-Based Deploy) ───────────────────────────
release:             ## Create and push a release tag (usage: make release v=1.0.0)
	@if [ -z "$(v)" ]; then echo "Usage: make release v=1.0.0" && exit 1; fi
	@python3 -c "from datetime import datetime,timezone,timedelta; \
	ist=datetime.now(timezone(timedelta(hours=5,minutes=30))); \
	h,m=ist.hour,ist.minute; \
	mkt=(h==9 and m>=15) or (10<=h<15) or (h==15 and m<=30); \
	print('\033[33mWARNING: Market hours (9:15-15:30 IST). Deploy at your own risk.\033[0m') if mkt else None"
	@echo "Tagging v$(v)..."
	@PREV_TAG=$$(git describe --tags --abbrev=0 2>/dev/null || echo ""); \
	if [ -n "$$PREV_TAG" ]; then \
		CHANGELOG=$$(git log $$PREV_TAG..HEAD --oneline --no-decorate); \
	else \
		CHANGELOG=$$(git log --oneline --no-decorate -10); \
	fi; \
	if [ -n "$$CHANGELOG" ]; then \
		git tag -a "v$(v)" -m "v$(v)" -m "$$CHANGELOG"; \
	else \
		git tag -a "v$(v)" -m "v$(v)"; \
	fi
	git push origin "v$(v)"
	@echo "Tag v$(v) pushed. GitHub Actions deploy pipeline started."
	@echo "Monitor: https://github.com/jayantkeswani/stocktrading/actions"

deploy-version:      ## Deploy a specific version via workflow_dispatch (usage: make deploy-version v=1.0.0)
	@if [ -z "$(v)" ]; then echo "Usage: make deploy-version v=1.0.0" && exit 1; fi
	gh workflow run deploy.yml -f version="v$(v)"
	@echo "Deployment of v$(v) triggered via workflow_dispatch."

show-version:        ## Show what version is currently deployed in production
	@curl -sf http://$(VM_IP)/api/v1/health 2>/dev/null | python3 -m json.tool || \
	  curl -sf http://8.231.84.44/api/v1/health | python3 -m json.tool

help:                ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-20s\033[0m %s\n", $$1, $$2}'
