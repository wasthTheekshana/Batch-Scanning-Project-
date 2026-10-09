import copy
import uuid

import pytest
from fastapi.testclient import TestClient

from central.app import create_app_from_env
from central.models import AuditEvent


def test_register_push_pull_update_revoke_roundtrip(admin_client, project_config, app):
    # admin enrols a PC; the PC exchanges the one-time code for its key
    pc = admin_client.post("/admin/pcs", json={"display_name": "Counter-1"}).json()
    pc_client = TestClient(app)
    key = pc_client.post("/pcs/register",
                         json={"code": pc["enrollment_code"]}).json()["api_key"]
    headers = {"Authorization": f"Bearer {key}", "X-Operator-Id": "alice"}
    url = f"/pcs/{pc['pc_id']}/config"
    assert pc_client.get(url, headers=headers).json()["projects"] == []

    # admin publishes and pushes a project; the PC sees v1
    p = str(uuid.uuid4())
    admin_client.put(f"/admin/projects/{p}",
                     json={"name": "Invoices", "config": project_config})
    admin_client.post(f"/admin/projects/{p}/push", json={"all_pcs": True})
    got = pc_client.get(url, headers=headers).json()["projects"][0]
    assert got["version"] == 1 and got["config"]["name"] == "Invoices"

    # admin edits; the PC sees v2; an identical re-save changes nothing
    edited = copy.deepcopy(project_config)
    edited["qc"]["pass_score"] = 90
    admin_client.put(f"/admin/projects/{p}", json={"name": "Invoices", "config": edited})
    admin_client.put(f"/admin/projects/{p}", json={"name": "Invoices", "config": edited})
    got = pc_client.get(url, headers=headers).json()["projects"][0]
    assert got["version"] == 2 and got["config"]["qc"]["pass_score"] == 90

    # admin revokes the key; the PC is locked out
    admin_client.delete(f"/admin/pcs/{pc['pc_id']}/key")
    assert pc_client.get(url, headers=headers).status_code == 401

    # the whole flow is on the audit trail
    with app.state.session_factory() as db:
        actions = {r.action for r in db.query(AuditEvent)}
    assert {"pc_created", "pc_registered", "project_saved", "project_pushed",
            "config_pull", "pc_key_revoked"} <= actions


def test_create_app_from_env_serves_healthz(monkeypatch, tmp_path):
    monkeypatch.setenv("CENTRAL_DATABASE_URL", f"sqlite:///{tmp_path / 'env.db'}")
    monkeypatch.setenv("CENTRAL_SESSION_SECRET", "e" * 40)
    with TestClient(create_app_from_env()) as c:
        assert c.get("/healthz").json() == {"status": "ok"}


@pytest.mark.parametrize("secret", [None, "short"])
def test_create_app_from_env_rejects_missing_or_short_secret(monkeypatch, tmp_path, secret):
    monkeypatch.setenv("CENTRAL_DATABASE_URL", f"sqlite:///{tmp_path / 'env.db'}")
    if secret is None:
        monkeypatch.delenv("CENTRAL_SESSION_SECRET", raising=False)
    else:
        monkeypatch.setenv("CENTRAL_SESSION_SECRET", secret)
    with pytest.raises(RuntimeError):
        create_app_from_env()
