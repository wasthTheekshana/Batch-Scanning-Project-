"""The admin dashboard: four static files and nothing else. The shell holds
no data; every number comes from the authenticated /admin API."""
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import RedirectResponse, Response

router = APIRouter(include_in_schema=False)

ROOT = Path(__file__).parent / "dashboard"
_FILES = {"index.html": "text/html; charset=utf-8",
          "app.js": "text/javascript; charset=utf-8",
          "form.js": "text/javascript; charset=utf-8",
          "style.css": "text/css; charset=utf-8"}

CSP = ("default-src 'none'; script-src 'self'; style-src 'self'; "
       "connect-src 'self'; img-src 'self' data:; base-uri 'none'; "
       "form-action 'none'; frame-ancestors 'none'")


def _serve(name: str) -> Response:
    path = ROOT / name
    try:
        body = path.read_bytes()
    except OSError:
        raise HTTPException(404, "not found")
    return Response(body, media_type=_FILES[name].split(";")[0],
                    headers={"Content-Type": _FILES[name],
                             "Content-Security-Policy": CSP})


@router.get("/")
def root():
    return RedirectResponse("/dashboard")


@router.get("/dashboard")
def page():
    return _serve("index.html")


@router.get("/dashboard/{name}")
def asset(name: str):
    if name not in _FILES or name == "index.html":
        raise HTTPException(404, "not found")
    return _serve(name)
