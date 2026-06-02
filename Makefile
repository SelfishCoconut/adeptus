.PHONY: dev test test-backend test-frontend test-integration lint format migrate generate-api sandbox sandbox-down clean help

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
	@echo "  generate-api      — regenerate the typed OpenAPI client from the backend"
	@echo "  sandbox           — bring up Juice Shop on http://localhost:3000"
	@echo "  sandbox-down      — tear down Juice Shop and its volumes"
	@echo "  clean             — remove caches, .venv, node_modules, dist"

dev:
	docker compose -f docker-compose.yml -f docker-compose.dev.yml up

test: lint test-backend test-mcp-servers test-frontend

test-backend:
	cd backend && uv run pytest --cov=app/features --cov-fail-under=80

test-mcp-servers:
	cd mcp-servers && uv run --extra dev pytest --cov=. --cov-fail-under=80

test-frontend:
	cd frontend && pnpm test --run --coverage
	cd frontend && pnpm playwright test

test-integration:
	cd backend && uv run pytest -m integration

lint:
	cd backend && uv run ruff check . && uv run ruff format --check .
	cd backend && uv run mypy app/
	cd mcp-servers && uv run --extra dev ruff check . && uv run --extra dev ruff format --check .
	# Each MCP server is an independent script tree with its own top-level
	# `server.py` and `tests/` package; mypy must check them one root at a time
	# or the colliding module names clash ("Duplicate module named ...").
	cd mcp-servers && for d in */server.py; do uv run --extra dev mypy "$$(dirname "$$d")" || exit 1; done
	cd frontend && pnpm lint
	cd frontend && pnpm tsc --noEmit

format:
	cd backend && uv run ruff format .
	cd mcp-servers && uv run --extra dev ruff format .
	cd backend && uv run ruff check --fix .
	cd frontend && pnpm prettier --write .

migrate:
	docker compose run --rm backend uv run alembic upgrade head

generate-api:
	cd backend && uv run python -c "import json; from app.main import app; print(json.dumps(app.openapi(), indent=2))" > ../frontend/openapi.json
	cd frontend && pnpm generate-api

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
