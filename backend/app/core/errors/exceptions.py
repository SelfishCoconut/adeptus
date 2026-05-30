class AdeptusError(Exception):
    """Base exception for all domain errors."""

    def __init__(self: "AdeptusError", message: str = "") -> None:
        self.message = message
        super().__init__(message)

    def __str__(self: "AdeptusError") -> str:
        return self.message


class NotFoundError(AdeptusError):
    """Raised when a requested resource does not exist."""

    def __init__(self: "NotFoundError", message: str = "Resource not found") -> None:
        super().__init__(message)


class AuthenticationError(AdeptusError):
    """Raised when authentication fails or credentials are missing."""

    def __init__(self: "AuthenticationError", message: str = "Authentication required") -> None:
        super().__init__(message)


class ForbiddenError(AdeptusError):
    """Raised when the caller lacks permission to perform an action."""

    def __init__(self: "ForbiddenError", message: str = "Access forbidden") -> None:
        super().__init__(message)


class ValidationError(AdeptusError):
    """Raised for domain-level validation failures (distinct from Pydantic request validation)."""

    def __init__(self: "ValidationError", message: str = "Validation failed") -> None:
        super().__init__(message)
