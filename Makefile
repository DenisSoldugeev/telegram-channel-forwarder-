.PHONY: help build up down logs restart clean deploy

# Default target
help:
	@echo "Telegram Forwarder Bot - Docker Commands"
	@echo ""
	@echo "Production (PostgreSQL):"
	@echo "  make build      - Build Docker image"
	@echo "  make up         - Start bot + PostgreSQL"
	@echo "  make down       - Stop all containers"
	@echo "  make logs       - View bot logs"
	@echo "  make restart    - Restart bot container"
	@echo ""
	@echo "Lightweight (SQLite):"
	@echo "  make up-sqlite  - Start bot with SQLite only"
	@echo ""
	@echo "Maintenance:"
	@echo "  make clean      - Remove containers and volumes"
	@echo "  make shell      - Open shell in bot container"
	@echo "  make db-shell   - Open PostgreSQL shell"
	@echo ""
	@echo "Deployment:"
	@echo "  make deploy     - Full deploy (build + up)"
	@echo "  make update     - Pull latest and restart"

# ============================================
# Production (PostgreSQL)
# ============================================

build:
	docker compose build

up:
	docker compose up -d

down:
	docker compose down

logs:
	docker compose logs -f bot

restart:
	docker compose restart bot

# ============================================
# Lightweight (SQLite)
# ============================================

up-sqlite:
	docker compose -f docker-compose.sqlite.yml up -d

down-sqlite:
	docker compose -f docker-compose.sqlite.yml down

logs-sqlite:
	docker compose -f docker-compose.sqlite.yml logs -f

# ============================================
# Maintenance
# ============================================

clean:
	docker compose down -v --remove-orphans
	docker image prune -f

shell:
	docker compose exec bot /bin/bash

db-shell:
	docker compose exec db psql -U postgres -d tg_forward_bot

status:
	docker compose ps

# ============================================
# Deployment
# ============================================

deploy: build up
	@echo "Deployment complete!"
	@docker compose ps

update:
	git pull
	docker compose build
	docker compose up -d
	@echo "Update complete!"
	@docker compose ps
