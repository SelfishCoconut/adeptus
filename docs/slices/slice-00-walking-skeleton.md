# Slice 00: Walking Skeleton

**Branch**: `slice-00-walking-skeleton`
**GitHub Issue**: #1
**Status**: planned
**Risky**: yes

---

## Goal

Stand up the full vertical stack — Docker Compose, FastAPI backend with login/logout/session, and a React SPA 3-pane workspace shell — so that a user can authenticate and reach an empty workspace that proves the frontend-backend-database round-trip is live.

## User-visible demo

- Navigate to `https://localhost` (self-signed cert, accept the browser warning).
- See a login form. Enter the seeded admin credentials (`ADEPTUS_ADMIN_USER` / `ADEPTUS_ADMIN_PASSWORD_HASH` from the compose `.env.example`).
- First login: see a one-time terms-of-use acceptance screen; click "Accept".
- Land on the workspace shell: three empty panes (left AI chat placeholder, right graph placeholder, bottom console placeholder) with a top bar showing the logged-in username and a "Logout" button.
- The top bar displays a backend healthcheck indicator (green dot = backend reachable, round-trip confirmed).
- Click "Logout": session destroyed, redirected back to login.
- Attempting to navigate to `/workspace` without a session redirects to `/login`.

## Out of scope

- Does NOT implement engagement creation or listing (Slice 01).
- Does NOT implement the real AI chat panel (Slice 11+).
- Does NOT implement the graph visualisation (Slice 08+).
- Does NOT implement the tool output console (Slice 04+).
- Does NOT implement privacy mode banner (Slice 02).
- Does NOT implement admin user-management UI (future slice).
- Does NOT implement TLS configuration swap procedure (Slice 39).
- Does NOT implement single-user dev mode without auth (Slice 40).
- Does NOT implement session listing or revocation UI.
- Does NOT implement the legal-gate for non-admin users (users created in later slices).

## Requirements traceability

- §2 — Deployment as a single Docker Compose stack; LAN multi-user mode.
- §3 — Username + password auth; long-lived sessions (14-day sliding, per ADR-0003); two roles (Admin/User); no self-signup; legal gate on first login; TLS with self-signed cert (skeleton only — cert bundled, swap procedure is Slice 39).
- §3 (ADR-0002) — Admin bootstrapped via `ADEPTUS_ADMIN_USER` / `ADEPTUS_ADMIN_PASSWORD_HASH` env vars on first boot; no plain-text passwords in compose.
- §3 (ADR-0003) — Server-side sessions in `sessions` table; opaque cookie (`HttpOnly; Secure; SameSite=Lax`); instant revocation via row delete.
- §11.1 — Vite + React + TypeScript SPA; react-router; TanStack Query; Zustand; Tailwind + shadcn/ui; dark/light mode.
- §11.2 — Three-pane workspace layout (left chat, right graph, bottom console); empty placeholders sufficient for this slice.

## Contract

```yaml
openapi: "3.1.0"
info:
  title: Adeptus API
  version: "0.1.0"

paths:
  /api/v1/health:
    get:
      operationId: health_check
      summary: Backend liveness probe
      security: []
      responses:
        "200":
          description: Healthy
          content:
            application/json:
              schema:
                type: object
                required: [status, version]
                properties:
                  status:
                    type: string
                    enum: [ok]
                  version:
                    type: string

  /api/v1/auth/login:
    post:
      operationId: login
      summary: Authenticate with username + password
      security: []
      requestBody:
        required: true
        content:
          application/json:
            schema:
              $ref: "#/components/schemas/LoginRequest"
      responses:
        "200":
          description: Authenticated; session cookie set
          headers:
            Set-Cookie:
              schema:
                type: string
                description: "session_id=<opaque>; HttpOnly; Secure; SameSite=Lax; Path=/"
          content:
            application/json:
              schema:
                $ref: "#/components/schemas/UserMe"
        "401":
          description: Invalid credentials

  /api/v1/auth/logout:
    post:
      operationId: logout
      summary: Destroy session
      responses:
        "204":
          description: Session deleted; cookie cleared
        "401":
          description: Not authenticated

  /api/v1/auth/me:
    get:
      operationId: get_me
      summary: Return the currently authenticated user
      responses:
        "200":
          content:
            application/json:
              schema:
                $ref: "#/components/schemas/UserMe"
        "401":
          description: Not authenticated

  /api/v1/auth/accept-terms:
    post:
      operationId: accept_terms
      summary: Record that the user has accepted the terms of use
      responses:
        "200":
          content:
            application/json:
              schema:
                $ref: "#/components/schemas/UserMe"
        "401":
          description: Not authenticated

components:
  schemas:
    LoginRequest:
      type: object
      required: [username, password]
      properties:
        username:
          type: string
        password:
          type: string
          format: password

    UserMe:
      type: object
      required: [id, username, role, terms_accepted_at]
      properties:
        id:
          type: string
          format: uuid
        username:
          type: string
        role:
          type: string
          enum: [admin, user]
        terms_accepted_at:
          type: string
          format: date-time
          nullable: true
```

## Data model changes

New tables introduced by this slice's Alembic migration:

- `users` table:
  - `id` UUID primary key (default `gen_random_uuid()`)
  - `username` VARCHAR(64) UNIQUE NOT NULL
  - `password_hash` TEXT NOT NULL (argon2 hash)
  - `role` VARCHAR(16) NOT NULL DEFAULT `'user'` (check: `admin` | `user`)
  - `terms_accepted_at` TIMESTAMPTZ NULL
  - `created_at` TIMESTAMPTZ NOT NULL DEFAULT NOW()
  - `updated_at` TIMESTAMPTZ NOT NULL DEFAULT NOW()
  - Index: `ix_users_username` UNIQUE

- `sessions` table (per ADR-0003):
  - `id` VARCHAR(64) primary key (256-bit random opaque token, hex-encoded)
  - `user_id` UUID NOT NULL REFERENCES `users(id)` ON DELETE CASCADE
  - `created_at` TIMESTAMPTZ NOT NULL DEFAULT NOW()
  - `last_used_at` TIMESTAMPTZ NOT NULL DEFAULT NOW()
  - `expires_at` TIMESTAMPTZ NOT NULL
  - `user_agent` TEXT NULL
  - `ip` INET NULL
  - Index: `ix_sessions_user_id` on `user_id`
  - Index: `ix_sessions_expires_at` on `expires_at` (supports cleanup job)

No existing tables modified. No pgvector extension needed for this slice.

## Backend tasks

Ordered; each independently testable.

1. **[S]** Create `backend/pyproject.toml` with `[project]` metadata and `[tool.ruff]`, `[tool.mypy]`, `[tool.pytest.ini_options]` sections. Confirm `make lint` and `make test-backend` resolve (even if test suite is empty at this point).

2. **[S]** Add `backend/app/__init__.py` and `backend/app/core/__init__.py`. Add `backend/app/core/config.py` — a `Settings` Pydantic-settings model reading from environment: `DATABASE_URL`, `ADEPTUS_ADMIN_USER`, `ADEPTUS_ADMIN_PASSWORD_HASH`, `SESSION_COOKIE_NAME` (default `session_id`), `SESSION_TTL_DAYS` (default 14), `ENVIRONMENT` (default `production`).

3. **[S]** Add `backend/app/core/db.py` — async SQLAlchemy engine + `AsyncSession` factory; `get_db` FastAPI dependency.

4. **[S]** Add `backend/app/core/errors/__init__.py` and `backend/app/core/errors/handlers.py` — register `RequestValidationError` → 422, domain `NotFoundError` → 404, `AuthenticationError` → 401, `ForbiddenError` → 403 on the FastAPI app.

5. **[M]** Add `backend/app/features/auth/models.py` — SQLAlchemy `User` and `Session` ORM models (columns as per Data model changes above).

6. **[S]** Add `backend/app/features/auth/schemas.py` — Pydantic v2 schemas: `LoginRequest`, `UserMe` (matches OpenAPI contract above).

7. **[M]** Add `backend/app/features/auth/repository.py` — async methods: `get_user_by_username`, `get_user_by_id`, `create_user`, `update_terms_accepted`, `create_session`, `get_session`, `refresh_session`, `delete_session`, `delete_expired_sessions`. Tests in `tests/test_repository.py` (use pytest fixtures with an in-memory SQLite engine or test Postgres container; mark `integration` if real Postgres needed).

8. **[M]** Add `backend/app/features/auth/service.py` — `authenticate_user` (verify argon2 hash, return `User`), `bootstrap_admin` (idempotent: create admin if no users exist, reading from `Settings`), `accept_terms`. Raises domain exceptions (`AuthenticationError`, `ValidationError`). Tests in `tests/test_service.py` — mock the repository; cover wrong-password, already-bootstrapped, terms-idempotency.

9. **[M]** Add `backend/app/features/auth/router.py` — POST `/api/v1/auth/login`, POST `/api/v1/auth/logout`, GET `/api/v1/auth/me`, POST `/api/v1/auth/accept-terms`. Session cookie set/cleared here. Dependency `get_current_user` reads cookie → `repository.get_session` → returns `User` or raises `AuthenticationError`. Tests in `tests/test_router.py` using `httpx.AsyncClient` + `AsyncSession` override.

10. **[S]** Add `backend/app/features/health/router.py` — GET `/api/v1/health` → `{"status": "ok", "version": "<version from pyproject.toml>"}`. No auth required. Unit test: one test verifying 200 + shape.

11. **[S]** Add `backend/app/main.py` — create `FastAPI` app; include health and auth routers; register error handlers; add `lifespan` that calls `bootstrap_admin` on startup.

12. **[S]** Write Alembic migration for `users` and `sessions` tables via `write-alembic-migration` skill. Confirm `make migrate` runs cleanly against the dev Postgres container.

13. **[S]** Add `backend/alembic/env.py` wired to `app.core.db` (async). Confirm autogenerate works.

14. **[S]** Step gate: security review of login/session cookie implementation before proceeding to frontend tasks or compose wiring.

## Frontend tasks

Ordered.

1. **[M]** Bootstrap the `frontend/` directory: `pnpm create vite@latest frontend -- --template react-ts`; add and configure `tailwindcss`, `shadcn/ui`, `react-router-dom`, `@tanstack/react-query`, `zustand`; configure `vitest` + React Testing Library + Playwright; set TypeScript strict mode; confirm `make test-frontend` resolves (zero tests = pass for now).

2. **[S]** Configure Vite proxy: `vite.config.ts` proxies `/api` to `http://localhost:8000` in dev. Add `.env.example` with `VITE_API_BASE_URL`.

3. **[S]** Generate OpenAPI client into `frontend/src/shared/api/` using `openapi-typescript` (or `@hey-api/openapi-ts`). Add `pnpm generate-api` script to `package.json`. Document regeneration in CLAUDE.md workflow note.

4. **[M]** Add `frontend/src/features/auth/` with:
   - `api.ts` — TanStack Query hooks: `useLogin`, `useLogout`, `useMe` (the `me` query is the session anchor for the whole app).
   - `store.ts` — Zustand store: `{ user: UserMe | null }`.
   - `components/LoginForm.tsx` — controlled form, calls `useLogin`, shows validation errors, navigates to `/workspace` on success. Test: renders, shows error on bad credentials (mocked query), navigates on success.
   - `components/TermsGate.tsx` — shown when `me.terms_accepted_at` is null; Accept button calls `useAcceptTerms`. Test: renders gate, calls accept, disappears on success.

5. **[M]** Add `frontend/src/features/workspace/` with:
   - `WorkspaceShell.tsx` — three-pane layout (CSS Grid or Flexbox + Tailwind): left placeholder, right placeholder, bottom placeholder. Top bar with username, role badge, Logout button, and a health indicator dot. Test: renders three panes and top bar.
   - `HealthIndicator.tsx` — polls `GET /api/v1/health` every 30 s via TanStack Query; green dot when healthy, red when failing. Test: shows green on success response, red on error.

6. **[S]** Add `frontend/src/app/` routing: `react-router-dom` with routes: `/login` → `LoginPage`, `/workspace` → `ProtectedRoute(WorkspaceShell)`, `/` → redirect to `/workspace`. `ProtectedRoute` checks `useMe` — if 401, redirects to `/login`. `TermsGate` wraps `WorkspaceShell` and intercepts if `terms_accepted_at` is null.

7. **[S]** Dark/light mode: add `ThemeProvider` using `shadcn/ui` convention (class on `<html>`); detect system preference; toggle button in top bar. Test: toggle switches class.

8. **[S]** Step gate: security review of session cookie handling and `ProtectedRoute` redirect logic before finishing frontend.

## Test plan

- **Unit — backend**: each layer in isolation (repository mocked in service tests; service mocked in router tests). Concrete test names:
  - `test_authenticate_user_success`
  - `test_authenticate_user_wrong_password`
  - `test_authenticate_user_unknown_username`
  - `test_bootstrap_admin_creates_once`
  - `test_bootstrap_admin_noop_when_users_exist`
  - `test_accept_terms_sets_timestamp`
  - `test_login_sets_cookie`
  - `test_logout_clears_cookie_and_deletes_session`
  - `test_me_returns_user`
  - `test_me_401_without_cookie`
  - `test_me_401_expired_session`
  - `test_health_returns_ok`

- **Unit — frontend** (Vitest + RTL):
  - `LoginForm.test.tsx`: renders form, submit calls mutation, shows error, redirects on success (mock `useLogin`)
  - `TermsGate.test.tsx`: shows gate when `terms_accepted_at` null, hides on accept
  - `WorkspaceShell.test.tsx`: renders three panes, top bar, logout button present
  - `HealthIndicator.test.tsx`: green on ok response, red on fetch error

- **Integration** (marked `@pytest.mark.integration`, requires real Postgres):
  - `test_login_logout_full_cycle`: POST login → GET me → POST logout → GET me → 401
  - `test_bootstrap_admin_idempotent`: run bootstrap twice, only one admin created

- **E2E** (Playwright, requires `make dev`):
  - `auth.spec.ts` — happy path: visit `/`, get redirected to `/login`, fill form, accept terms, see workspace, logout, redirected back to `/login`
  - `auth.spec.ts` — unhappy path: wrong password shows error message

## Acceptance criteria

- `make test` passes (lint + typecheck + backend unit + frontend unit).
- `make test-integration` passes (login/logout cycle; bootstrap idempotency).
- `make dev` brings up the full stack; manually:
  1. Open `https://localhost`; accept self-signed cert warning.
  2. Login with admin credentials from `.env.example`.
  3. Accept terms of use.
  4. See the empty 3-pane workspace with green health dot.
  5. Logout → redirected to login.
  6. Navigating directly to `/workspace` redirects to `/login`.
- `make migrate` runs `alembic upgrade head` cleanly against a fresh Postgres container.
- CI (`.github/workflows/ci.yml`) is green on the PR.

## Risks

- **Risk 1: pycache but no source files.** The `backend/` directory has `.venv` and pycache artifacts from an earlier scaffold run but the actual `.py` source files are absent. The first backend task must create all source files from scratch; the implementer must not assume any prior code exists.
- **Risk 2: argon2 hash bootstrap.** If `ADEPTUS_ADMIN_PASSWORD_HASH` contains an invalid hash, `bootstrap_admin` will create an account that cannot be logged into. Mitigation: validate the hash format in `bootstrap_admin` at startup and emit a loud warning log if it looks wrong (does not start with `$argon2`).
- **Risk 3: self-signed TLS in dev.** Browsers require explicit cert-trust interaction. The full TLS configuration procedure is out of scope (Slice 39), but the skeleton must still serve HTTPS so the `HttpOnly; Secure` cookie attribute works. Mitigation: bundle a pre-generated self-signed cert in `docker/certs/`; Nginx (or Caddy) terminates TLS and proxies to FastAPI.
- **Risk 4: OpenAPI client generation bootstrap.** If the backend is not running when the frontend team tries to regenerate the client, the script fails. Mitigation: check in a committed snapshot of the generated client so CI does not require a live backend; add a `Makefile` target `generate-api` that starts only the backend container.
- **Risk 5: SameSite=Lax + cross-origin dev.** Vite dev server on port 5173 and FastAPI on port 8000 are cross-origin. Mitigation: Vite proxy (`/api` → `http://localhost:8000`) keeps the effective origin the same from the browser's perspective.

## Resolved decisions

1. **TLS terminator** — Introduce a minimal Nginx container in `docker-compose.yml` now, bundling a pre-generated self-signed cert in `docker/certs/`. Nginx terminates TLS and proxies to FastAPI. `Secure` stays `True` on the session cookie. Full TLS swap procedure deferred to Slice 39.

2. **Terms-of-use text** — Use: *"By using Adeptus you agree to use it only on systems you have explicit written permission to test."*

3. **Session expiry behaviour** — On expired session mid-use, silently redirect to `/login`. No modal. The frontend `useMe` hook treats a 401 as "not authenticated" and `ProtectedRoute` redirects.

## Security review required?

Yes — this slice touches authentication, session cookies, password hashing, and the admin bootstrap mechanism. A security reviewer must confirm before the PR is merged:

- argon2 parameters (memory, iterations, parallelism) are not weaker than defaults.
- Session cookie attributes (`HttpOnly; Secure; SameSite=Lax`) are set correctly and verified in tests.
- The `bootstrap_admin` path has no TOCTOU race (check-then-create must be in a single transaction with a serializable isolation level or an `INSERT ... WHERE NOT EXISTS` pattern).
- No session ID is logged anywhere in the request lifecycle.
- The `GET /api/v1/health` endpoint leaks no internal state (version string is acceptable; stack trace, DB URL, or env vars are not).

## Progress

(The stop-checkpoint hook and compact-handoff skill append here. Leave empty at planning time.)
- 2026-05-26T20:55:43Z — cdb0e40 chore: post-bootstrap fixes
- 2026-05-26T21:06:34Z — cdb0e40 chore: post-bootstrap fixes
- 2026-05-26T21:11:00Z — 628dc21 chore(slice-00): start slice — spec + PROJECT_PLAN status
- 2026-05-26T21:26:52Z — 36e2df1 chore(backend): scaffold pyproject.toml + ruff/mypy/pytest config
- 2026-05-26T21:29:37Z — 36e2df1 chore(backend): scaffold pyproject.toml + ruff/mypy/pytest config
- 2026-05-26T21:31:19Z — 36e2df1 chore(backend): scaffold pyproject.toml + ruff/mypy/pytest config
- 2026-05-26T21:33:19Z — 2e6f3f4 feat(slice-00): add Settings pydantic-settings config from env
- 2026-05-26T21:35:50Z — a256ced feat(slice-00): add async db engine + session factory
- 2026-05-26T21:43:42Z — e406894 feat(slice-00): add core error handlers + domain exceptions
- 2026-05-26T21:47:37Z — 3c2c463 chore(slice-00): scaffold auth feature folder (backend)
- 2026-05-26T21:49:53Z — 896adb5 feat(slice-00): add User and Session ORM models
- 2026-05-26T21:52:34Z — e6af7fd feat(slice-00): add auth Pydantic schemas (LoginRequest, UserMe)
- 2026-05-26T22:41:33Z — f4f8291 feat(slice-00): add auth async repository
- 2026-05-26T23:25:46Z — f34448b feat(slice-00): add auth service layer (argon2, admin bootstrap, accept-terms)
- 2026-05-28T10:26:34Z — e387d8e feat(slice-00): add Alembic migration for users and sessions
- 2026-05-28T12:12:45Z — e387d8e feat(slice-00): add Alembic migration for users and sessions
- 2026-05-28T12:24:42Z — e387d8e feat(slice-00): add Alembic migration for users and sessions
- 2026-05-28T16:57:37Z — 773360a fix(slice-00): harden auth security step gate
- 2026-05-28T17:02:14Z — 4c8b182 docs(slice-00): append progress checkpoint log
- 2026-05-28T17:14:22Z — 4c8b182 docs(slice-00): append progress checkpoint log
- 2026-05-28T18:43:04Z — 34072ed fix(slice-00): address frontend auth security review (FE8 step gate)
- 2026-05-28T19:32:13Z — b714b3b test(slice-00): enforce 60% frontend coverage gate in vitest config

## Post-review resolution (2026-05-29)

Code review found 0 Critical, 5 Warnings. Security review: OK to merge with two Low
deferrals. All Warnings now resolved:

- **W1 (auth)** — `deps.py` read the cookie via a hardcoded `Cookie(alias="session_id")`
  while `router.py` writes it from `settings.SESSION_COOKIE_NAME`. Fixed: the dependency
  now reads the cookie off the request using the same settings value, so reader and writer
  cannot drift.
- **W2** — Declared `passlib[argon2]` but imported `argon2` directly. Fixed: depend on
  `argon2-cffi` directly; `passlib` dropped.
- **W3 (auth)** — `secure=True` was unconditional, so the cookie is unusable over plain
  HTTP. Resolved **by design** rather than by weakening the flag: the new Nginx edge
  terminates TLS, so the Secure cookie is always delivered over HTTPS. No code change.
- **W4** — Frontend 60% coverage gate (already fixed in b714b3b).
- **W5** — Removed the redundant Zustand auth store; the TanStack Query `me` cache is the
  single source of truth (login seeds it, logout clears all caches).

**Integration completed (Resolved Decision #1):** Added a `web` service to
`docker-compose.yml` — a multi-stage image that builds the SPA and serves it over HTTPS via
Nginx, terminating TLS and proxying `/api` to the backend same-origin. This makes
`https://localhost` reachable, satisfying the acceptance criteria.

> **Deviation from Resolved Decision #1:** the decision said to bundle a *pre-generated*
> self-signed cert in `docker/certs/`. Committing a private key would trip the
> `detect-private-key`/gitleaks pre-commit hooks and is poor hygiene, so the cert is instead
> generated **at image build time** (baked into the image, no key in the repo). Same outcome
> — HTTPS works out of the box by default — without a committed secret. The TLS cert-swap
> procedure remains Slice 39.

**Security Lows deferred (not blocking):** `POSTGRES_PASSWORD` hardcoded in compose (dev
default), and no login rate-limiting (belongs to a later hardening slice).

**Open — manual acceptance:** the live `https://localhost` login → workspace round-trip
must be exercised with the Docker daemon running (`make dev`); it was not run in the
authoring session because no daemon was available there.

### Second review round (2026-05-30)

Re-ran code-reviewer + security-reviewer on the integration + fixes. All blocking
findings resolved:

- **T5 (security, HIGH)** — backend published `0.0.0.0:8000`, exposing the plaintext API
  to the LAN past the TLS edge. Now bound to `127.0.0.1:8000`.
- **W2 (code review)** — `web` depended on `backend` with no healthcheck → 502 race on
  cold start. Added a backend healthcheck; `web` now waits on `condition: service_healthy`.
- **W1 completion** — router tests hardcoded `"session_id"`; now derive the name from
  `get_settings().SESSION_COOKIE_NAME`.
- **A6 (MEDIUM)** — dropped the user UUID from the accept-terms 404 message.
- **T9 / N1 / N2 / headers** — `.env` excluded from build context; cookie attributes
  single-sourced; `openssl` removed from the final image; `nosniff`/`X-Frame-Options`/
  `ssl_ciphers` added. HSTS consciously deferred to Slice 39 (would pin browsers to a
  distrusted self-signed cert).
- **Compose env interpolation bug (found during validation)** — docker compose expands
  `$` in env_file values, which mangled the argon2 admin hash and would break login.
  Fixed by `$$`-escaping the hash in `.env`/`.env.example`; verified via
  `docker compose config` that the container receives the correct single-`$` hash.

Deferred (non-blocking, tracked as follow-ups): explicit HSTS + CSP (Slice 39 TLS
hardening), login rate-limiting, `POSTGRES_PASSWORD` in compose, ProtectedRoute loading
state. Line 55 of `auth/deps.py` (user-row-gone guard) is left uncovered — the FK makes
the state near-unreachable.
