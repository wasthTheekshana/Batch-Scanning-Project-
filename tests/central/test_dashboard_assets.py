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
