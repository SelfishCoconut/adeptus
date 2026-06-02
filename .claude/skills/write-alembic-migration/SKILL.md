---
name: write-alembic-migration
description: |
  Generates an Alembic migration for Adeptus. Runs alembic autogenerate
  against the current SQLAlchemy models, reviews the output for the
  common autogenerate gotchas (missing server_default, lost indexes,
  wrong enum handling, broken downgrade), and verifies upgrade+downgrade
  cycle works. Use whenever models.py is touched, when the user says
  "create a migration", "alembic", or after add-feature-folder generates
  new models.
allowed-tools: Read, Write, Edit, Bash
---

# Write an Alembic migration

## Steps

1. Confirm the database is up: `docker compose ps postgres` shows running. If not: `make dev` first.

2. Ensure migrations are at head: `cd backend && uv run alembic upgrade head`.

3. Generate the autogenerate candidate:
   ```
   cd backend && uv run alembic revision --autogenerate -m "slice-NN: <description>"
   ```

4. Open the generated file in `backend/alembic/versions/`. Review for these common autogenerate failures:

   ### Things autogenerate gets WRONG
   - **Renames** are detected as drop+add. If a column or table was renamed, manually replace with `op.alter_column(..., new_column_name=...)` or `op.rename_table(...)`.
   - **Server defaults** are often dropped silently. Re-add `server_default=...` if the model has one.
   - **Indexes** on existing columns sometimes aren't detected. Cross-check with `models.py`.
   - **Enum types** (PostgreSQL ENUM) need explicit `create_type=True/False` handling and explicit drop in downgrade.
   - **Constraints** with custom names — verify names match between up and down.
   - **pgvector columns** (`Vector(384)`) — autogenerate may emit a generic type; ensure it's the pgvector `Vector` type and `hnsw` index is created with proper opclass.

5. Write a complete `downgrade()` function. Many autogenerate downgrades are stubs; make them actually reverse the upgrade. If a downgrade is genuinely lossy (data loss), comment why and ensure tests don't rely on downgrade through it.

6. Verify the cycle works:
   ```
   uv run alembic upgrade head
   uv run alembic downgrade -1
   uv run alembic upgrade head
   ```
   If any step fails, fix and retry.

7. Add a test in `backend/tests/migrations/test_<migration_id>.py` that:
   - Spins up a fresh DB at the previous revision
   - Inserts representative data
   - Upgrades to this revision
   - Asserts the data survived and any new constraints hold
   - Downgrades and verifies cleanup (where lossless)

8. Stage and commit:
   ```
   git add backend/alembic/versions/<file> backend/tests/migrations/<file>
   git commit -m "feat(slice-NN): migration — <description> (task N)"
   ```

9. Report to the main loop:
   - Migration file path
   - What autogenerate got wrong (verbatim, so the user learns the pattern)
   - Upgrade-downgrade-upgrade cycle result
   - Test added

## Hard rules
- Never trust autogenerate output without reading it line by line.
- Never commit a migration where downgrade is `pass` or empty unless it's an explicit data-loss boundary, commented as such.
- Never edit a migration after it's been merged to main. If it's wrong, write a new migration.
- Never rely on `op.execute()` for things Alembic supports natively — but use it for pgvector index creation since `op.create_index` doesn't have a clean hnsw API: `op.execute("CREATE INDEX ... USING hnsw (embedding vector_cosine_ops)")`.
