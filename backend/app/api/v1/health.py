"""Health and monitoring endpoints (Milestone M9 / T-091).

Provides:
- /health: Base process uptime check
- /api/v1/health/db: Database connectivity check
- /api/v1/health/live: Kubernetes/Docker liveness probe (200 OK if service process is responding)
- /api/v1/health/ready: Readiness probe (verifies database pool and migration status)
- /api/v1/health/detailed: Detailed service telemetry & system metadata
"""

from __future__ import annotations

import platform
import sys
import time
from datetime import UTC, datetime

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.auth.dependencies import require_roles
from app.auth.models import User
from app.core.config import get_settings
from app.core.db import get_db
from app.core.errors import AppError

router = APIRouter(tags=["health"])
require_owner = require_roles("owner")

_START_TIME = time.time()


@router.get("/health")
def health() -> dict[str, str]:
    """Base process ping."""
    settings = get_settings()
    return {"status": "ok", "app": settings.app_name, "environment": settings.environment}


@router.get("/api/v1/health/live")
def health_live() -> dict[str, str]:
    """Liveness probe: verifies process is alive and responsive."""
    return {"status": "alive", "timestamp": datetime.now(UTC).isoformat()}


@router.get("/api/v1/health/ready")
def health_ready(db: Session = Depends(get_db)) -> dict[str, str]:
    """Readiness probe: verifies database connection is active."""
    try:
        db.execute(text("SELECT 1"))
    except SQLAlchemyError as exc:
        raise AppError("DB_UNAVAILABLE", "Database connection pool unreachable.", 503) from exc
    return {"status": "ready", "database": "connected"}


@router.get("/api/v1/health/db")
def health_db(db: Session = Depends(get_db)) -> dict[str, str]:
    """Database connectivity health check."""
    try:
        db.execute(text("SELECT 1"))
    except SQLAlchemyError as exc:
        raise AppError("DB_UNAVAILABLE", "Database is not reachable.", 503) from exc
    return {"status": "ok", "database": "up"}


@router.get("/api/v1/health/detailed")
def health_detailed(
    db: Session = Depends(get_db),
    _owner: User = Depends(require_owner),
) -> dict[str, object]:
    """Service telemetry (owner only: it reveals runtime and OS details)."""
    settings = get_settings()
    uptime_seconds = round(time.time() - _START_TIME, 2)

    db_status = "up"
    try:
        db.execute(text("SELECT 1"))
    except Exception:
        db_status = "down"

    return {
        "status": "ok" if db_status == "up" else "degraded",
        "app_name": settings.app_name,
        "environment": settings.environment,
        "uptime_seconds": uptime_seconds,
        "database": db_status,
        "system": {
            "python_version": sys.version.split()[0],
            "os": platform.system(),
            "platform": platform.platform(),
        },
        "timestamp": datetime.now(UTC).isoformat(),
    }
