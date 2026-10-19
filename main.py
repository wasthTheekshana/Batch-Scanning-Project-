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
                               shutdown_central, start_document_sync, startup_sync)
from core.event_bus import EventBus
from core.hot_folder import HotFolderService
from core.job_queue import Pipeline
from core.profile_manager import ProfileManager
from core.project_runtime import RuntimeResolver
from core.project_store import load_config
from core.rbac import Session
from core.template_manager import TemplateManager
from data.audit import AuditLogger
from data.backup import BackupService
from data.db import Database, is_encrypted, migrate_to_encrypted
from data.keystore import KeyStore
from delivery.registry import active_channels
from export.batch_exporter import export_document
from ocr.tesseract_engine import is_available as tesseract_available, ocr_page
from ui.login_dialog import LoginDialog, require_password_change
from ui.main_window import MainWindow

APP_NAME = "DOKScanner"


def selftest() -> int:
    """`DOKScanner.exe --selftest`: import every module the app loads lazily
    and use the libraries that need native parts, then write what happened to
    selftest.txt in the data folder. A packaged build can fail only when a
    button is clicked; this finds that without clicking."""
    import tempfile
    root = data_dir()
    lines, failed = [], False

    def check(name, work):
        nonlocal failed
        try:
            lines.append(f"ok    {name}: {work() or ''}".rstrip())
        except Exception as e:                          # report, never raise
            failed = True
            lines.append(f"FAIL  {name}: {type(e).__name__}: {e}")

    def modules():
        import importlib
        for name in ("ui.cover_sheet_dialog", "ui.document_type_dialog",
                     "ui.zone_dialog", "ui.workflow_designer", "ui.custody_panel",
                     "ui.reports_panel", "ui.admin_panel", "ui.central_tab",
                     "scanning.cover_sheets", "scanning.wia_source",
                     "ocr.comb", "ocr.doc_types", "separation.barcode"):
            importlib.import_module(name)
        return "all imported"

    def cover_sheet():
        from scanning.cover_sheets import write_cover_sheets
        out = Path(tempfile.mkdtemp()) / "t.pdf"
        write_cover_sheets(["CIF-1"], out)
        return f"{out.stat().st_size} bytes"

    def barcode_roundtrip():
        import cv2
        import fitz
        import numpy as np
        from scanning.cover_sheets import write_cover_sheets
        from separation.barcode import decode_barcodes
        out = Path(tempfile.mkdtemp()) / "t.pdf"
        write_cover_sheets(["CIF-100234"], out)
        with fitz.open(str(out)) as pdf:
            pix = pdf[0].get_pixmap(dpi=200, colorspace=fitz.csGRAY)
        page = np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.width)
        return str([b["value"] for b in decode_barcodes(page)])

    def ocr():
        import numpy as np
        from ocr.tesseract_engine import is_available, ocr_page
        if not is_available():
            raise RuntimeError("Tesseract not found")
        ocr_page(np.full((60, 200), 255, np.uint8))
        return "tesseract runs"

    def app_version():
        from version import __version__
        return __version__

    def digit_model():
        import numpy as np
        from ocr import digits
        if not digits.available():
            raise RuntimeError("digit model file not found")
        glyph = np.zeros((60, 40), np.uint8)
        glyph[5:55, 17:23] = 255                      # a plain vertical stroke
        if not digits.cnn_available():
            raise RuntimeError("the convolutional digit network (digit_cnn.onnx) did not load")
        return "reads " + digits.read_digit(glyph)[0] + " (convolutional network + older network)"

    def handwriting_model():
        import numpy as np
        from ocr import handwriting
        if not handwriting.available():
            return "model files not installed: handwriting is read the older, weaker way"
        line = np.full((70, 360, 3), 255, np.uint8)
        import cv2
        cv2.putText(line, "Jaffna", (20, 52), cv2.FONT_HERSHEY_SIMPLEX, 1.6, (0, 0, 0), 3)
        text, confidence = handwriting.read_line(line, tries=False)
        return f"reads {text!r} ({confidence:.2f})"

    def sftp_library():
        import paramiko
        return "paramiko " + paramiko.__version__

    def searchable_pdf():
        """Write a PDF whose text layer sits on a word, then find the word."""
        import cv2
        import fitz
        import numpy as np
        from export.pdf_writer import write_searchable_pdf
        tmp = Path(tempfile.mkdtemp())
        cv2.imwrite(str(tmp / "p.png"), np.full((300, 600), 255, np.uint8))
        word = {"text": "Account", "left": 60, "top": 60, "width": 240,
                "height": 40}
        out = write_searchable_pdf([tmp / "p.png"], ["Account"], tmp / "t.pdf",
                                   words=[[word]])
        with fitz.open(str(out)) as pdf:
            if not pdf[0].search_for("Account"):
                raise RuntimeError("text layer not searchable")
        return "text layer found"

    def passwords():
        from core.rbac import hash_password, verify_password
        if not verify_password("a-long-password", hash_password("a-long-password")):
            raise RuntimeError("bcrypt check failed")
        return "bcrypt works"

    def scanner():
        from scanning.sources import available_sources
        return str(available_sources())

    def twain_driver():
        """The TWAIN library loads inside the packaged app and, when the scanner
        is on, its driver opens (no paper used)."""
        from scanning.sources import available_sources, preferred_choice
        from scanning.twain_source import TWAINSource
        import twain                                       # the bundled library
        found = available_sources()
        choice = preferred_choice(found)
        if choice is None:
            return f"library loads; {len(found.get('twain', []))} source(s), none matched to a scanner"
        source = TWAINSource()
        try:
            source.connect(choice[1])
        except Exception as e:                             # scanner off: not a build fault
            return f"library loads; {choice[1]} listed but not opened ({e})"
        source.disconnect()
        return f"{choice[1]} opens"

    def dialog():
        from PyQt6.QtWidgets import QApplication
        app = QApplication.instance() or QApplication([])
        from ui.cover_sheet_dialog import CoverSheetDialog
        dlg = CoverSheetDialog()
        dlg.ids.setPlainText("A-1" + chr(10) + "A-2")
        return dlg.summary.text()

    for name, work in (("version", app_version), ("modules", modules), ("cover sheet PDF", cover_sheet),
                       ("barcode round trip", barcode_roundtrip),
                       ("OCR engine", ocr), ("digit model", digit_model),
                       ("handwriting model", handwriting_model),
                       ("scanners", scanner), ("TWAIN driver", twain_driver),
                       ("SFTP library", sftp_library),
                       ("searchable PDF", searchable_pdf),
                       ("passwords", passwords),
                       ("cover sheet dialog", dialog)):
        check(name, work)
    text = chr(10).join(lines) + chr(10)
    (root / "selftest.txt").write_text(text, encoding="utf-8")
    print(text)
    return 1 if failed else 0


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
    for row in db.list_projects("active"):
        existing = load_config(row)
        if existing is not None:      # a corrupt row is skipped, not fatal
            return existing
    cfg = pm.create("General Documents")
    cfg["export"]["output_path"] = str(root / "output")
    cfg["delivery"]["local_folder"]["path"] = str(root / "delivered")
    return pm.save(cfg)


def main() -> int:
    if "--selftest" in sys.argv:
        return selftest()
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(name)s %(levelname)s %(message)s")
    root = data_dir()
    key = KeyStore(root / "keys" / "master.bin").get_or_create()
    db = open_database(root, key)
    audit = AuditLogger(db, root / "logs")
    pm = ProfileManager(root / "projects", db, data_root=root)
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

    from PyQt6.QtWidgets import QMessageBox
    from core.crash_guard import install as install_crash_guard
    install_crash_guard(root / "logs", show=lambda title, text: QMessageBox.critical(
        None, title, text))

    login = LoginDialog(db)
    if login.exec() != login.DialogCode.Accepted:
        return 0
    if not require_password_change(db):
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
    start_document_sync(sync_service)

    window = MainWindow(db, pm, bus, pipeline=pipeline, audit=audit,
                        staging_dir=root / "staging",
                        backup_service=backup_service,
                        cddvd_channel=cddvd_channel,
                        central_state=central_state,
                        sync_service=sync_service,
                        sync_worker=sync_worker)
    window.restore_geometry_or_default()
    window.show()
    window.start_idle_lock(app)
    window.report_refused_batches(pipeline.refused_batches)
    window.report_unreadable_projects()
    hot_folders = HotFolderService(db, pipeline, root / "staging", audit=audit)
    hot_folders.start()
    code = app.exec()

    shutdown_central(sync_worker, sync_service)
    backup_service.stop()
    hot_folders.stop()
    pipeline.shutdown(wait=False)
    Session.logout(db)
    audit.close()
    db.close()
    return code


if __name__ == "__main__":
    sys.exit(main())
