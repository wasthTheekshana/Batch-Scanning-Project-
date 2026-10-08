"""Route-enumeration guards: every /admin route (except login) must require
a session, and every mutating one must also require the CSRF header."""
import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from central.manage import create_admin

_SKIP_METHODS = {"HEAD", "OPTIONS"}
_PARAM_VALUES = {"version": "1"}


def _api_routes(routes, prefix=""):
    """Flatten app.routes, also descending into include_router() wrappers
    (newer FastAPI wraps included routers lazily)."""
    for route in routes:
        if isinstance(route, APIRoute):
            yield prefix + route.path, route
        elif hasattr(route, "original_router"):
            yield from _api_routes(
                route.original_router.routes,
                prefix + route.include_context.prefix)


def _admin_requests(app):
    """(method, concrete_url) for every /admin route except /admin/login."""
    out = []
    for path, route in _api_routes(app.routes):
        if not path.startswith("/admin") or path == "/admin/login":
            continue
        url = path
        for name in route.param_convertors:
            url = url.replace("{%s}" % name, _PARAM_VALUES.get(name, "x"))
        assert "{" not in url, f"cannot instantiate {path}"
        for method in sorted(route.methods - _SKIP_METHODS):
            out.append((method, url))
    return out


def test_route_list_is_large_enough_not_to_shrink_silently(app):
    assert len(_admin_requests(app)) >= 20


def test_every_admin_route_requires_login(app):
    requests = _admin_requests(app)
    assert len(requests) >= 20
    with TestClient(app) as anon:
        for method, url in requests:
            r = anon.request(method, url, json={})
            assert r.status_code == 401, (method, url, r.status_code)


def test_every_admin_mutation_requires_csrf(app):
    create_admin(app.state.session_factory, "root", "correct horse battery")
    mutations = [(m, u) for m, u in _admin_requests(app) if m != "GET"]
    assert len(mutations) >= 9
    with TestClient(app) as c:
        r = c.post("/admin/login", json={
            "username": "root", "password": "correct horse battery"})
        assert r.status_code == 200
        assert "x-csrf-token" not in c.headers
        for method, url in mutations:
            r = c.request(method, url, json={})
            assert r.status_code == 403, (method, url, r.status_code)


@pytest.mark.parametrize("bad", ["", "wrong"])
def test_csrf_header_must_match(app, bad):
    create_admin(app.state.session_factory, "root", "correct horse battery")
    with TestClient(app) as c:
        c.post("/admin/login", json={
            "username": "root", "password": "correct horse battery"})
        r = c.put("/admin/settings", json={"poll_interval_seconds": 60},
                  headers={"X-CSRF-Token": bad})
        assert r.status_code == 403
