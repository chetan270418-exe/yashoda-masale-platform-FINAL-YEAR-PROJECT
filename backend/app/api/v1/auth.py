"""Auth HTTP endpoints under /api/v1/auth (rules.md section 6)."""

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.auth import repository as repo
from app.auth import service
from app.auth.dependencies import get_current_user, login_rate_limiter, require_owner
from app.auth.models import User
from app.auth.schemas import (
    LoginRequest,
    StaffCreate,
    TokenResponse,
    UserCreate,
    UserRead,
)
from app.core.db import get_db

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


def _user_to_read(user: User) -> UserRead:
    return UserRead(
        id=user.id,
        email=user.email,
        full_name=user.full_name,
        role_name=user.role.name,
        is_active=user.is_active,
        created_at=user.created_at,
    )


@router.post("/register", response_model=UserRead, status_code=201)
def register(body: UserCreate, db: Session = Depends(get_db)) -> UserRead:
    """Consumer self-registration (B10: always Consumer)."""
    user = service.register_consumer(db, body)
    return _user_to_read(user)


@router.post("/login", response_model=TokenResponse, dependencies=[Depends(login_rate_limiter)])
def login(body: LoginRequest, db: Session = Depends(get_db)) -> TokenResponse:
    """Authenticate and receive a JWT. Protected by rate limiting."""
    return service.login(db, body)


@router.get("/me", response_model=UserRead)
def me(user: User = Depends(get_current_user)) -> UserRead:
    """Return the authenticated user's profile."""
    return _user_to_read(user)


@router.post("/staff", response_model=UserRead, status_code=201)
def create_staff(
    body: StaffCreate,
    db: Session = Depends(get_db),
    user: User = Depends(require_owner),
) -> UserRead:
    """Owner creates staff, dealer or distributor accounts."""
    new_user = service.create_staff_user(db, body, user)
    return _user_to_read(new_user)


@router.get("/users", response_model=list[UserRead])
def list_users(
    db: Session = Depends(get_db),
    _user: User = Depends(require_owner),
) -> list[UserRead]:
    """List all users (owner only)."""
    users = repo.get_all_users(db)
    return [_user_to_read(u) for u in users]
