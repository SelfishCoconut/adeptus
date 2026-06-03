"""Tests for the engagements event seam (in-process slot-limit observer).

The seam inverts the engagements → mcp dependency: engagements emits
``slot_limit_changed`` and listeners (registered at app startup) react.
"""

from uuid import uuid4

import pytest

from app.features.engagements import events


@pytest.fixture(autouse=True)
def _clean_listeners() -> object:
    """Each test starts and ends with an empty listener registry."""
    events._reset()
    yield
    events._reset()


def test_emit_invokes_registered_listener() -> None:
    """A registered listener is called with (engagement_id, slot_limit)."""
    received: list[tuple] = []
    events.on_slot_limit_changed(lambda eid, n: received.append((eid, n)))

    eng_id = uuid4()
    events.emit_slot_limit_changed(eng_id, 5)

    assert received == [(eng_id, 5)]


def test_emit_invokes_all_listeners_in_order() -> None:
    """Multiple listeners all fire, in registration order."""
    order: list[str] = []
    events.on_slot_limit_changed(lambda eid, n: order.append("first"))
    events.on_slot_limit_changed(lambda eid, n: order.append("second"))

    events.emit_slot_limit_changed(uuid4(), 3)

    assert order == ["first", "second"]


def test_registration_is_idempotent_by_identity() -> None:
    """Registering the same callable twice does not double-invoke it."""
    calls: list[int] = []

    def listener(eid: object, n: int) -> None:
        calls.append(n)

    events.on_slot_limit_changed(listener)
    events.on_slot_limit_changed(listener)  # second registration is a no-op

    events.emit_slot_limit_changed(uuid4(), 7)

    assert calls == [7]


def test_emit_with_no_listeners_is_a_noop() -> None:
    """Emitting with an empty registry does nothing (and does not raise)."""
    events.emit_slot_limit_changed(uuid4(), 4)  # must not raise


def test_reset_clears_listeners() -> None:
    """_reset removes all registered listeners."""
    calls: list[int] = []
    events.on_slot_limit_changed(lambda eid, n: calls.append(n))

    events._reset()
    events.emit_slot_limit_changed(uuid4(), 9)

    assert calls == []
