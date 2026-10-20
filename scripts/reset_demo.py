"""Start fresh for a demo: delete every scanned document and its files, keep the projects (and the users).

    python scripts/reset_demo.py                 # shows what it would do, changes nothing
    python scripts/reset_demo.py --yes           # does it (DOK Scanner must be closed)
    python scripts/reset_demo.py --yes --index-only "Bank - Auto Indexing (Applications)"
                                                 # ...and makes that project read only account number, NIC and branch

Removed: documents, batches, jobs, custody events and links (a custody bundle's QR label stays registered, set back
to "received"), the audit log, the central sync queue, and the files in output\\, delivered\\ and staging\\.
Kept: projects (and their version history), users, templates, the central registration, backups, keys, logs.
A copy of the database is made first, in reset-backup\\ next to it: delete that folder when you no longer need it.

--index-only gives the named project the recognition rules of the "Bank - Demo (account, NIC, branch)" template
(page types, later-page patterns, form regions) with only those three fields, as a new version of that project:
everything else in it (scanner, export, delivery, separation) is left as it is.
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

DEFAULT_ROOT = Path.home() / "AppData" / "Local" / "DOKScanner"
WIPE_TABLES = ("custody_events", "bundle_documents", "documents", "jobs", "batches", "audit_log", "sync_outbox")
WIPE_FOLDERS = ("output", "delivered", "staging")
DEMO_TEMPLATE = REPO / "core" / "templates" / "bank_demo_index.json"


def app_running() -> bool:
    try:
        out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq DOKScanner.exe"], capture_output=True, text=True).stdout
    except OSError:
        return False
    return "DOKScanner.exe" in out


def folder_stats(folder: Path) -> tuple[int, int]:
    files = [p for p in folder.rglob("*") if p.is_file()] if folder.exists() else []
    return len(files), sum(p.stat().st_size for p in files)


def open_db(root: Path):
    from data.db import Database
    from data.keystore import KeyStore
    return Database(root / "dokscanner.db", key=KeyStore(root / "keys" / "master.bin").get_or_create())


def index_only(root: Path, db, name: str) -> str:
    """New version of the project `name` that reads only the three index fields."""
    from core.profile_manager import ProfileManager
    from core.project_store import load_config
    row = next((r for r in db.list_projects("active") if r["name"] == name), None)
    if row is None:
        raise SystemExit(f"no active project named {name!r}")
    cfg = load_config(row)
    if cfg is None:
        raise SystemExit(f"project {name!r} cannot be read")
    demo = json.loads(DEMO_TEMPLATE.read_text(encoding="utf-8"))["config"]["ocr"]
    template = json.loads(DEMO_TEMPLATE.read_text(encoding="utf-8"))["config"]
    # a form's title page also begins a document: a QR code the scanner spoiled no longer glues two forms together
    cfg["separation"] = {**cfg.get("separation", {}), "strategy": template["separation"]["strategy"]}
    keep = {k: v for k, v in cfg.get("ocr", {}).items() if k not in ("document_types", "zones")}
    cfg["ocr"] = {**keep, "zones": [], "document_types": demo["document_types"],
                  "mode": demo.get("mode", keep.get("mode", "zone"))}
    saved = ProfileManager(root / "projects", db, data_root=root).save(cfg)
    fields = sorted({z["name"] for t in saved["ocr"]["document_types"] for z in t.get("zones", [])})
    return f"{name}: now version {saved['version']}, reads {', '.join(fields)}"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--yes", action="store_true", help="really do it (without this, only report)")
    ap.add_argument("--data-dir", type=Path, default=DEFAULT_ROOT, help="the DOKScanner data folder")
    ap.add_argument("--index-only", metavar="PROJECT", help="make this project read only account number, NIC, branch")
    ap.add_argument("--keep-bundles", metavar="PREFIX",
                    help="also delete every custody bundle whose label does not start with PREFIX (case ignored), "
                         "e.g. --keep-bundles dok/com")
    ap.add_argument("--no-wipe", action="store_true", help="with --index-only: change the project only, delete nothing")
    args = ap.parse_args(argv)
    root: Path = args.data_dir
    if not (root / "dokscanner.db").exists():
        print(f"no database in {root}")
        return 2
    if args.yes and app_running() and root == DEFAULT_ROOT:
        print("DOK Scanner is running: close it first (it holds the database open), then run this again.")
        return 3
    db = open_db(root)
    if args.no_wipe:
        if not args.index_only:
            print("--no-wipe is for use with --index-only")
            return 2
        if args.yes and app_running() and root == DEFAULT_ROOT:
            print("DOK Scanner is running: close it first, then run this again.")
            return 3
        print(index_only(root, db, args.index_only) if args.yes else "Would update the project. Add --yes to do it.")
        db.close()
        return 0
    counts = {t: db.query(f"SELECT COUNT(*) FROM {t}")[0][0] for t in WIPE_TABLES}
    folders = {f: folder_stats(root / f) for f in WIPE_FOLDERS}
    print("Would remove:" if not args.yes else "Removing:")
    for t, n in counts.items():
        print(f"  {n:6d} rows  {t}")
    for f, (n, size) in folders.items():
        print(f"  {n:6d} files {f}\\  ({size / 1e6:.0f} MB)")
    if args.keep_bundles:
        like = args.keep_bundles.lower() + "%"
        gone = db.query("SELECT label FROM bundles WHERE lower(label) NOT LIKE ?", (like,))
        kept = db.query("SELECT label FROM bundles WHERE lower(label) LIKE ?", (like,))
        print(f"  {len(gone):6d} custody bundles not starting with {args.keep_bundles!r}: " + ", ".join(r["label"] for r in gone))
        print(f"  {len(kept):6d} custody bundles kept: " + ", ".join(r["label"] for r in kept))
    print("Keeping: projects, users, templates, central registration, backups, keys, logs")
    if not args.yes:
        print("Nothing changed. Add --yes to do it.")
        db.close()
        return 0
    backup = root / "reset-backup" / datetime.now().strftime("%Y%m%d-%H%M%S")
    backup.mkdir(parents=True)
    db.conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    shutil.copy2(root / "dokscanner.db", backup / "dokscanner.db")
    print(f"Database copied to {backup}")
    with db._lock, db.conn:
        for t in WIPE_TABLES:
            db.conn.execute(f"DELETE FROM {t}")
        if args.keep_bundles:
            db.conn.execute("DELETE FROM bundles WHERE lower(label) NOT LIKE ?", (args.keep_bundles.lower() + "%",))
        db.conn.execute("UPDATE bundles SET status = 'received'")
    for f in WIPE_FOLDERS:
        folder = root / f
        if folder.exists():
            for child in folder.iterdir():
                shutil.rmtree(child, ignore_errors=True) if child.is_dir() else child.unlink(missing_ok=True)
    if args.index_only:
        print(index_only(root, db, args.index_only))
    db.conn.execute("VACUUM")
    db.close()
    print("Done. Start DOK Scanner: it opens empty, with the projects as they were.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
