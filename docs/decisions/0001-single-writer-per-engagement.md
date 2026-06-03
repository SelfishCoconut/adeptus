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

## Implementation notes

- **Realized in Slice 07** (`backend/app/features/graph/writer.py`): a lazy per-engagement registry (`_writers`) with one consumer `asyncio.Task` per engagement draining a per-engagement command queue. The registry entry is created in a synchronous critical section (no `await` between the membership check and assignment) so concurrent first-writes cannot spawn two consumers.
- **The writer is the validation chokepoint for *all* write sources, not just the user path.** Slice 07 wires only the user write path (router → service → `writer.submit_*`), and the service performs the engagement-isolation and endpoint-ownership checks. But because the AI-ingestion and tool-result paths will plug into this *same* queue in later slices, invariants that must hold for every write source — engagement-boundary isolation and edge-endpoint ownership (both endpoints live and in this engagement) — are enforced **inside the writer consumer** (`_handle_create_edge`), not only in the service. Any future ingestion caller therefore inherits these guards for free; a new caller that bypassed the service could not bypass the writer.

## Alternatives considered

- **Optimistic concurrency via row versioning**: would push race resolution into every endpoint, complicating the AI ingestion path and making undo harder.
- **Distributed locks (Redis, advisory locks)**: adds infrastructure dependencies and doesn't simplify the semantic-merge UI.
- **Last-writer-wins with conflict-free replicated data types**: viable for some node properties but expensive to reason about for the audit log invariants.
