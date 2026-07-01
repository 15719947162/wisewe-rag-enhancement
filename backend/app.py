from __future__ import annotations

import importlib.util
import inspect
import os
from contextlib import asynccontextmanager
from pathlib import Path

import starlette.routing as _starlette_routing

# starlette 1.0 removed on_startup/on_shutdown from Router.__init__,
# but FastAPI 0.115 still passes them AND reads them as attributes.
# Patch 1: make __init__ silently drop the removed kwargs.
if "on_startup" not in inspect.signature(_starlette_routing.Router.__init__).parameters:
    _orig_router_init = _starlette_routing.Router.__init__

    def _compat_router_init(self, *args, on_startup=None, on_shutdown=None, lifespan=None, **kwargs):
        return _orig_router_init(self, *args, lifespan=lifespan, **kwargs)

    _starlette_routing.Router.__init__ = _compat_router_init  # type: ignore[method-assign]

# Patch 2: FastAPI's include_router reads router.on_startup / router.on_shutdown
# as instance attributes; add them if missing.
if not hasattr(_starlette_routing.Router, "on_startup"):
    _starlette_routing.Router.on_startup = []  # type: ignore[attr-defined]
if not hasattr(_starlette_routing.Router, "on_shutdown"):
    _starlette_routing.Router.on_shutdown = []  # type: ignore[attr-defined]

from fastapi import FastAPI
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, ORJSONResponse
from fastapi.staticfiles import StaticFiles

from backend.routes import console, dashboard, eval, health, identity, ingestion, knowledge_bases, openapi_v1, parse, rag
from backend.services.identity_sync_scheduler import start_identity_sync_scheduler, stop_identity_sync_scheduler
from core.config import load_project_env
from core.runtime_settings import apply_runtime_env_overrides


_DEFAULT_CORS_HEADERS = [
    "Accept",
    "Accept-Language",
    "Authorization",
    "Content-Language",
    "Content-Type",
    "X-API-Key",
    "X-KB-Tenant-Id",
    "X-KB-User-Id",
    "X-Requested-With",
]


def _get_default_response_class():
    # ORJSONResponse is faster, but it raises at runtime if orjson is absent.
    # Docker/backend health checks should still work in minimal images.
    if importlib.util.find_spec("orjson") is None:
        return JSONResponse
    return ORJSONResponse


def _get_cors_origins() -> list[str]:
    configured = os.getenv("KB_CORS_ALLOW_ORIGINS") or os.getenv("CORS_ALLOW_ORIGINS")
    if configured:
        return [item.strip() for item in configured.split(",") if item.strip()]
    return [
        "http://127.0.0.1:3000",
        "http://localhost:3000",
    ]


def _get_cors_headers() -> list[str]:
    configured = os.getenv("KB_CORS_ALLOW_HEADERS") or os.getenv("CORS_ALLOW_HEADERS")
    if configured:
        return [item.strip() for item in configured.split(",") if item.strip()]
    return _DEFAULT_CORS_HEADERS


@asynccontextmanager
async def _app_lifespan(_app: FastAPI):
    await start_identity_sync_scheduler()
    try:
        yield
    finally:
        await stop_identity_sync_scheduler()


def create_app() -> FastAPI:
    load_project_env()
    apply_runtime_env_overrides()

    app = FastAPI(
        title="WiseWe RAG Console API",
        version="0.1.0",
        default_response_class=_get_default_response_class(),
        lifespan=_app_lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=_get_cors_origins(),
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=_get_cors_headers(),
    )

    app.include_router(health.router)
    app.include_router(identity.router)
    app.include_router(knowledge_bases.router)
    app.include_router(console.router)
    app.include_router(parse.router)
    app.include_router(rag.router)
    app.include_router(openapi_v1.router)
    app.include_router(eval.router)
    app.include_router(ingestion.router)
    app.include_router(dashboard.router)

    @app.exception_handler(RequestValidationError)
    async def _validation_exception_handler(request, exc):
        if str(request.url.path).startswith("/openapi/v1/"):
            return openapi_v1.validation_error_response(exc.errors())
        return await request_validation_exception_handler(request, exc)

    output_dir = Path("data/output")
    output_dir.mkdir(parents=True, exist_ok=True)
    app.mount("/api/assets/output", StaticFiles(directory=output_dir), name="output-assets")

    return app


app = create_app()
