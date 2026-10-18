"""The dashboard shell: served statically with strict headers, and written
so that nothing from the API can ever be interpreted as HTML or script."""
import re
from pathlib import Path

import pytest

DASH = Path(__file__).parent.parent.parent / "central" / "dashboard"


def test_the_dashboard_pages_are_served_without_a_login(client):
    for path, kind in (("/dashboard", "text/html"),
                       ("/dashboard/app.js", "javascript"),
                       ("/dashboard/style.css", "text/css")):
        r = client.get(path)
        assert r.status_code == 200, path
        assert kind in r.headers["content-type"], path
        assert len(r.content) > 100


def test_the_root_redirects_to_the_dashboard(client):
    r = client.get("/", follow_redirects=False)
    assert r.status_code in (302, 307) and r.headers["location"] == "/dashboard"


def test_dashboard_responses_carry_a_strict_content_security_policy(client):
    csp = client.get("/dashboard").headers["content-security-policy"]
    for directive in ("default-src 'none'", "script-src 'self'",
                      "style-src 'self'", "connect-src 'self'",
                      "frame-ancestors 'none'", "base-uri 'none'",
                      "form-action 'none'"):
        assert directive in csp
    assert "unsafe-inline" not in csp and "unsafe-eval" not in csp


def test_every_response_is_hardened_and_admin_data_is_never_cached(admin_client):
    for path in ("/dashboard", "/admin/me", "/admin/overview"):
        h = admin_client.get(path).headers
        assert h["x-content-type-options"] == "nosniff", path
        assert h["x-frame-options"] == "DENY", path
        assert h["referrer-policy"] == "no-referrer", path
        assert "no-store" in h["cache-control"], path
    assert "no-store" not in admin_client.get("/healthz").headers.get("cache-control", "")


def test_unknown_dashboard_files_are_404(client):
    assert client.get("/dashboard/secret.txt").status_code == 404
    assert client.get("/dashboard/../models.py").status_code in (404, 400)


def test_me_returns_the_csrf_token_so_a_reloaded_page_can_keep_working(client, admin):
    login = client.post("/admin/login", json={"username": "root",
                                              "password": "correct horse battery"})
    me = client.get("/admin/me").json()
    assert me["csrf_token"] == login.json()["csrf_token"]
    assert me["username"] == "root"


# -- the page itself -----------------------------------------------------------------
def test_the_html_has_no_inline_script_style_or_external_resources():
    html = (DASH / "index.html").read_text(encoding="utf-8")
    assert not re.search(r"<script(?![^>]*\bsrc=)", html)       # only <script src>
    assert "style=" not in html and "<style" not in html
    assert "onclick" not in html.lower() and "javascript:" not in html.lower()
    assert not re.search(r"""(?:src|href)\s*=\s*["']https?://""", html)
    assert 'src="/dashboard/app.js"' in html
    assert 'href="/dashboard/style.css"' in html


# These are search strings for a source scan; nothing here executes them.
@pytest.mark.parametrize("banned", [
    "innerHTML", "outerHTML", "insertAdjacentHTML", "document.write",
    "eval(", "new Function", "setTimeout(\"", "setInterval(\"",
    "localStorage", "sessionStorage"])
def test_the_script_never_turns_data_into_markup_or_stores_the_token(banned):
    js = (DASH / "app.js").read_text(encoding="utf-8")
    assert banned not in js


def test_the_script_only_talks_to_its_own_origin():
    js = (DASH / "app.js").read_text(encoding="utf-8")
    # the SVG namespace is an identifier for the charts' elements, not an address anything is fetched from
    js_without_namespace = js.replace("http://www.w3.org/2000/svg", "")
    assert not re.search(r"""https?://""", js_without_namespace)
    assert "fetch(" in js and "X-CSRF-Token" in js
