"""Composition-root listener functions for the mcp feature.

These listeners are registered at app startup (``app/main.py``) into the
engagement event seam (``app/features/engagements/events``).  They live here
rather than inline in ``main.py`` so that:

  1. Business logic stays in the mcp feature, not in the composition root.
  2. The functions are importable and independently testable in
     ``test_engagements_pause_event.py`` without loading the full FastAPI app.
  3. The dependency direction is maintained: mcp imports engagements' event seam
     (to register) but engagements does NOT import mcp (engagements only emits
     through the seam).

Slice 06 cite (task 7, recommended approach): the engagements service emits
``engagement_paused_changed``; this listener calls
``concurrency.set_paused(engagement_id, paused)`` and returns its
``(killed_running, dequeued)`` tuple via the event-dispatch return value, so the
engagements service can aggregate counts for the ``EngagementPauseState``
response.  Only the listener return value crosses the seam — no direct import of
mcp from engagements is ever needed.
"""

from uuid import UUID

from app.features.mcp import concurrency


def on_engagement_paused_changed(engagement_id: UUID, paused: bool) -> tuple[int, int]:
    """mcp listener for the ``engagement_paused_changed`` event.

    Called synchronously by ``emit_engagement_paused_changed`` in the
    engagements event seam whenever an engagement's pause state changes.

    Delegates to ``concurrency.set_paused`` which:
    - On pause (``paused=True``): cancels every live task for the engagement,
      resolves every awaiting-decision run as killed, de-queues every FIFO
      ticket, and sets the pause flag so subsequent ``acquire`` calls raise
      ``EngagementPaused``.
    - On resume (``paused=False``): clears the pause flag only.

    Returns ``(killed_running, dequeued)`` — the counts produced by the
    in-process kill action — so the engagements service can surface them in
    ``EngagementPauseState``.
    """
    return concurrency.set_paused(engagement_id, paused)
