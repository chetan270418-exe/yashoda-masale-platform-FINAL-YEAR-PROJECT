"""FastAPI dependencies for authentication and role-based access control."""

from collections.abc import Callable
from typing import Annotated

from fastapi import Depends, Header
from sqlalchemy.orm import Session

from app.auth import repository as repo
from app.auth.models import User
from app.auth.security import decode_access_token
from app.core.config import get_settings
from app.core.db import get_db
from app.core.errors import AppError
from app.core.rate_limit import RateLimiter


def get_current_user(
    authorization: Annotated[str | None, Header()] = None,
    db: Session = Depends(get_db),
) -> User:
    """Extract and validate the current user from the Authorization header.

    Expected format: ``Authorization: Bearer <jwt>``
    Raises 401 if the token is missing, malformed or the user does not exist.
    """
    if not authorization or not authorization.startswith("Bearer "):
        raise AppError("UNAUTHORIZED", "Missing or invalid Authorization header.", 401)

    token = authorization.removeprefix("Bearer ").strip()
    payload = decode_access_token(token)
    if payload is None or "sub" not in payload:
        raise AppError("UNAUTHORIZED", "Invalid or expired token.", 401)

    user = repo.get_user_by_id(db, payload["sub"])
    if user is None:
        raise AppError("UNAUTHORIZED", "User not found.", 401)
    if not user.is_active:
        raise AppError("ACCOUNT_DISABLED", "This account has been deactivated.", 403)
    return user


def get_optional_current_user(
    authorization: Annotated[str | None, Header()] = None,
    db: Session = Depends(get_db),
) -> User | None:
    """Return active user if a valid Authorization header is present, else None.

    Deactivated users are treated as anonymous (deactivated dealers lose wholesale prices).
    """
    if not authorization or not authorization.startswith("Bearer "):
        return None
    token = authorization.removeprefix("Bearer ").strip()
    payload = decode_access_token(token)
    if payload is None or "sub" not in payload:
        return None
    user = repo.get_user_by_id(db, payload["sub"])
    if user is None or not user.is_active:
        return None
    return user


def require_roles(*allowed_roles: str) -> Callable[..., User]:
    """Return a dependency that rejects users whose role is not in *allowed_roles* (spec six roles).

    Usage::

        @router.get("/admin-only")
        def admin_view(user: User = Depends(require_roles("owner"))):
            ...
    """
    allowed_set = set(allowed_roles)

    def _check(user: User = Depends(get_current_user)) -> User:
        if user.role.name not in allowed_set:
            raise AppError(
                "FORBIDDEN",
                f"Role '{user.role.name}' is not allowed. "
                f"Required: {', '.join(allowed_roles)}.",
                403,
            )
        return user

    return _check


# Backwards compatibility alias
require_role = require_roles

# Reusable singletons for common roles (prevents B008 ruff warning in route signatures)
require_owner = require_roles("owner")

# Rate limiter for login endpoint
login_rate_limiter = RateLimiter(
    max_requests=get_settings().login_rate_limit_max,
    window_seconds=get_settings().login_rate_limit_window_seconds,
    name="login",
)
