"""Sample data for the central admin demo: stations in every state, 14 days of activity, a bank project with two
versions, about 40 documents with index data (some corrected), and one account of each role.

Runs once, on an empty demo database (`python scripts/central_demo.py`). Everything goes through the real API
or the real tables, so the dashboard shows exactly what a live installation would. The names and numbers are made up."""
from __future__ import annotations

import hashlib
import random
import uuid
from datetime import timedelta

from fastapi.testclient import TestClient

ADMIN = ("admin", "demo-admin-passphrase")
SEARCHER = ("finder", "demo-searcher-passphrase")
AUDITOR = ("watcher", "demo-auditor-passphrase")

NAMES = ["Kamal Perera", "Nimali Fernando", "Sunil Jayasuriya", "Dilani Wickramasinghe", "Ruwan Silva",
         "Chamari Gunawardena", "Priya Rajendran", "Mohamed Rizwan", "Anjali De Silva", "Tharindu Bandara",
         "Kavindi Senanayake", "Sivakumar Nadarajah", "Ishara Weerasinghe", "Nuwan Pathirana", "Hasini Amarasinghe"]
BRANCHES = {"Colombo Fort": ["Colombo Fort", "Maradana", "Pettah"], "Galle desk": ["Galle", "Matara", "Hikkaduwa"],
            "Kandy desk": ["Kandy", "Peradeniya", "Katugastota"]}
STATUSES = ["approved"] * 7 + ["pending_qc", "rejected"]


def seed(app) -> bool:
    """Fill an empty database. Returns False (and does nothing) when there is already data."""
    from sqlalchemy import select

    from central.manage import create_admin
    from central.models import AdminUser, Pc, PcDailyStat, utcnow

    factory = app.state.session_factory
    with factory() as db:
        if db.scalar(select(Pc.id).limit(1)) is not None:
            return False
        if db.scalar(select(AdminUser).where(AdminUser.username == ADMIN[0])) is None:
            create_admin(factory, *ADMIN)
    create_admin(factory, *AUDITOR, role="auditor")
    create_admin(factory, *SEARCHER, role="searcher")

    rnd = random.Random(7)                               # the same demo every time
    admin = TestClient(app)
    token = admin.post("/admin/login", json={"username": ADMIN[0], "password": ADMIN[1]}).json()["csrf_token"]
    admin.headers["X-CSRF-Token"] = token

    # one project, two published versions
    project_id = str(uuid.uuid4())
    config = admin.get("/admin/project-defaults").json()
    admin.put(f"/admin/projects/{project_id}", json={"name": "Bank customer file", "config": config})
    config["qc"]["pass_score"] = 85
    second = admin.put(f"/admin/projects/{project_id}", json={"name": "Bank customer file", "config": config})
    assert second.json()["version"] == 2, second.text

    # stations: two healthy, one that stopped, one never enrolled
    stations = {}
    for name in ("Colombo Fort", "Galle desk", "Kandy desk", "Jaffna desk"):
        made = admin.post("/admin/pcs", json={"display_name": name}).json()
        stations[name] = {"id": made["pc_id"], "code": made["enrollment_code"]}
    for name in ("Colombo Fort", "Galle desk", "Kandy desk"):
        reg = TestClient(app).post("/pcs/register", json={"code": stations[name]["code"]}).json()
        stations[name]["key"] = reg["api_key"]
    admin.post(f"/admin/projects/{project_id}/push", json={"all_pcs": True})

    today = utcnow().date()
    applied = {"Colombo Fort": 2, "Galle desk": 1, "Kandy desk": 2}          # Galle has not applied version 2
    versions = {"Colombo Fort": "1.0.0", "Galle desk": "1.0.0", "Kandy desk": "0.9.4"}
    for name, version in applied.items():
        client = TestClient(app)
        day_total = {"Colombo Fort": 46, "Galle desk": 28, "Kandy desk": 15}[name]
        client.post(f"/pcs/{stations[name]['id']}/heartbeat", headers={"Authorization": f"Bearer {stations[name]['key']}"},
                    json={"app_version": versions[name], "applied": {project_id: version},
                          "stats": {"day": today.isoformat(), "documents": day_total, "pages": day_total * 4,
                                    "batches": 5, "errors": 6 if name == "Galle desk" else 0}})
    with factory() as db:
        for name, base in (("Colombo Fort", 55), ("Galle desk", 32), ("Kandy desk", 20)):
            for back in range(1, 14):
                if (today - timedelta(days=back)).weekday() >= 5:
                    continue                                                # quiet weekends
                docs = max(0, int(rnd.gauss(base, base * 0.25)))
                db.add(PcDailyStat(pc_id=stations[name]["id"], day=(today - timedelta(days=back)).isoformat(),
                                   documents=docs, pages=docs * 4, batches=max(1, docs // 10),
                                   errors=rnd.choice([0, 0, 0, 1, 2])))
        db.commit()

    # documents, as the stations would report them
    count = 0
    for name, towns in BRANCHES.items():
        pc = TestClient(app)
        pc.headers["Authorization"] = f"Bearer {stations[name]['key']}"
        for i in range(14 if name != "Kandy desk" else 8):
            count += 1
            who = rnd.choice(NAMES)
            nic = f"{rnd.randint(70, 99)}{rnd.randint(1000000, 9999999)}V"
            doc_id = f"demo-{count:03d}"
            created = utcnow() - timedelta(days=rnd.randint(0, 9), hours=rnd.randint(0, 8))
            pdf = f"{who.split()[0].lower()}-{nic}.pdf"
            base = f"/pcs/{stations[name]['id']}/documents/{doc_id}"
            pc.put(base + "/meta", json={
                "project_id": project_id, "batch_id": f"B-{created:%Y%m%d}-{rnd.randint(1, 4)}", "page_count": rnd.randint(2, 6),
                "created_at": created.isoformat() + "Z", "validation_status": rnd.choice(STATUSES), "file_name": pdf,
                "size": rnd.randint(150_000, 900_000), "sha256": hashlib.sha256(doc_id.encode()).hexdigest(),
                "delivered_to": ["local_folder", "kept on the scanning PC"], "delivery_status": "delivered_local"})
            fields = {"full_name": who, "nic": nic, "account_number": str(rnd.randint(10 ** 9, 10 ** 10 - 1)),
                      "mobile": f"07{rnd.randint(0, 8)}{rnd.randint(1000000, 9999999)}",
                      "email": f"{who.split()[0].lower()}{rnd.randint(1, 99)}@example.lk", "branch": rnd.choice(towns)}
            status = rnd.choice(STATUSES)
            pc.put(base + "/index", json={"fields": fields, "validation_status": status, "project_version": 2})
            if i % 5 == 3:                                   # an operator corrected the name on the PC
                fields["full_name"] = who + " A"
                fields["email"] = fields["email"].replace("example.lk", "gmail.com")
                pc.put(base + "/index", json={"fields": fields, "validation_status": "approved", "project_version": 2})
    from central.models import ConfigVersion
    with factory() as db:                                    # version 2 went out two days ago; Kandy has since gone quiet
        for row in db.scalars(select(ConfigVersion).where(ConfigVersion.owner_id == project_id)):
            row.created_at = utcnow() - timedelta(days=3 if row.version == 1 else 2)
        db.get(Pc, stations["Kandy desk"]["id"]).last_seen_at = utcnow() - timedelta(hours=30)
        db.get(Pc, stations["Galle desk"]["id"]).last_seen_at = utcnow() - timedelta(minutes=2)
        db.commit()
    # give the searcher their project
    users = admin.get("/admin/users").json()
    searcher_id = next(u["id"] for u in users if u["username"] == SEARCHER[0])
    admin.put(f"/admin/users/{searcher_id}/projects", json={"project_ids": [project_id]})
    return True
