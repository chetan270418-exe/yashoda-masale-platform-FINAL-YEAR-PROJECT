"""FastAPI entry point. Modular monolith: each module adds its router here."""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.ai import router as ai_router
from app.api.v1 import auth, health
from app.catalog import router as catalog_router
from app.core.config import get_settings
from app.core.errors import register_error_handlers
from app.core.logging import register_request_logging
from app.inventory import router as inventory_router
from app.orders import router as orders_router


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title=settings.app_name, version="0.1.0")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[o.strip() for o in settings.cors_origins.split(",") if o.strip()],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    register_request_logging(app)
    register_error_handlers(app)
    app.include_router(health.router)
    app.include_router(auth.router)
    app.include_router(catalog_router.router)
    app.include_router(inventory_router.router)
    app.include_router(orders_router)
    app.include_router(ai_router.router)
    return app


app = create_app()
