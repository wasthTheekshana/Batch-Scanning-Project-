"""Scheduled backup & disaster recovery (HLD §14).

run_backup() produces a single encrypted `.dokbak` archive containing a
consistent SQLite snapshot (SQLite online backup API — safe while the
app is running) plus the project config folder and optional file
stores. The archive is a Fernet-encrypted zip; the Fernet key derives
from the DPAPI-protected master key, and restore accepts the raw master
key so archives can be recovered on a clean machine when the key is
exported (HLD §14.3 restore test).
"""
from __future__ import annotations

import base64
import io
import logging
import shutil
import sqlite3
import threading
import zipfile
from datetime import datetime
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

log = logging.getLogger(__name__)

ARCHIVE_SUFFIX = ".dokbak"
DEFAULT_KEEP = 10


def _fernet(master_key: bytes) -> Fernet:
    return Fernet(base64.urlsafe_b64encode(master_key[:32]))


class RestoreError(Exception):
    """Archive missing, corrupt, or wrong key."""


class BackupService:
    def __init__(self, db, sources: list[str | Path], dest_dir: str | Path,
                 master_key: bytes, interval_hours: float = 24.0,
                 keep: int = DEFAULT_KEEP):
        self.db = db
        self.sources = [Path(s) for s in sources]
        self.dest_dir = Path(dest_dir)
        self.master_key = master_key
        self.interval_hours = interval_hours
        self.keep = keep
        self.last_result: dict | None = None
        self._timer: threading.Timer | None = None
        self._stopped = threading.Event()

    # -- backup -----------------------------------------------------------------
    def run_backup(self) -> Path:
        self.dest_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        out_path = self.dest_dir / f"dok_backup_{stamp}{ARCHIVE_SUFFIX}"

        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("db/dokscanner.db", self._db_snapshot())
            for src in self.sources:
                if not src.exists():
                    continue
                for f in src.rglob("*"):
                    if f.is_file():
                        zf.write(f, f"files/{src.name}/"
                                    f"{f.relative_to(src).as_posix()}")

        out_path.write_bytes(_fernet(self.master_key).encrypt(buffer.getvalue()))
        self._prune()
        self.last_result = {"path": str(out_path),
                            "time": datetime.now().isoformat(),
                            "size": out_path.stat().st_size}
        self.db.append_audit(None, "system", "backup",
                             {"path": str(out_path)})
        log.info("backup written: %s", out_path)
        return out_path

    def _db_snapshot(self) -> bytes:
        """Consistent snapshot via the SQLite online backup API; works for
        both plain and SQLCipher connections (snapshot is plaintext inside
        the encrypted archive)."""
        if self.db.encrypted:
            return self._encrypted_db_snapshot()
        dest = sqlite3.connect(":memory:")
        try:
            self.db.conn.backup(dest)
            out = io.BytesIO()
            for line in dest.iterdump():
                out.write((line + "\n").encode("utf-8"))
            return out.getvalue()
        finally:
            dest.close()

    def _encrypted_db_snapshot(self) -> bytes:
        """SQLCipher connections lack iterdump/backup interop with stdlib
        sqlite3, so decrypt into a temporary plaintext file with
        sqlcipher_export, dump it, and remove it."""
        import tempfile
        tmp = Path(tempfile.mkdtemp()) / "snapshot.db"
        try:
            self.db.conn.execute(
                f"ATTACH DATABASE '{tmp.as_posix()}' AS plainbk KEY ''")
            self.db.conn.execute("SELECT sqlcipher_export('plainbk')")
            self.db.conn.execute("DETACH DATABASE plainbk")
            conn = sqlite3.connect(str(tmp))
            try:
                out = io.BytesIO()
                for line in conn.iterdump():
                    out.write((line + "\n").encode("utf-8"))
                return out.getvalue()
            finally:
                conn.close()
        finally:
            shutil.rmtree(tmp.parent, ignore_errors=True)

    def _prune(self) -> None:
        archives = sorted(self.dest_dir.glob(f"*{ARCHIVE_SUFFIX}"))
        for old in archives[:-self.keep]:
            old.unlink(missing_ok=True)

    # -- scheduler ---------------------------------------------------------------
    def start(self) -> None:
        self._stopped.clear()
        self._schedule_next()

    def stop(self) -> None:
        self._stopped.set()
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None

    def _schedule_next(self) -> None:
        if self._stopped.is_set():
            return
        self._timer = threading.Timer(self.interval_hours * 3600, self._tick)
        self._timer.daemon = True
        self._timer.start()

    def _tick(self) -> None:
        try:
            self.run_backup()
        except Exception:
            log.exception("scheduled backup failed")
        self._schedule_next()


def restore_backup(archive: str | Path, out_dir: str | Path,
                   master_key: bytes) -> Path:
    """Decrypt and unpack an archive. The DB is restored as
    out_dir/dokscanner.restored.db (plaintext SQL replayed); file stores
    land under out_dir/files/. Returns the restored DB path."""
    archive = Path(archive)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    try:
        raw = _fernet(master_key).decrypt(archive.read_bytes())
    except (InvalidToken, ValueError) as e:
        raise RestoreError(f"cannot decrypt {archive.name}: wrong key or "
                           f"corrupt archive") from e

    db_path = out_dir / "dokscanner.restored.db"
    with zipfile.ZipFile(io.BytesIO(raw)) as zf:
        sql = zf.read("db/dokscanner.db").decode("utf-8")
        if db_path.exists():
            db_path.unlink()
        conn = sqlite3.connect(str(db_path))
        try:
            conn.executescript(sql)
            conn.commit()
        finally:
            conn.close()
        for name in zf.namelist():
            if name.startswith("files/"):
                target = out_dir / name
                target.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(name) as src, open(target, "wb") as dst:
                    shutil.copyfileobj(src, dst)
    return db_path
