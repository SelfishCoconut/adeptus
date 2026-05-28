"""Business logic for authentication; raises domain exceptions."""

from uuid import UUID

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.errors import AuthenticationError, ValidationError
from app.features.auth import repository as repo
from app.features.auth.models import User

# Module-level hasher — argon2id with library defaults (memory, iterations, parallelism).
# Defaults are RFC 9106 compliant and exceed OWASP minimums.
_hasher = PasswordHasher()


async def authenticate_user(db: AsyncSession, *, username: str, password: str) -> User:
    """Verify credentials and return the matching User. Raises AuthenticationError on failure.

    Uses a constant-time-ish flow: even on unknown username we run a dummy hash verify
    so the response time does not betray whether the user exists.
    """
    user = await repo.get_user_by_username(db, username)
    if user is None:
        # Run a dummy verify to keep timing roughly constant.
        try:
            _hasher.verify("$argon2id$v=19$m=65536,t=3,p=4$YWJjZGVmZ2g$" + "A" * 43, password)
        except (VerifyMismatchError, VerificationError, InvalidHashError):
            pass
        raise AuthenticationError("Invalid credentials")

    try:
        _hasher.verify(user.password_hash, password)
    except VerifyMismatchError:
        raise AuthenticationError("Invalid credentials") from None
    except (VerificationError, InvalidHashError) as exc:
        raise AuthenticationError("Invalid credentials") from exc

    # Optional: rehash if parameters changed. Skip for slice 00 — keeps it simple.
    return user


async def bootstrap_admin(db: AsyncSession) -> User | None:
    """Idempotently create the admin user from settings.

    - Validate ADEPTUS_ADMIN_PASSWORD_HASH first (fail loudly on a non-argon2 value,
      even on a restart where the admin already exists — catches a misconfigured env
      var before it produces a silently unloginable account).
    - Atomically insert the admin via INSERT ... ON CONFLICT DO NOTHING, so concurrent
      startups cannot race (slice-00 security gate). Returns the new User, or None if
      the admin already existed.
    - Caller is responsible for committing (this is service-layer, not router-layer).

    Validation: the hash must start with "$argon2" — raises ValidationError otherwise.
    This catches the common mistake of placing a plaintext password in the env var.
    """
    settings = get_settings()
    pw_hash = settings.ADEPTUS_ADMIN_PASSWORD_HASH
    if not pw_hash.startswith("$argon2"):
        raise ValidationError(
            "ADEPTUS_ADMIN_PASSWORD_HASH does not look like an argon2 hash "
            "(should start with $argon2). Refusing to create an unloginable admin."
        )

    return await repo.create_admin_if_absent(
        db,
        username=settings.ADEPTUS_ADMIN_USER,
        password_hash=pw_hash,
    )


async def accept_terms(db: AsyncSession, *, user_id: UUID) -> User:
    """Record terms-of-use acceptance. Idempotent — calling twice is safe."""
    return await repo.update_terms_accepted(db, user_id)
