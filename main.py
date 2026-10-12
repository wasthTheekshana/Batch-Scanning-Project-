"""DOK Scanner entry point (HLD §16).

Initialises the data directory, database, audit logging and pipeline;
shows the login screen first (RBAC from day one), then the main window.
Job recovery runs on every startup (HLD §14.2).
"""
from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

from PyQt6.QtWidgets import QApplication

from core.central_sync import (build_central, choose_pipeline_profile,
                               shutdown_central, startup_sync)
from core.event_bus import EventBus
from core.job_queue import Pipeline
from core.profile_manager import ProfileManager
from core.project_runtime import RuntimeResolver
from core.rbac import Session
from core.template_manager import TemplateManager
from data.audit import AuditLogger
from data.backup import BackupService
from data.db import Database, is_encrypted, migrate_to_encrypted
from data.keystore import KeyStore
from delivery.registry import active_channels
from export.batch_exporter import export_document
from ocr.tesseract_engine import is_available as tesseract_available, ocr_page
from ui.login_dialog import LoginDialog
from ui.main_window import MainWindow

APP_NAME = "DOKScanner"


def data_dir() -> Path:
    root = Path(os.environ.get("LOCALAPPDATA", Path.home())) / APP_NAME
    root.mkdir(parents=True, exist_ok=True)
    return root


def open_database(root: Path, key: bytes) -> Database:
    """Open the encrypted database, migrating a Phase 1 plaintext DB
    in place on first Phase 2 launch (HLD §10.2: encryption at creation;
    migration path documented for existing installs)."""
    db_path = root / "dokscanner.db"
    if db_path.exists() and not is_encrypted(db_path):
        logging.info("migrating plaintext database to SQLCipher…")
        enc_path = root / "dokscanner.enc.db"
        if enc_path.exists():
            enc_path.unlink()
        migrate_to_encrypted(db_path, enc_path, key)
        backup_plain = root / "dokscanner.pre-encryption.bak"
        db_path.replace(backup_plain)
        enc_path.replace(db_path)
        logging.info("migration complete; plaintext backup at %s",
                     backup_plain)
    return Database(db_path, key=key)


def build_default_project(pm: ProfileManager, db: Database,
                          root: Path) -> dict:
    """First run: create a general-purpose project so the app is usable
    immediately (HLD §20: start with a general profile, refine later)."""
    projects = db.list_projects("active")
    if projects:
        import json
        return json.loads(projects[0]["config_json"])
    cfg = pm.create("General Documents")
    cfg["export"]["output_path"] = str(root / "output")
    cfg["delivery"]["local_folder"]["path"] = str(root / "delivered")
    return pm.save(cfg)


def main() -> int:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(name)s %(levelname)s %(message)s")
    root = data_dir()
    key = KeyStore(root / "keys" / "master.bin").get_or_create()
    db = open_database(root, key)
    audit = AuditLogger(db, root / "logs")
    pm = ProfileManager(root / "projects", db)
    try:
        TemplateManager(db).seed_built_ins()
    except Exception:
        logging.exception("failed to seed built-in templates; continuing "
                          "without them")
    backup_service = BackupService(
        db, sources=[root / "projects", root / "delivered"],
        dest_dir=root / "backups", master_key=key, interval_hours=24)
    backup_service.start()

    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    from ui.theme import apply_theme
    apply_theme(app)

    login = LoginDialog(db)
    if login.exec() != login.DialogCode.Accepted:
        return 0

    bus = EventBus()
    central_state, sync_service, sync_worker = build_central(
        db, pm, root, audit, bus,
        operator_fn=lambda: (Session.current_user().username
                             if Session.current_user() else ""))
    startup_result = (startup_sync(sync_service, central_state)
                      if sync_service is not None else None)
    profile = choose_pipeline_profile(
        db, fallback=lambda: build_default_project(pm, db, root))
    ocr_fn = ocr_page if tesseract_available() else None
    if ocr_fn is None:
        logging.warning("Tesseract not found — OCR disabled this session")

    channels = active_channels(profile.get("delivery", {}))
    cddvd_channel = next((c for c in channels if c.name == "cd_dvd"), None)
    pipeline = Pipeline(
        db, profile,
        channels=channels,
        export_fn=export_document,
        audit=audit,
        ocr_fn=ocr_fn,
        working_dir=root / "staging",
        resolver=RuntimeResolver(db))
    pipeline.subscribe(bus.publish_pipeline_event)
    pipeline.start()
    pipeline.recover()
    if sync_worker is not None:     # the startup sync already ran above
        sync_worker.start(initial_sync=False, first_result=startup_result)

    window = MainWindow(db, pm, bus, pipeline=pipeline, audit=audit,
                        staging_dir=root / "staging",
                        backup_service=backup_service,
                        cddvd_channel=cddvd_channel,
                        central_state=central_state,
                        sync_service=sync_service,
                        sync_worker=sync_worker)
    window.show()
    window.report_refused_batches(pipeline.refused_batches)
    code = app.exec()

    shutdown_central(sync_worker)
    backup_service.stop()
    pipeline.shutdown(wait=False)
    Session.logout(db)
    audit.close()
    db.close()
    return code


if __name__ == "__main__":
    sys.exit(main())
