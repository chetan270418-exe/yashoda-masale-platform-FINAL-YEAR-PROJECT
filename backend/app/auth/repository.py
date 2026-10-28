"""Auth data-access layer — thin queries, no business logic."""

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.models import Role, User


def get_user_by_email(db: Session, email: str) -> User | None:
    """Fetch a user by email (case-insensitive match)."""
    return db.scalars(select(User).where(User.email == email.lower())).first()


def get_user_by_id(db: Session, user_id: uuid.UUID | str) -> User | None:
    """Fetch a user by primary key.

    Accepts either a UUID object or its string representation (e.g. from JWT).
    """
    if isinstance(user_id, str):
        user_id = uuid.UUID(user_id)
    return db.get(User, user_id)


def create_user(
    db: Session,
    *,
    email: str,
    password_hash: str,
    full_name: str,
    role_id: uuid.UUID,
) -> User:
    """Insert a new user and flush so the caller can read generated fields."""
    user = User(
        email=email.lower(),
        password_hash=password_hash,
        full_name=full_name,
        role_id=role_id,
    )
    db.add(user)
    db.flush()
    return user


def get_role_by_name(db: Session, name: str) -> Role | None:
    """Fetch a role by its unique name."""
    return db.scalars(select(Role).where(Role.name == name)).first()


def get_all_roles(db: Session) -> list[Role]:
    """Return every role row."""
    return list(db.scalars(select(Role)).all())


def get_all_users(db: Session) -> list[User]:
    """Return all users (for owner listing)."""
    return list(db.scalars(select(User)).all())
