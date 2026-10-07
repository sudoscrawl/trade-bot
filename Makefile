UV ?= uv

.DEFAULT_GOAL := help

.PHONY: help sync test migrate migration run docker-up docker-down docker-logs

help:
	@printf '%s\n' \
		'Available targets:' \
		'  make sync                         Install locked dependencies' \
		'  make test                         Run the test suite' \
		'  make migrate                      Apply database migrations' \
		"  make migration message='summary'  Create a migration" \
		'  make run                          Migrate, then start the bot' \
		'  make docker-up                    Start the bot in Docker' \
		'  make docker-down                  Stop the Docker bot' \
		'  make docker-logs                  Follow Docker logs'

sync:
	$(UV) sync --all-groups --locked

test:
	$(UV) run --group dev pytest -q

migrate:
	$(UV) run alembic upgrade head

migration:
	@test -n "$(message)" || (echo "Usage: make migration message='describe_change'" >&2; exit 2)
	$(UV) run alembic revision --autogenerate -m "$(message)"

run: migrate
	$(UV) run python -m bot.main

docker-up:
	mkdir -p logs
	@if docker compose version >/dev/null 2>&1; then \
		docker compose up --build -d; \
	else \
		docker build --tag quant-bot:latest .; \
		docker run --detach --name quant-bot --env-file .env --restart unless-stopped --volume "$(CURDIR)/logs:/app/logs" quant-bot:latest; \
	fi

docker-down:
	@if docker compose version >/dev/null 2>&1; then \
		docker compose down; \
	else \
		docker stop quant-bot 2>/dev/null || true; \
		docker rm quant-bot 2>/dev/null || true; \
	fi

docker-logs:
	@if docker compose version >/dev/null 2>&1; then \
		docker compose logs -f bot; \
	else \
		docker logs --follow quant-bot; \
	fi
