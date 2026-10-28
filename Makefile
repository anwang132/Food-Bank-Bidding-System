# Common tasks. Requires Docker (Compose v2), uv, and Node.js 22+.
SHELL := /bin/bash
COMPOSE := docker compose
TEST_ENV := FB_TEST_DATABASE_URL=postgresql+psycopg://foodbank:foodbank@localhost:55432/foodbank_test \
            FB_TEST_REDIS_URL=redis://localhost:56379/1

.PHONY: up down seed reset-demo logs install check test test-backend test-frontend e2e \
        e2e-realtime test-stack perf

up:            ## Build and start the stack (Postgres, Redis, migrations, 2 APIs, nginx)
	$(COMPOSE) up -d --build
	@until curl -fsS http://localhost:8080/healthz >/dev/null; do sleep 1; done
	@echo "App: http://localhost:8080"

down:          ## Stop the stack (keeps the database volume)
	$(COMPOSE) down

seed:          ## Load synthetic demo data into an empty database
	$(COMPOSE) exec -T api-1 python -m app.cli seed

reset-demo:    ## Wipe the local demo database and reseed it
	$(COMPOSE) exec -T api-1 python -m app.cli reset-demo --confirm foodbank

logs:
	$(COMPOSE) logs -f api-1 api-2

install:       ## Install local toolchains for tests and linting
	cd backend && uv sync
	cd frontend && npm ci && npx playwright install chromium

check:         ## Formatting, lint and type checks
	cd backend && uv run ruff format --check . && uv run ruff check . && uv run mypy app
	cd frontend && npm run lint && npm run typecheck

test: test-backend test-frontend

test-backend:  ## Unit, PostgreSQL integration, API and in-process realtime tests (needs db + redis)
	$(COMPOSE) up -d db redis
	cd backend && $(TEST_ENV) uv run pytest

test-frontend:
	cd frontend && npm test

e2e:           ## Browser journey + authorization tests against the running stack
	cd frontend && npm run e2e

e2e-realtime:  ## Browser cross-process and Redis-outage test (stops/starts Redis)
	cd frontend && npm run e2e:realtime

test-stack:    ## Two-process delivery and Redis-outage tests against the running stack
	cd backend && uv run pytest -m stack tests/stack -v

perf:          ## Allocation, HTTP/DB and browser-visible benchmarks (writes perf/results)
	uv run --project backend python perf/bench.py alloc
	uv run --project backend python perf/bench.py http
	cd frontend && npm run perf:browser
