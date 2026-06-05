# 0010. Append-only hash-chain audit log with single-appender invariant

Date: 2026-06-05
Status: Accepted

## Context

§14 of the requirements mandates a tamper-evident audit log that records every tool run, AI call, graph edit, login, and approval/rejection with user attribution — and that entries are hash-chained. Per-row hashes alone detect field tampering but not row deletion or reordering; a chain (`entry_hash = SHA-256(prev_hash_bytes || canonical(fields))`) means removing, reordering, or inserting any row breaks every subsequent link. The audit log is the sole source of truth for provenance and attribution (§17.4), so no provenance columns are added to entity tables (§8.2).

Two structural constraints shaped the design. First, `seq` and `prev_hash` must be assigned under a strict total order — a concurrent append reading a stale head would fork the chain, silently defeating tamper-evidence. Second, `actor_user_id` and `engagement_id` must be both tamper-evident (hashed) and durable across user/engagement deletion; a foreign key's `SET NULL` action would mutate a hashed column and make a legitimate delete look like tampering, while `RESTRICT` would block deletion. The constraint that a column cannot be simultaneously hashed-immutable and FK-mutable ruled out both FK variants.

All code lives under `app/features/audit/`; no widening of `core/` or `shared/` occurs.

## Decision

**Single-appender invariant.** `repository.append_entry` serializes every append under a `SELECT ... FOR UPDATE` on a dedicated single-row `audit_chain_head` table (columns: `last_seq`, `head_hash`). The lock is held across hash computation, row insertion, and head-row update — all inside the same database transaction. This gives a strict total order on appends without a separate process, mirroring the philosophy of ADR-0001 (single-writer per engagement) applied to the audit chain. `seq` and `entry_hash` carry UNIQUE constraints as a database-level backstop; a residual fork hard-fails at the DB rather than silently corrupting the chain.

**No foreign key on `actor_user_id` or `engagement_id`.** Both columns are plain nullable UUID columns with no `REFERENCES` clause. They are immutable, denormalized values included in the hash. Deleting a user or engagement never touches an audit row — the row retains a now-dangling id, which is the correct behavior for a forensic record (§17.4). A SQL rewrite of `actor_user_id` is still caught by the verifier because the value is hashed.

**One shared pure hash function.** `hashing.compute_entry_hash(prev_hash, content)` is the single implementation called by both `repository.append_entry` (writer) and `verify.py` (verifier). Canonicalization is fully deterministic: fixed-precision UTC timestamps (`strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"`), sorted-key compact JSON payload, JSON `null` as the NULL sentinel, and `prev_hash` decoded from hex to raw bytes before concatenation. No parallel serialization path exists.

**Same-transaction (atomic) recording.** `audit.service.record()` writes the audit row in the same database transaction as the originating action. They commit or roll back together — no silent audit gaps, and an audit-write failure fails the action. For graph mutations (which commit inside the single-writer process), the audit row commits in the request session alongside the Slice 09 undo-push, the closest atomic analogue available without threading a session into the writer.

**Verifier hard-fails on a missing head row.** A missing `audit_chain_head` row is classified as a `head-missing` failure (non-zero exit) rather than a skipped check, so tail truncation by a database-admin adversary who also removes the head row is surfaced rather than silently passing.

## Consequences

- Positive: the chain cannot fork under concurrency by construction; `seq`/`entry_hash` uniqueness is a database backstop. Field tampering, row deletion, and reordering are all detectable by the verifier.
- Positive: writer and verifier share one hash function; hash drift (a class of silent false-negative bugs) is eliminated by design and enforced by a round-trip test.
- Positive: users and engagements can be deleted without touching or breaking audit rows; attribution remains tamper-evident via the hash.
- Positive: no provenance columns on entity tables — attribution lives only in `audit_entries`, satisfying §8.2 and §17.4.
- Negative: the `audit_chain_head` lock serializes all appends instance-wide. For a 2–5 person team the contention is negligible; a high-throughput deployment would need a partitioned chain or a dedicated append service.
- Negative: an audit-write failure fails the originating action. This is the deliberate trade-off of the same-transaction policy: no silent gaps at the cost of coupling audit durability to action durability.
- Neutral but worth knowing: a pure in-DB chain cannot detect a database-admin adversary who truncates the tail *and* rewrites or removes the `audit_chain_head` row. This is explicitly out of scope per §14 ("no external timestamping"). A future slice that needs anti-truncation guarantees must add an external append-only anchor (head-hash notarization or WORM export) under a separate ADR.
- Neutral but worth knowing: `login_failed` events are audited with `actor_user_id = NULL` (attribution is impossible for an unauthenticated attempt). Because `POST /api/v1/auth/login` has no rate limit, this is an unauthenticated, unbounded write path against the head lock — tracked as a pre-existing auth gap, independent of this slice.
- Neutral but worth knowing: async/background `tool_run_completed` emission is deferred. `ToolRun` carries no user-attribution column and the background `_stream_to_channel` has ~10 terminal sites; threading `user_id` through is a focused follow-up, not part of this slice.

## Alternatives considered

- **Per-row hashes without chaining**: detects field tampering but not row deletion or reordering; rejected because §14 requires detecting those cases.
- **`ON DELETE RESTRICT` FK on `actor_user_id` / `engagement_id`**: prevents user/engagement deletion while audit rows reference them; rejected because the forensic log must outlive the entities it records.
- **`ON DELETE SET NULL` FK**: nulls a hashed column on delete, making a legitimate delete indistinguishable from tampering by the verifier; rejected because it contradicts the tamper-evidence requirement.
- **`SELECT MAX(seq)` at insert time**: subject to a read-then-write race between concurrent appenders; rejected because two concurrent reads of the same max would assign duplicate `seq`/`prev_hash` and fork the chain.
- **Separate appender process** (analogous to the single-writer of ADR-0001): would eliminate the lock but adds operational complexity (supervision, startup ordering, cross-process session hand-off) with no benefit at current scale; rejected in favor of the `FOR UPDATE` approach, which gives the same total-order guarantee as a DB transaction.
