"""Domain exceptions for the graph feature.

Defined here (not in service.py) so that both writer.py and service.py can
import them without creating a circular dependency.

HTTP translation happens in router.py via the core error-handler registry.

Exception → HTTP mapping (via Starlette MRO-based handler lookup):
  NodeNotFound      → NotFoundError  → 404
  EdgeNotFound      → NotFoundError  → 404
  NoHistory         → NotFoundError  → 404
  DuplicateEdge     → ConflictError  → 409
  EngagementArchived → ConflictError → 409

The core handlers (app.core.errors.handlers) register handlers for the base
classes NotFoundError and ConflictError.  Starlette walks type(exc).__mro__ when
looking up the handler, so all subclasses are covered automatically — no new
handler registrations are needed in core/.
"""

from app.core.errors import ConflictError, NotFoundError


class GraphDomainError(Exception):
    """Base class for all graph domain exceptions.

    Not a subclass of AdeptusError here — subclasses carry their own HTTP
    semantic by inheriting from the appropriate core error base (NotFoundError
    or ConflictError) directly, giving them both the graph taxonomy and the
    correct HTTP mapping.
    """


class NodeNotFound(NotFoundError):
    """Raised when a GraphNode cannot be found, has been deleted, or belongs to
    a different engagement.  Maps to HTTP 404 via the NotFoundError handler."""

    def __init__(self, message: str = "Node not found") -> None:
        super().__init__(message)


class EdgeNotFound(NotFoundError):
    """Raised when a GraphEdge cannot be found, has been deleted, or belongs to
    a different engagement.  Maps to HTTP 404 via the NotFoundError handler."""

    def __init__(self, message: str = "Edge not found") -> None:
        super().__init__(message)


class NoHistory(NotFoundError):
    """Raised when an undo is requested but no prior history entry exists for
    the entity.  Maps to HTTP 404 via the NotFoundError handler."""

    def __init__(self, message: str = "No prior state to revert to") -> None:
        super().__init__(message)


class DuplicateEdge(ConflictError):
    """Raised when a create-edge command would produce a duplicate live triple
    (engagement_id, source_id, target_id, relation).  Maps to HTTP 409 via the
    ConflictError handler."""

    def __init__(self, message: str = "A live edge with the same triple already exists") -> None:
        super().__init__(message)


class EngagementArchived(ConflictError):
    """Raised when a write operation is attempted against an archived engagement.

    §4: archived engagements are read-only — graph reads remain allowed, but all
    writes are rejected.  Maps to HTTP 409 via the ConflictError handler.
    """

    def __init__(self, message: str = "Engagement is archived (read-only)") -> None:
        super().__init__(message)
