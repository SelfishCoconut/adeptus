"""Domain exceptions for the graph feature.

Defined here (not in service.py) so that both writer.py and service.py can
import them without creating a circular dependency.

HTTP translation happens in router.py via the core error-handler registry.
"""

from app.core.errors import AdeptusError


class GraphDomainError(AdeptusError):
    """Base class for all graph domain exceptions."""


class NodeNotFound(GraphDomainError):
    """Raised when a GraphNode cannot be found, has been deleted, or belongs to
    a different engagement."""

    def __init__(self, message: str = "Node not found") -> None:
        super().__init__(message)


class EdgeNotFound(GraphDomainError):
    """Raised when a GraphEdge cannot be found, has been deleted, or belongs to
    a different engagement."""

    def __init__(self, message: str = "Edge not found") -> None:
        super().__init__(message)


class NoHistory(GraphDomainError):
    """Raised when an undo is requested but no prior history entry exists for
    the entity.  Maps to HTTP 404 in the router."""

    def __init__(self, message: str = "No prior state to revert to") -> None:
        super().__init__(message)


class DuplicateEdge(GraphDomainError):
    """Raised when a create-edge command would produce a duplicate live triple
    (engagement_id, source_id, target_id, relation).  Maps to HTTP 409."""

    def __init__(self, message: str = "A live edge with the same triple already exists") -> None:
        super().__init__(message)
