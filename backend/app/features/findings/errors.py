"""Domain exceptions for the findings feature.

HTTP translation happens in router.py via the core error-handler registry. The
core handlers (app.core.errors.handlers) register handlers for the base classes
NotFoundError and ConflictError; Starlette walks ``type(exc).__mro__`` when
looking up the handler, so every subclass below is covered automatically — no new
registration in core/ is needed (no ADR / no core widening).

Exception → HTTP mapping:
  EngagementNotFound  → NotFoundError  → 404
  FindingNotFound     → NotFoundError  → 404
  LinkedNodeNotFound  → NotFoundError  → 404
  EngagementArchived  → ConflictError  → 409
"""

from app.core.errors import ConflictError, NotFoundError


class EngagementNotFound(NotFoundError):
    """Raised when the engagement does not exist OR the caller is not a member.

    Collapses both cases into the same 404 to avoid existence disclosure (§17.1).
    No admin bypass — the membership query never consults role (§4). Mirrors
    graph.service.EngagementNotFound / mcp.service.EngagementNotFound.
    """

    def __init__(self, message: str = "Engagement not found") -> None:
        super().__init__(message)


class FindingNotFound(NotFoundError):
    """Raised when a finding cannot be found in the engagement (or is in another
    engagement). Maps to HTTP 404 via the NotFoundError handler."""

    def __init__(self, message: str = "Finding not found") -> None:
        super().__init__(message)


class LinkedNodeNotFound(NotFoundError):
    """Raised when a ``node_id`` to link does not name a live node in THIS
    engagement (missing, soft-deleted, or cross-engagement).

    A distinct 404 message from FindingNotFound, but it must NOT reveal whether
    the node exists in some *other* engagement (§17.1 isolation). Maps to 404.
    """

    def __init__(self, message: str = "Linked node not found in this engagement") -> None:
        super().__init__(message)


class EngagementArchived(ConflictError):
    """Raised when a write is attempted against an archived engagement.

    §4: archived engagements are read-only — finding reads remain allowed, but all
    writes are rejected. Maps to HTTP 409 via the ConflictError handler.
    """

    def __init__(self, message: str = "Engagement is archived (read-only)") -> None:
        super().__init__(message)
