"""Restore a DOK Scanner backup archive on this machine (HLD §14.3).

The archive is Fernet-encrypted with the installation's master key.
On the ORIGINAL machine the key loads from the DPAPI keystore
automatically; on a CLEAN machine pass the exported key file with
--key-hex (32-byte key as hex, exported by the admin beforehand).

Usage:
    python scripts/restore.py <archive.dokbak> <output_dir>
    python scripts/restore.py <archive.dokbak> <output_dir> --key-hex <hex>
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from data.backup import RestoreError, restore_backup  # noqa: E402
from data.keystore import KeyStore  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", help="path to .dokbak archive")
    parser.add_argument("output_dir", help="directory to restore into")
    parser.add_argument("--key-hex",
                        help="master key as 64 hex chars (clean-machine "
                             "restore); omit to use this machine's keystore")
    args = parser.parse_args()

    if args.key_hex:
        key = bytes.fromhex(args.key_hex)
    else:
        root = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "DOKScanner"
        keystore_path = root / "keys" / "master.bin"
        if not keystore_path.exists():
            print("ERROR: no keystore on this machine — pass --key-hex")
            return 2
        key = KeyStore(keystore_path).get_or_create()

    try:
        db_path = restore_backup(args.archive, args.output_dir, key)
    except RestoreError as e:
        print(f"RESTORE FAILED: {e}")
        return 1
    print(f"Restored database : {db_path}")
    print(f"Restored files    : {Path(args.output_dir) / 'files'}")
    print("Verify with a SQLite browser that projects/documents/audit_log "
          "rows are intact (UAT restore test, HLD 14.3).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
