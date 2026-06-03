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
"""

from collections.abc import Callable
from uuid import UUID

# A listener is notified with (engagement_id, new_slot_limit).
SlotLimitListener = Callable[[UUID, int], None]

_slot_limit_listeners: list[SlotLimitListener] = []


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


def _reset() -> None:
    """Clear all registered listeners.  For use in tests only."""
    _slot_limit_listeners.clear()
