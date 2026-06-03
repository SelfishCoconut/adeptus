# 0009. Engagement config changes reach the concurrency manager via an event seam

Date: 2026-06-03
Status: Accepted

## Context

Slice 05 adds a per-engagement concurrency slot limit. The limit is **owned by
the engagements feature**: it is a column on the engagement, set through the
engagement PATCH endpoint, validated by the engagement schemas. It is
**consumed by the mcp feature**: the in-process admission manager
(`mcp/concurrency.py`) reads the limit to decide how many heavy tool runs may
run at once.

Most of the time the consumer pulls the value — `mcp/service.py` reads the
engagement's limit and passes it to `concurrency.acquire()` on every admission.
But one case needs a push: when an admin *raises* the limit while runs are
queued, the queue must be re-scanned and eligible waiters admitted
**immediately**, not on the next unrelated acquire/release. `acquire()` cannot
do this because no acquire is happening — the runs are already waiting.

The first implementation wired this push the obvious way: `engagements/service.py`
imported `mcp.concurrency` and called `set_slot_limit()` directly. That makes the
engagements feature depend on the mcp feature — a cross-feature import in the
wrong direction (engagements is the lower-level config owner; mcp is the
consumer). Code review flagged it as cross-feature coupling without an ADR.

## Decision

Invert the dependency with a small in-process **event seam** owned by the
engagements feature.

- `engagements/events.py` holds a synchronous observer registry:
  `on_slot_limit_changed(listener)` to subscribe and
  `emit_slot_limit_changed(engagement_id, n)` to publish. Registration is
  idempotent by listener identity.
- `engagements/service.update_engagement` **emits** the event when the slot
  limit changes. It no longer imports anything from `mcp`.
- The mcp feature is the **subscriber**: at the composition root (`main.create_app`)
  we register `concurrency.set_slot_limit` as a listener. The composition root is
  the one place allowed to know about both features, so neither feature imports
  the other for this purpose.

The runtime dependency now flows mcp → engagements (the consumer reacts to the
owner's config), matching the pull direction that already existed.

This seam lives **inside the engagements feature**, so it introduces no new code
in `core/` or `shared/` and needs no widening of those trees.

## Consequences

**Positive**
- The engagements feature stays ignorant of mcp; the dependency direction is
  consistent (owner ← consumer) for both the pull and push paths.
- The seam is the natural extension point for future reactions to engagement
  config changes (e.g. privacy-mode toggles invalidating caches) without
  re-coupling features.
- Listeners are pure functions registered at startup — trivially testable, and
  unit tests can register the same listener to exercise the end-to-end path.

**Negative**
- One indirection: reading the code, the link between an engagement PATCH and
  the concurrency re-scan is the startup registration in `main.create_app`, not a
  direct call. The registration comment and this ADR document that link.
- Listeners run synchronously inside the request that emits; a slow or throwing
  listener would affect the PATCH. Acceptable here — `set_slot_limit` is a fast
  in-memory scan — but the seam is not a general async event bus and should not
  be used for slow work.

**Neutral**
- If Adeptus ever splits the backend into multiple processes, this in-process
  seam would become a real pub/sub message; the emit/subscribe shape is chosen
  to make that migration mechanical.

## Alternatives considered

- **Route the call through `mcp/service.py`** instead of `concurrency` directly:
  cosmetically tidier, but engagements still imports mcp — the coupling
  direction is unchanged. Rejected.
- **Drop `set_slot_limit` and rely only on the per-`acquire()` limit**: removes
  the coupling entirely, but a limit increase would not wake already-queued runs
  until an unrelated acquire/release happened. Fails the slice's "raise admits
  queued waiters immediately" behavior. Rejected.
- **Write an ADR accepting the engagements → mcp import**: documents the smell
  rather than fixing it, and entrenches a backwards dependency direction that
  future features would copy. Rejected.
