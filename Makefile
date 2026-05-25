.PHONY: dev test test-backend test-frontend test-integration lint format migrate sandbox sandbox-down clean help

help:
	@echo "Adeptus — make targets"
	@echo "  dev               — start full stack (compose, hot-reload)"
	@echo "  test              — backend + frontend tests + lint + typecheck"
	@echo "  test-backend      — pytest only"
	@echo "  test-frontend     — vitest + RTL + playwright"
	@echo "  test-integration  — slow tests against real services (postgres, ollama, sandbox)"
	@echo "  lint              — ruff + mypy + eslint + tsc --noEmit"
	@echo "  format            — ruff format + prettier"
	@echo "  migrate           — alembic upgrade head"
	@echo "  sandbox           — bring up Juice Shop on http://localhost:3000"
	@echo "  sandbox-down      — tear down Juice Shop and its volumes"
	@echo "  clean             — remove caches, .venv, node_modules, dist"

dev:
	docker compose -f docker-compose.yml -f docker-compose.dev.yml up

test: lint test-backend test-frontend

test-backend:
	cd backend && uv run pytest --cov=app/features --cov-fail-under=80

test-frontend:
	cd frontend && pnpm test --run --coverage
	cd frontend && pnpm playwright test

test-integration:
	cd backend && uv run pytest -m integration

lint:
	cd backend && uv run ruff check . && uv run ruff format --check .
	cd backend && uv run mypy app/
	cd frontend && pnpm lint
	cd frontend && pnpm tsc --noEmit

format:
	cd backend && uv run ruff format .
	cd backend && uv run ruff check --fix .
	cd frontend && pnpm prettier --write .

migrate:
	cd backend && uv run alembic upgrade head

sandbox:
	docker compose -f sandbox/docker-compose.juice-shop.yml up -d
	@echo "Waiting for Juice Shop..."
	@for i in $$(seq 1 30); do \
		curl -sf http://localhost:3000 >/dev/null && echo "Juice Shop ready at http://localhost:3000" && exit 0; \
		sleep 1; \
	done; \
	echo "Timed out waiting for Juice Shop" && exit 1

sandbox-down:
	docker compose -f sandbox/docker-compose.juice-shop.yml down -v

clean:
	rm -rf backend/.venv backend/.pytest_cache backend/.ruff_cache backend/.mypy_cache backend/htmlcov
	rm -rf frontend/node_modules frontend/dist frontend/coverage frontend/playwright-report
	find . -type d -name __pycache__ -exec rm -rf {} +
