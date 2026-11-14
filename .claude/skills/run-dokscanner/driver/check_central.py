"""End-to-end check of the multi-PC central sync, against the REAL stack.

    python .claude/skills/run-dokscanner/driver/check_central.py [--out DIR]

1. starts the real central server (uvicorn, throw-away sqlite DB, fresh admin)
2. drives the admin API like the future dashboard (create PC, publish, push)
3. runs the genuine DOKScanner main() headless and clicks through the Admin >
   Central tab and the Workflow Designer: enrol, sync, local edit of a managed
   project, central edit, key revoked, re-enrol, disconnect - screenshotting each
Exit code 0 = every check passed. Screenshots: <out>/shots/*.png
"""
import argparse
import copy
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import _harness as h  # noqa: E402
from admin_api import Admin  # noqa: E402

PID = "aaaaaaaa-1111-2222-3333-444444444444"
ADMIN_PW = "correct horse battery"


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def start_server(out: Path, port: int):
    cdir = out / "central"
    cdir.mkdir(parents=True, exist_ok=True)
    db = cdir / "central.db"
    db.unlink(missing_ok=True)
    env = dict(os.environ,
               CENTRAL_DATABASE_URL=f"sqlite:///{db.as_posix()}",
               CENTRAL_SESSION_SECRET="run-check-secret-0123456789-abcdefghijklmnop",
               CENTRAL_COOKIE_SECURE="0")           # plain http on loopback
    subprocess.run([sys.executable, "-c",
                    "from central.db import make_engine, make_session_factory;"
                    "from central.manage import create_admin;"
                    "from central.settings import Settings;"
                    "s = Settings.from_env();"
                    f"create_admin(make_session_factory(make_engine(s.database_url)), 'root', '{ADMIN_PW}')"],
                   env=env, cwd=h.REPO, check=True)
    log = open(cdir / "server.log", "w")
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "central.app:create_app_from_env", "--factory",
         "--host", "127.0.0.1", "--port", str(port), "--log-level", "warning"],
        env=env, cwd=h.REPO, stdout=log, stderr=subprocess.STDOUT)
    base = f"http://127.0.0.1:{port}"
    for _ in range(100):
        try:
            with urllib.request.urlopen(base + "/healthz", timeout=1) as r:
                if r.status == 200:
                    return proc, base
        except OSError:
            time.sleep(0.2)
    proc.kill()
    raise SystemExit(f"central server did not start; see {cdir / 'server.log'}")


def central_project_config(tmp: Path) -> dict:
    from projects.profile_manager import ProfileManager
    return ProfileManager(tmp / "pm").create("Seed")


def make_page_png(path: Path, variant: int = 0) -> None:
    """A real one-page scan: a white page with a heading and rows of dark text bars
    (dense enough that the default blank-page separation keeps it as a document)."""
    import cv2
    import numpy as np
    page = np.full((1000, 800), 255, np.uint8)
    for i in range(12):        # `variant` changes the layout: the duplicate check must not fire
        cv2.rectangle(page, (100 + variant * 30 * (i % 3), 100 + i * 60),
                      (700 - variant * 40 * (i % 2), 118 + i * 60), 0, -1)
    cv2.putText(page, f"INVOICE INV-123{variant}", (100, 60), cv2.FONT_HERSHEY_SIMPLEX, 1.2, 0, 3)
    cv2.imwrite(str(path), page)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=h.DEFAULT_OUT)
    args = ap.parse_args()
    out, shots = args.out, args.out / "shots"
    appdir = h.setup_env(out, allow_loopback_http=True)   # sandboxed app data, https switch
    report = h.Report()
    port = free_port()
    proc, base = start_server(out, port)
    print(f"central server up at {base}", flush=True)
    try:
        # ---- server side, as a dashboard would ------------------------------------
        print("\n== central admin API", flush=True)
        admin = Admin(base)
        report.check("wrong password -> 401", admin.login(password="nope")[0] == 401)
        report.check("login -> 200", admin.login(password=ADMIN_PW)[0] == 200)
        report.check("POST without CSRF -> 403",
                     admin.call("POST", "/admin/pcs", {"display_name": "X"}, csrf=False)[0] == 403)
        st, pc = admin.call("POST", "/admin/pcs", {"display_name": "Counter-1"})
        report.check("create PC -> 201 + one-time code", st == 201 and len(pc["enrollment_code"]) >= 16)
        base_cfg = central_project_config(Path(tempfile.mkdtemp()))
        cfg = copy.deepcopy(base_cfg)
        cfg["ocr"]["lang"] = "eng"
        report.check("publish project v1 -> 200",
                     admin.call("PUT", f"/admin/projects/{PID}", {"name": "Invoices", "config": cfg})[0] == 200)
        leaky = copy.deepcopy(base_cfg)
        leaky["delivery"]["sftp"] = {"enabled": True, "host": "h", "password": "hunter2"}
        st, body = admin.call("PUT", "/admin/projects/bbbbbbbb-1111-2222-3333-444444444444",
                              {"name": "Leaky", "config": leaky})
        report.check("config with a password is refused (422)", st == 422, str(body.get("detail", ""))[:70])
        report.check("push to the PC -> 200",
                     admin.call("POST", f"/admin/projects/{PID}/push",
                                {"pc_ids": [pc["pc_id"]]})[0] == 200)

        def stored():
            return json.loads((appdir / "projects" / f"{PID}.json").read_text(encoding="utf-8"))

        local_delivery = str(appdir / "delivered_bank")     # PC-local, inside the sandbox

        def scenario(app, win):
            from PyQt6.QtGui import QFontMetrics
            from PyQt6.QtWidgets import QLabel, QMessageBox, QTabWidget
            tab = win.admin_panel.central_tab
            state = tab.state
            inner = win.admin_panel.findChild(QTabWidget)
            QMessageBox.information = staticmethod(lambda *a, **k: None)
            QMessageBox.warning = staticmethod(lambda *a, **k: None)
            bar = lambda: [w.text() for w in win.statusBar().findChildren(QLabel)  # noqa: E731
                           if w.text() and not w.isHidden()]

            print("\n== app: Admin > Central, not registered", flush=True)
            win.tabs.setCurrentWidget(win.admin_panel)
            inner.setCurrentWidget(tab)
            report.check("screenshot", h.shot(app, win, shots / "01-central-unregistered.png"))
            report.check("says not registered", "not registered" in tab.status_label.text().lower())
            report.check("no central notices in the status bar", not any("enrollment" in t or "restart" in t for t in bar()))

            print("\n== app: register with the one-time code", flush=True)
            tab.url_edit.setText(base)
            tab.code_edit.setText(pc["enrollment_code"])
            tab.register_btn.click()
            report.check("Register button disabled while working", not tab.register_btn.isEnabled())
            report.check("registered", h.wait_for(app, state.is_registered))
            report.check("PC id matches the server", state.settings.pc_id == pc["pc_id"])
            report.check("project arrives via the first sync",
                         h.wait_for(app, lambda: tab.projects_table.rowCount() >= 1, 25))
            h.pump(app, 0.5)
            report.check("screenshot", h.shot(app, win, shots / "02-registered-and-synced.png"))
            hdr = tab.projects_table.horizontalHeader()
            fm = QFontMetrics(hdr.font())
            cut = [tab.projects_table.horizontalHeaderItem(i).text()
                   for i in range(tab.projects_table.columnCount())
                   if hdr.sectionSize(i) < fm.horizontalAdvance(
                       tab.projects_table.horizontalHeaderItem(i).text())]
            report.check("Central table headers are fully visible", not cut, str(cut))
            report.check("API key is not in central.json",
                         "dsk_" not in (appdir / "central.json").read_text())
            central_text = " ".join(w.text() for w in tab.findChildren(QLabel)
                                    if not w.isHidden())
            report.check("Central tab says projects apply to the next scan batch",
                         "apply to the next scan batch" in central_text, central_text[:120])
            report.check("status bar shows NO restart notice",
                         not any("restart" in t.lower() for t in bar()), str(bar()))
            cfg_local = stored()
            report.check("central content applied", cfg_local["name"] == "Invoices" and cfg_local["ocr"]["lang"] == "eng")
            report.check("local sections seeded under this PC's own data dir",
                         str(appdir).replace("\\", "/") in cfg_local["export"]["output_path"].replace("\\", "/"))

            print("\n== app: Workflow Designer on the managed project", flush=True)
            win.tabs.setCurrentWidget(win.designer)
            d = win.designer
            listed = any(d.project_combo.itemData(i) == PID for i in range(d.project_combo.count()))
            report.check("Designer lists the synced project without a manual refresh", listed)
            idx = next((i for i in range(d.project_combo.count())
                        if d.project_combo.itemData(i) == PID), None)
            report.check("synced project selectable in the Designer", idx is not None)
            if idx is not None:
                d.project_combo.setCurrentIndex(idx)
                h.pump(app, 0.4)
                report.check("screenshot", h.shot(app, win, shots / "03-designer-managed.png"))
                report.check("Save button says 'Save local settings' for the managed project",
                             d.save_btn.text() == "Save local settings", d.save_btn.text())
                report.check("central-owned QC editor is disabled", not d.qc_enabled.isEnabled())
                report.check("local delivery folder is editable", d.delivery_path.isEnabled())
                report.check("SFTP 'Enabled' is editable", d.sftp_enabled.isEnabled())
                before = stored()
                d.delivery_path.setText(local_delivery)
                d.sftp_enabled.setChecked(True)
                d.sftp_host.setText("sftp.bank.local")
                d.qc_enabled.setChecked(not d.qc_enabled.isChecked())      # sneak a central-owned edit
                d.save()
                after = stored()
                report.check("local delivery path saved", after["delivery"]["local_folder"]["path"] == local_delivery)
                report.check("SFTP enabled saved", after["delivery"]["sftp"]["enabled"] is True)
                report.check("central-owned qc NOT changed", after["qc"] == before["qc"])
                report.check("central-owned ocr NOT changed", after["ocr"] == before["ocr"])

            print("\n== app: a scan batch for the managed project (the REAL pipeline)", flush=True)
            from scanning.file_import import import_files

            def scan_batch(batch_id: str, variant: int) -> list:
                png = out / f"{batch_id}.png"
                make_page_png(png, variant)
                jobs = import_files([png], batch_id, PID, out / "wizard-staging")
                win.start_batch(batch_id, jobs)
                h.wait_for(app, lambda: any(
                    r["delivery_status"] == "delivered_local"
                    for r in win.db.list_documents(batch_id)), 60)
                return win.db.list_documents(batch_id)

            docs1 = scan_batch("e2e-batch-1", 0)
            delivered_dir = Path(local_delivery)
            report.check("batch 1: the document was delivered", len(docs1) == 1
                         and docs1[0]["delivery_status"] == "delivered_local",
                         str([dict(r) for r in docs1])[:150])
            report.check("batch 1: file is in the managed project's PC-local delivery folder",
                         delivered_dir.is_dir() and any(delivered_dir.iterdir()), str(delivered_dir))
            report.check("batch 1: batch row carries the managed project's id",
                         win.db.get_batch("e2e-batch-1")["project_id"] == PID)
            report.check("batch 1: document row carries the managed project's id",
                         bool(docs1) and docs1[0]["project_id"] == PID)

            print("\n== central edit reaches the PC, local settings survive", flush=True)
            srv = admin.call("GET", f"/admin/projects/{PID}")[1]["config"]
            srv["ocr"]["lang"] = "deu"
            st, body = admin.call("PUT", f"/admin/projects/{PID}", {"name": "Invoices", "config": srv})
            report.check("central accepted v2", st == 200 and body["version"] == 2)
            tab.worker.sync_now()
            report.check("new version applied locally",
                         h.wait_for(app, lambda: stored()["ocr"]["lang"] == "deu", 25))
            now = stored()
            report.check("PC-local delivery survived the update",
                         now["delivery"]["local_folder"]["path"] == local_delivery
                         and now["delivery"]["sftp"]["enabled"] is True)
            report.check("next batch will use the new version (resolver sees lang=deu, no restart)",
                         win.pipeline.resolver.resolve(PID).profile["ocr"]["lang"] == "deu")
            n_before = len(list(delivered_dir.iterdir()))
            docs2 = scan_batch("e2e-batch-2", 3)
            report.check("batch 2 after the central edit is delivered without a restart",
                         len(docs2) == 1 and docs2[0]["delivery_status"] == "delivered_local"
                         and len(list(delivered_dir.iterdir())) == n_before + 1,
                         str([dict(r) for r in docs2])[:150])
            report.check("batch 2 is filed under the managed project",
                         win.db.get_batch("e2e-batch-2")["project_id"] == PID)

            print("\n== admin revokes the PC's key", flush=True)
            report.check("revoked on the server", admin.call("DELETE", f"/admin/pcs/{pc['pc_id']}/key")[0] == 204)
            tab.worker.sync_now()
            report.check("client notices (auth_failed persisted)",
                         h.wait_for(app, lambda: state.settings.auth_failed, 25))
            win.tabs.setCurrentWidget(win.admin_panel)
            inner.setCurrentWidget(tab)
            h.pump(app, 0.5)
            report.check("screenshot", h.shot(app, win, shots / "04-key-revoked.png"))
            report.check("Central tab asks for a new code", "enrollment code" in tab.status_label.text().lower())
            report.check("status bar tells every role", any("enrollment" in t.lower() for t in bar()), str(bar()))
            report.check("Sync now is disabled (polling stopped)", not tab.sync_btn.isEnabled())

            print("\n== re-enrol with a fresh code", flush=True)
            st, body = admin.call("POST", f"/admin/pcs/{pc['pc_id']}/enrollment-code")
            tab.url_edit.setText(base)
            tab.code_edit.setText(body["enrollment_code"])
            tab.register_btn.click()
            report.check("service restored",
                         h.wait_for(app, lambda: state.is_registered() and not state.settings.auth_failed, 25))
            h.pump(app, 1.0)
            report.check("screenshot", h.shot(app, win, shots / "05-reenrolled.png"))

            print("\n== disconnect keeps the projects", flush=True)
            QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes)
            tab.disconnect_btn.click()
            h.pump(app, 0.5)
            report.check("screenshot", h.shot(app, win, shots / "06-disconnected.png"))
            report.check("unregistered again", not state.is_registered())
            report.check("project file still on disk", (appdir / "projects" / f"{PID}.json").exists())
            return report.summary()

        code = h.run_main(scenario)
        print(f"main() returned {code} (clean shutdown)")
        return code
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()


if __name__ == "__main__":
    sys.exit(main())
