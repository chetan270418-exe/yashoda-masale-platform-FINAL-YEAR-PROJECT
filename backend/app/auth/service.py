"""Auth business logic — registration, login, staff creation."""

from sqlalchemy.orm import Session

from app.audit.service import log_action
from app.auth import repository as repo
from app.auth.models import User
from app.auth.schemas import LoginRequest, StaffCreate, TokenResponse, UserCreate
from app.auth.security import create_access_token, hash_password, verify_password
from app.core.config import get_settings
from app.core.errors import AppError


def register_consumer(db: Session, data: UserCreate) -> User:
    """Self-registration for consumers only (B10).

    Raises AppError if the email is already taken or the consumer role
    is missing from the database.
    """
    if repo.get_user_by_email(db, data.email):
        raise AppError("DUPLICATE_EMAIL", "A user with this email already exists.", 409)

    consumer_role = repo.get_role_by_name(db, "consumer")
    if consumer_role is None:
        raise AppError("ROLE_MISSING", "Consumer role has not been seeded.", 500)

    user = repo.create_user(
        db,
        email=data.email,
        password_hash=hash_password(data.password),
        full_name=data.full_name,
        role_id=consumer_role.id,
    )
    db.commit()
    return user


def create_staff_user(db: Session, data: StaffCreate, created_by: User) -> User:
    """Owner creates a staff user with an explicit role.

    Only the owner role may call this. The chosen role must exist and
    must not be 'consumer' (consumers self-register via B10).
    """
    if created_by.role.name != "owner":
        raise AppError("FORBIDDEN", "Only the owner can create staff.", 403)

    if data.role_name == "consumer":
        raise AppError(
            "INVALID_ROLE",
            "Use the public registration endpoint for consumers.",
            400,
        )

    target_role = repo.get_role_by_name(db, data.role_name)
    if target_role is None:
        raise AppError("ROLE_NOT_FOUND", f"Role '{data.role_name}' does not exist.", 404)

    if repo.get_user_by_email(db, data.email):
        raise AppError("DUPLICATE_EMAIL", "A user with this email already exists.", 409)

    user = repo.create_user(
        db,
        email=data.email,
        password_hash=hash_password(data.password),
        full_name=data.full_name,
        role_id=target_role.id,
    )
    log_action(
        db,
        actor_id=created_by.id,
        action="CREATE_STAFF_USER",
        entity="users",
        entity_id=user.id,
        after={"email": user.email, "role": target_role.name, "full_name": user.full_name},
    )
    db.commit()
    return user


def authenticate(db: Session, email: str, password: str) -> User:
    """Verify credentials and return the user.

    Raises AppError on bad credentials or inactive account.
    """
    user = repo.get_user_by_email(db, email)
    if user is None or not verify_password(password, user.password_hash):
        raise AppError("BAD_CREDENTIALS", "Invalid email or password.", 401)
    if not user.is_active:
        raise AppError("ACCOUNT_DISABLED", "This account has been deactivated.", 403)
    return user


def login(db: Session, data: LoginRequest) -> TokenResponse:
    """Authenticate and return a JWT token response."""
    user = authenticate(db, data.email, data.password)
    settings = get_settings()
    token = create_access_token({"sub": str(user.id), "role": user.role.name})
    return TokenResponse(
        access_token=token,
        token_type="bearer",
        expires_in=settings.access_token_expire_minutes * 60,
    )
