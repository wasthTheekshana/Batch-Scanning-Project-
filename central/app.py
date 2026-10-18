"""FastAPI application factory."""
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from starlette.middleware.sessions import SessionMiddleware
from starlette.responses import JSONResponse

from central.db import make_engine, make_session_factory
from central.settings import Settings


@asynccontextmanager
async def _lifespan(app: FastAPI):
    yield
    app.state.engine.dispose()


def create_app(settings: Settings) -> FastAPI:
    engine = make_engine(settings.database_url)
    # API docs are off: the surface is small and not worth advertising.
    app = FastAPI(title="dokscanner-central", docs_url=None, redoc_url=None,
                  openapi_url=None, lifespan=_lifespan)
    app.state.settings = settings
    app.state.engine = engine
    app.state.session_factory = make_session_factory(engine)
    app.add_middleware(
        SessionMiddleware, secret_key=settings.session_secret,
        session_cookie="dok_central", https_only=settings.cookie_secure,
        same_site="strict", max_age=8 * 3600)

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(request, exc):
        """Global validation error handler: return only type, loc, msg (no input/ctx).
        This prevents echoing back user-supplied values in error responses.
        For /pcs/register, return 400 with enrollment code error message instead of 422."""
        if request.url.path == "/pcs/register":
            return JSONResponse(
                status_code=400,
                content={"detail": "invalid or expired enrollment code"}
            )
        errors = []
        for error in exc.errors():
            # Extract only safe fields: type, loc, msg
            error_dict = {
                "type": error.get("type"),
                "loc": error.get("loc"),
                "msg": error.get("msg")
            }
            errors.append(error_dict)
        return JSONResponse(
            status_code=422,
            content={"detail": errors}
        )

    @app.middleware("http")
    async def security_headers(request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        path = request.url.path
        if path.startswith(("/admin", "/dashboard")):
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.exception_handler(RecursionError)
    async def recursion_error_handler(request, exc):
        """Handle deeply nested JSON bodies that cause RecursionError during parsing."""
        return JSONResponse(
            status_code=422,
            content={"detail": "request body nested too deeply"}
        )

    from central import (routes_admin_auth, routes_admin_config,
                         routes_admin_fleet, routes_admin_overview,
                         routes_admin_pcs, routes_admin_users,
                         routes_dashboard, routes_pcs)
    app.include_router(routes_admin_auth.router)
    app.include_router(routes_admin_users.router)
    app.include_router(routes_admin_overview.router)
    app.include_router(routes_admin_fleet.router)
    app.include_router(routes_admin_pcs.router)
    app.include_router(routes_admin_config.router)
    app.include_router(routes_pcs.router)
    app.include_router(routes_dashboard.router)

    @app.get("/healthz")
    def healthz():
        return {"status": "ok"}

    return app


def create_app_from_env() -> FastAPI:
    """uvicorn factory entry point (reads CENTRAL_* environment variables)."""
    return create_app(Settings.from_env())
