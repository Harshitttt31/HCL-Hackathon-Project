"""FastAPI application entry point: `uvicorn app.main:app`."""
from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.api.auth_routes import router as auth_router
from app.api.deps import get_service
from app.api.routes import router
from app.core.config import get_settings
from app.core.logging import configure_logging, get_logger

log = get_logger(__name__)
WEB_DIR = Path(__file__).resolve().parent / "web"


@asynccontextmanager
async def lifespan(app: FastAPI):
    s = get_settings()
    configure_logging(s.log_level)
    log.info("starting", extra={"llm_backend": s.effective_llm_backend, "embedding_backend": s.embedding_backend, "model": s.llm_model,
                                "auth_required": s.auth_required})
    if not s.jwt_secret:
        log.warning("jwt_secret_not_set", extra={"effect": "a random per-process secret is used; sign-ins end when the API restarts"})
    get_service()  # build the retriever, tool registry and graph once, at startup
    yield


def create_app() -> FastAPI:
    app = FastAPI(title=get_settings().app_name, version="1.0.0", lifespan=lifespan,
                  description="Authority-aware academic assistant: source precedence, deterministic tools, privacy and audit.")
    app.include_router(router)
    app.include_router(auth_router)

    # Web interface: one static page plus its assets, served by the API itself (no separate front-end server).
    app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")

    @app.get("/", include_in_schema=False)
    def web_ui():
        return FileResponse(WEB_DIR / "index.html", headers={
            "Cache-Control": "no-cache",
            "Content-Security-Policy": "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; frame-ancestors 'none'",
            "X-Content-Type-Options": "nosniff", "Referrer-Policy": "no-referrer"})

    @app.exception_handler(RequestValidationError)
    async def validation_handler(request: Request, exc: RequestValidationError):
        detail = [f"{'.'.join(str(p) for p in e['loc'] if p != 'body')}: {e['msg']}" for e in exc.errors()]
        return JSONResponse(status_code=422, content={"detail": detail})

    @app.exception_handler(Exception)
    async def unhandled(request: Request, exc: Exception):
        ref = uuid.uuid4().hex[:8]
        log.error("unhandled_error", extra={"error_ref": ref, "error": type(exc).__name__, "path": request.url.path})
        return JSONResponse(status_code=500, content={"detail": "internal error", "error_ref": ref})

    return app


app = create_app()
