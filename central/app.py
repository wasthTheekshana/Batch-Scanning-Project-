"""FastAPI application factory."""
from contextlib import asynccontextmanager

from fastapi import FastAPI
from starlette.middleware.sessions import SessionMiddleware

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

    @app.get("/healthz")
    def healthz():
        return {"status": "ok"}

    return app
