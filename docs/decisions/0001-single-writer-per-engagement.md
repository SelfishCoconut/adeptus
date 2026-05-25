# 0001. Single-writer process per engagement for graph mutations

Date: 2026-05-24
Status: Accepted

## Context

The engagement graph is shared across all members of an engagement and is written by multiple sources concurrently: human users via the UI, the AI when ingesting tool results, and background tool-result parsers. Naively concurrent writes against the in-memory NetworkX graph or the Postgres mirror create race conditions, lost updates, and inconsistent reads. We also need a clean separation between *write ordering* races (a solved CS problem) and *semantic* conflicts (e.g. "this service is Apache" vs "this service is nginx"), where human or AI mediation is appropriate.

## Decision

Each active engagement has exactly one writer process that owns the in-memory NetworkX graph for that engagement. All graph mutations — from users, the AI, and tool-result ingestion — are serialized through an internal queue owned by that writer. Reads can be served either from Postgres or from the in-memory graph. Semantic conflicts are resolved by AI proposal + human confirmation as a separate, higher-level concern.

## Consequences

**Positive**
- Write races are impossible by construction
- Reasoning about graph state becomes trivial — a single linearizable history
- Replay, audit, and undo are natural to implement
- Backpressure is explicit (queue depth)

**Negative**
- Writer process is a single point of failure per engagement — needs supervision
- Cross-engagement operations require coordination across multiple writers
- Memory usage scales linearly with active engagements

**Neutral**
- Read-after-write consistency requires either querying the writer directly or accepting eventual consistency via Postgres

## Alternatives considered

- **Optimistic concurrency via row versioning**: would push race resolution into every endpoint, complicating the AI ingestion path and making undo harder.
- **Distributed locks (Redis, advisory locks)**: adds infrastructure dependencies and doesn't simplify the semantic-merge UI.
- **Last-writer-wins with conflict-free replicated data types**: viable for some node properties but expensive to reason about for the audit log invariants.
