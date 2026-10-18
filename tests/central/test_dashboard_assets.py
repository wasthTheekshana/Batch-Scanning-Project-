"""The dashboard's static files: the settings-form script is served under the
same locked-down policy, and an auditor is shown no write controls."""
from pathlib import Path

DASHBOARD = Path(__file__).parents[2] / "central" / "dashboard"


def test_the_form_script_is_served_with_the_locked_down_policy(client):
    r = client.get("/dashboard/form.js")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/javascript")
    assert "script-src 'self'" in r.headers["content-security-policy"]
    assert "DokForm" in r.text
    page = client.get("/dashboard").text
    assert page.index("/dashboard/form.js") < page.index("/dashboard/app.js")


def test_other_files_are_still_not_served(client):
    assert client.get("/dashboard/secret.js").status_code == 404
    assert client.get("/dashboard/../app.py").status_code in (404, 422)


def test_the_dashboard_hides_write_controls_from_an_auditor():
    js = (DASHBOARD / "app.js").read_text(encoding="utf-8")
    assert 'role === "auditor" ? null' in js and "(read-only)" in js
    plain = js.replace("wbutton(", "WRITE(")
    for label in ("Add PC", "Delete", "Revoke key", "Assign", "Add admin...", "Save",
                  "Reset password", "Change role", "Disable", "Enable"):
        assert f'button("{label}"' not in plain, label        # never made with the plain helper


def test_the_project_form_exists_and_the_raw_json_is_one_tab_away():
    js = (DASHBOARD / "app.js").read_text(encoding="utf-8")
    assert "projectDialog" in js and "JSON (advanced)" in js and "window.DokForm" in js


# -- the fleet and activity screens and the rules every dashboard file must keep -------------------------
import re  # noqa: E402


def _js() -> str:
    return (DASHBOARD / "app.js").read_text(encoding="utf-8")


def _routes(js: str) -> set[str]:
    block = js[js.index("const routes = {"):js.index("async function render()")]
    return set(re.findall(r"\b([a-z]+)(?=[,:])", block.split("{", 1)[1].split("};")[0]))


def test_every_menu_link_has_a_screen_and_every_screen_a_link():
    html = (DASHBOARD / "index.html").read_text(encoding="utf-8")
    links = set(re.findall(r'data-view="([a-z]+)"', html))
    assert {"overview", "fleet", "activity", "pcs", "projects", "templates", "admins", "audit",
            "settings"} <= links
    assert links <= _routes(_js())


def test_values_from_the_server_are_never_put_in_the_page_as_markup():
    js = _js() + (DASHBOARD / "form.js").read_text(encoding="utf-8")
    for forbidden in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval(", "new Function"):
        assert forbidden not in js, forbidden


def test_nothing_in_the_dashboard_needs_an_inline_style_or_script_or_another_site():
    """The page's content policy is script-src 'self'; style-src 'self': inline styles, inline
    scripts and anything from another site would be blocked (and would be a hole if allowed)."""
    html = (DASHBOARD / "index.html").read_text(encoding="utf-8")
    assert not re.search(r"\sstyle=|\sonclick=|\sonload=|<script(?![^>]*\ssrc=)", html)
    js = _js()
    assert not re.search(r"""setAttribute\(\s*["']style["']""", js)
    assert not re.search(r"""\bstyle\s*:\s*["'{]""", js)               # h("x", { style: ... })
    for name in ("app.js", "form.js", "index.html", "style.css"):
        text = (DASHBOARD / name).read_text(encoding="utf-8")
        # the SVG namespace is an identifier, not an address anything is fetched from
        text = text.replace("http://www.w3.org/2000/svg", "")
        assert not re.search(r"https?://", text), name


def test_the_fleet_and_activity_screens_are_read_only():
    js = _js()
    fleet = js[js.index("async function fleet()"):js.index("async function activity()")]
    activity = js[js.index("async function activity()"):js.index("const routes = {")]
    for source in (fleet, activity):
        assert 'api("POST"' not in source and 'api("PUT"' not in source and 'api("DELETE"' not in source
        assert "wbutton(" not in source and 'api("PATCH"' not in source


def test_the_fleet_view_keeps_itself_current_and_a_stale_render_is_discarded():
    js = _js()
    assert "fleet.refresh = 30000" in js and "clearInterval(refreshTimer)" in js
    assert "myRender !== renderId" in js


def test_the_stylesheet_serves_and_has_a_phone_layout(client):
    r = client.get("/dashboard/style.css")
    assert r.status_code == 200 and "table.stack" in r.text and "max-width: 880px" in r.text
    assert "prefers-color-scheme: dark" in r.text and ":focus-visible" in r.text


def test_the_overview_lists_the_servers_alerts_and_the_menu_shows_how_many():
    js = _js()
    overview = js[js.index("async function overview()"):js.index("function detailText")]
    assert 'api("GET", "/admin/alerts")' in overview and "SEVERITY_TEXT" in js
    assert "updateAlertBadge()" in js and "overview.refresh" in js
    html = (DASHBOARD / "index.html").read_text(encoding="utf-8")
    assert 'id="alert-count"' in html
    assert 'api("POST"' not in overview and "wbutton(" not in overview          # reading only
