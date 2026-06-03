"""In-process event seam for engagement configuration changes.

The engagements feature owns the slot-limit setting (a column on the engagement,
set via the PATCH endpoint) but must not know that the mcp concurrency manager
exists.  This tiny observer registry inverts that dependency: engagements *emits*
``slot_limit_changed`` and the mcp feature *subscribes* at app startup, so the
runtime dependency flows mcp → engagements (mcp consumes engagement config),
never the reverse.  See docs/decisions for the rationale.

Listeners run synchronously in registration order.  This is deliberately a
per-feature seam, not a general event bus — it lives inside the engagements
feature so it needs no core/ or shared/ widening.

Slice 06 adds ``engagement_paused_changed``.  Its listeners return
``(killed_running, dequeued)`` counts so the service can surface them in the
``EngagementPauseState`` response.  The seam is extended minimally:
``emit_engagement_paused_changed`` collects and returns listener return values as
a list.  The existing ``emit_slot_limit_changed`` is unchanged (it discards
listener return values — no callers depend on them; confirmed by grep).  Only
the pause event needs return-value collection, so we avoid widening the
slot_limit seam.
"""

from collections.abc import Callable
from uuid import UUID

# A listener is notified with (engagement_id, new_slot_limit).
SlotLimitListener = Callable[[UUID, int], None]

# A pause listener is notified with (engagement_id, paused) and returns
# (killed_running, dequeued) counts so the service can surface them.
PausedListener = Callable[[UUID, bool], tuple[int, int]]

_slot_limit_listeners: list[SlotLimitListener] = []
_paused_listeners: list[PausedListener] = []


def on_slot_limit_changed(listener: SlotLimitListener) -> None:
    """Register *listener* to be invoked whenever an engagement's slot limit changes.

    Idempotent by listener identity: registering the same callable twice (e.g.
    across repeated ``create_app()`` calls in tests) is a no-op.
    """
    if listener not in _slot_limit_listeners:
        _slot_limit_listeners.append(listener)


def emit_slot_limit_changed(engagement_id: UUID, slot_limit: int) -> None:
    """Notify all registered listeners that *engagement_id*'s slot limit is now *slot_limit*."""
    # Iterate a snapshot so a listener that (un)registers during dispatch can't
    # mutate the list mid-iteration.
    for listener in list(_slot_limit_listeners):
        listener(engagement_id, slot_limit)


# ---------------------------------------------------------------------------
# Slice 06 — engagement_paused_changed event seam
# ---------------------------------------------------------------------------


def on_engagement_paused_changed(listener: PausedListener) -> None:
    """Register *listener* to be invoked whenever an engagement's pause state changes.

    The listener receives ``(engagement_id, paused)`` and must return
    ``(killed_running, dequeued)`` — the counts of in-flight runs killed and
    queued runs removed by the pause action.  On resume (``paused=False``) the
    listener is expected to return ``(0, 0)``.

    Idempotent by listener identity: registering the same callable twice is a
    no-op (mirrors ``on_slot_limit_changed``).
    """
    if listener not in _paused_listeners:
        _paused_listeners.append(listener)


def emit_engagement_paused_changed(engagement_id: UUID, paused: bool) -> list[tuple[int, int]]:
    """Notify all registered listeners that *engagement_id*'s pause state changed.

    Returns a list of ``(killed_running, dequeued)`` tuples — one per registered
    listener.  The service aggregates these to produce the final counts for the
    ``EngagementPauseState`` response.

    Recommended use (Slice 06 cite): the mcp feature registers a single listener
    at the composition root (``app/main.py``) that calls
    ``concurrency.set_paused(engagement_id, paused)`` and returns its
    ``(killed_running, dequeued)`` tuple.  Only one listener is expected in
    production; the list return lets unit tests register stubs without special-
    casing.
    """
    results: list[tuple[int, int]] = []
    for listener in list(_paused_listeners):
        results.append(listener(engagement_id, paused))
    return results


def _reset() -> None:
    """Clear all registered listeners.  For use in tests only."""
    _slot_limit_listeners.clear()
    _paused_listeners.clear()
