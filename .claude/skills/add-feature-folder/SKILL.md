---
name: add-feature-folder
description: |
  Scaffolds a new vertical-slice feature folder for Adeptus on both
  backend and frontend with the canonical file layout. Use when a slice
  needs a new feature that doesn't yet exist — e.g. "create the auth
  feature folder", "scaffold the engagements feature", or implicitly
  the first time the implementer needs to write into a feature that
  has no folder yet.
allowed-tools: Read, Write, Bash
---

# Add a feature folder

## Backend layout to generate

For feature `<name>`, create `backend/app/features/<name>/`:

- `__init__.py` (empty)
- `router.py` — FastAPI APIRouter; HTTP-level error translation only
- `schemas.py` — Pydantic v2 request/response models
- `models.py` — SQLAlchemy 2.x ORM (only if this feature owns tables)
- `service.py` — business logic; raises domain exceptions
- `repository.py` — DB access; one repository per aggregate
- `deps.py` — feature-local FastAPI Depends (auth dep, etc.)
- `tests/__init__.py` (empty)
- `tests/conftest.py` — feature-local fixtures + mocks
- `tests/test_router.py`
- `tests/test_service.py`
- `tests/test_repository.py` (only if `models.py` exists)

Each file gets a one-line module docstring describing its responsibility. Add the new router to `backend/app/main.py` (under `# feature routers` marker).

## Frontend layout to generate

For feature `<name>`, create `frontend/src/features/<name>/`:

- `api.ts` — TanStack Query hooks + generated types re-export
- `components/index.ts` — barrel
- `hooks/index.ts` — feature-local hooks
- `stores/index.ts` — Zustand stores (only if needed)
- `types.ts` — feature-local types (NOT API types — those come from `shared/api/`)
- `__tests__/` directory (empty, ready for `*.test.tsx`)

## Steps

1. Confirm the feature name is valid: lowercase, kebab-case, doesn't collide with existing.
2. Confirm we're on a slice branch (don't scaffold features on main).
3. Create the backend folder + files. Each file is minimal (docstring + imports + one stub that raises NotImplementedError if applicable).
4. Create the frontend folder + files. Same minimalism.
5. Register the backend router in `app/main.py` under the marker.
6. Stage the changes: `git add backend/app/features/<name> frontend/src/features/<name>`.
7. Commit, citing the scaffolding task's id with a `(task N)` token: `chore(slice-NN): scaffold <name> feature folders (task N)`. (If scaffolding isn't its own numbered task in the spec, attach it to the first task it enables.)
8. Report to the main loop what was created.

## Hard rules
- Don't write business logic in the scaffold. Every method body is `raise NotImplementedError(...)` or empty pass.
- Don't create files outside the standard list. If the implementer needs more, they add them as part of the task.
- Don't add tests with real assertions — just placeholder shape (`def test_placeholder(): ...`) so pytest finds the file.
- Never overwrite an existing feature folder. If it exists, STOP and report.
- Never create the folder in `core/` or `shared/`.
