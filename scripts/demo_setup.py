"""Set up (or reset) a separate DEMO copy of the app's data.

    python scripts/demo_setup.py            # create / refresh the demo data
    python scripts/demo_setup.py --reset    # wipe the demo data, then create it

The demo lives in %USERPROFILE%\\DOKScannerDemo (never your real data folder):
a fresh database with the admin/admin login and the "Bank - Customer file"
project ready. Start the app against it with scripts\\launch_demo.bat (it only
sets LOCALAPPDATA, which is where the app looks for its data).
"""
from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DEMO_NAME = "DOKScannerDemo"
PROJECT_NAME = "Bank - Customer file"


def demo_base() -> Path:
    return Path(os.environ.get("USERPROFILE", Path.home())) / DEMO_NAME


def reset(base: Path) -> None:
    """Delete the demo folder. Refuses anything that is not named like it, so
    a wrong argument can never wipe real data."""
    if base.name != DEMO_NAME:
        raise SystemExit(f"refusing to delete {base}: not a {DEMO_NAME} folder")
    if base.exists():
        shutil.rmtree(base)


def setup(base: Path) -> Path:
    """Create the demo data under `base` (idempotent); returns the app's data
    folder (base/DOKScanner)."""
    sys.path.insert(0, str(REPO))
    os.environ["LOCALAPPDATA"] = str(base)       # main.data_dir() reads this
    import main as app_main
    from core.profile_manager import ProfileManager
    from core.rbac import Session
    from core.template_manager import TemplateManager
    from data.keystore import KeyStore

    root = app_main.data_dir()
    key = KeyStore(root / "keys" / "master.bin").get_or_create()
    db = app_main.open_database(root, key)
    try:
        Session.seed_default_admin(db)
        manager = TemplateManager(db)
        manager.seed_built_ins()
        if not any(row["name"] == PROJECT_NAME for row in db.list_projects()):
            template = next(t for t in manager.list_templates()
                            if t["name"] == PROJECT_NAME)
            ProfileManager(root / "projects", db, data_root=root).create(
                PROJECT_NAME,
                config=manager.build_project_config(template["template_id"]))
    finally:
        db.close()
    return root


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--reset", action="store_true",
                        help="delete the demo data first")
    parser.add_argument("--dir", type=Path, default=None,
                        help=f"demo folder (default %%USERPROFILE%%\\{DEMO_NAME})")
    args = parser.parse_args(argv)
    base = args.dir or demo_base()
    if args.reset:
        reset(base)
    root = setup(base)
    print(f"demo data ready: {root}")
    print("start it with scripts\\launch_demo.bat  (sign in: admin / admin)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
