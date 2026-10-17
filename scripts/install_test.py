"""Install the built installer into an isolated folder with an EMPTY user profile, check that
the application starts and passes its self-test, then uninstall and check nothing is left.

    python scripts/install_test.py [path\to\DOKScanner_Setup.exe]

It is the closest to "a clean PC" that can be done on a development machine: a fresh install
directory, a fresh data folder (LOCALAPPDATA is pointed at an empty temp folder, so nothing from
the developer's own data is used) and a silent install/uninstall as the group-policy
deployment would do. It does not replace one run on a really clean Windows machine before
the first customer install (needs administrator rights: the installer is per-machine).
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def step(name: str, ok: bool, detail: str = "") -> bool:
    print(f"{'ok  ' if ok else 'FAIL'}  {name}{(': ' + detail) if detail else ''}")
    return ok


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    setup = Path(argv[0]) if argv else ROOT / "dist" / "DOKScanner_Setup.exe"
    if not setup.exists():
        print("installer not found:", setup)
        return 2
    work = Path(tempfile.mkdtemp(prefix="dok_install_test_"))
    target, profile, log = work / "app", work / "profile", work / "install.log"
    profile.mkdir()
    env = {**os.environ, "LOCALAPPDATA": str(profile)}
    good = True

    done = subprocess.run([str(setup), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART",
                           f"/DIR={target}", f"/LOG={log}"], capture_output=True, timeout=900)
    exe = target / "DOKScanner.exe"
    good &= step("silent install", done.returncode == 0 and exe.exists(), f"exit {done.returncode}")
    if not exe.exists():
        print("log:", log)
        return 1

    for name in ("tesseract", "models/trocr/encoder_model.onnx", "models/trocr/decoder_model.onnx",
                 "models/trocr/tokenizer.json", "unins000.exe"):
        optional = name == "tesseract" and not (ROOT / "dist" / "tesseract").exists()
        good &= step(f"installed {name}", (target / name).exists() or optional)

    result = subprocess.run([str(exe), "--selftest"], capture_output=True, text=True, env=env, timeout=300)
    good &= step("self-test on an empty profile", result.returncode == 0)
    for line in result.stdout.splitlines():
        if not line.startswith("ok"):
            print("      ", line)
    good &= step("a fresh data folder was created", (profile / "DOKScanner").exists())
    good &= step("handwriting model found by the installed app",
                 "handwriting model: reads" in result.stdout)

    gone = subprocess.run([str(target / "unins000.exe"), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART"],
                          capture_output=True, timeout=600)
    good &= step("silent uninstall", gone.returncode == 0)
    import time
    for _ in range(40):                          # the uninstaller finishes after it returns
        if not exe.exists():
            break
        time.sleep(0.5)
    good &= step("the application files are removed", not exe.exists())
    good &= step("the user's data is left alone (HLD 21.2)", (profile / "DOKScanner").exists())
    print("\nINSTALL TEST", "PASSED" if good else "FAILED", "| workspace:", work)
    return 0 if good else 1


if __name__ == "__main__":
    sys.exit(main())
