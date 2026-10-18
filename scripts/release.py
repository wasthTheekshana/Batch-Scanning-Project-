"""Prepare a release: check, build, verify, record.

    python scripts/release.py            # check + build + verify + write dist/RELEASE.txt
    python scripts/release.py --check    # only the consistency checks (no build)
    python scripts/release.py --tag      # after a successful build, create the git tag vX.Y.Z

Nothing is pushed anywhere. Steps:
  1. version.py, the installer script and CHANGELOG.md agree on the version;
  2. the git tree is clean (a release is built from committed code);
  3. the test suite passes;
  4. PyInstaller builds the exe, Inno Setup builds the installer;
  5. the exe's self-test passes;
  6. dist/RELEASE.txt records the version, the git commit and the SHA-256 of both files.
The clean-machine install test is a separate script: scripts/install_test.py.
"""
from __future__ import annotations

import argparse
import hashlib
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def version() -> str:
    from version import __version__
    return __version__


def installer_version() -> str:
    text = (ROOT / "installer" / "setup.iss").read_text(encoding="utf-8")
    return re.search(r'#define AppVersion "([^"]+)"', text).group(1)


def changelog_version() -> str | None:
    # the newest numbered release; an "[Unreleased]" section above it is work waiting for its number
    m = re.search(r"^## \[(\d+\.\d+\.\d+)\]", (ROOT / "CHANGELOG.md").read_text(encoding="utf-8"), re.M)
    return m.group(1) if m else None


def consistency_problems() -> list[str]:
    problems = []
    v = version()
    if not re.fullmatch(r"\d+\.\d+\.\d+", v):
        problems.append(f"version.py has {v!r}: expected MAJOR.MINOR.PATCH")
    if installer_version() != v:
        problems.append(f"installer/setup.iss says {installer_version()}, version.py says {v}")
    if changelog_version() != v:
        problems.append(f"CHANGELOG.md's newest entry is {changelog_version()}, version.py says {v}")
    return problems


def sync_installer_version() -> None:
    path = ROOT / "installer" / "setup.iss"
    text = path.read_text(encoding="utf-8")
    path.write_text(re.sub(r'(#define AppVersion ")[^"]+(")', rf"\g<1>{version()}\g<2>", text),
                    encoding="utf-8", newline="")


def run(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    print("  $", " ".join(str(c) for c in cmd))
    return subprocess.run(cmd, cwd=ROOT, **kw)


def git_clean() -> bool:
    out = subprocess.run(["git", "status", "--porcelain", "--untracked-files=no"], cwd=ROOT,
                         capture_output=True, text=True).stdout
    return not out.strip()


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def find_iscc() -> Path | None:
    import os
    for base in (os.environ.get("LOCALAPPDATA", ""), os.environ.get("ProgramFiles(x86)", ""),
                 os.environ.get("ProgramFiles", "")):
        for sub in ("Programs/Inno Setup 6", "Inno Setup 6"):
            p = Path(base) / sub / "ISCC.exe"
            if p.exists():
                return p
    return None


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--check", action="store_true", help="only the consistency checks")
    ap.add_argument("--tag", action="store_true", help="create the git tag after a good build")
    ap.add_argument("--skip-tests", action="store_true")
    args = ap.parse_args(argv)

    print(f"Release {version()}")
    problems = consistency_problems()
    for p in problems:
        print("  PROBLEM:", p)
    if problems:
        return 1
    print("  version, installer script and changelog agree")
    if args.check:
        return 0

    if not git_clean():
        print("  PROBLEM: uncommitted changes in tracked files; commit them first")
        return 1
    if not args.skip_tests and run([sys.executable, "scripts/run_tests.py", "--all"]).returncode:
        print("  PROBLEM: tests failed")
        return 1
    if run([sys.executable, "-m", "PyInstaller", "docscanner.spec", "--noconfirm"]).returncode:
        print("  PROBLEM: the exe did not build")
        return 1
    iscc = find_iscc()
    if iscc is None:
        print("  PROBLEM: Inno Setup 6 (ISCC.exe) not found")
        return 1
    if run([str(iscc), "installer/setup.iss"]).returncode:
        print("  PROBLEM: the installer did not build")
        return 1
    exe, setup = ROOT / "dist" / "DOKScanner.exe", ROOT / "dist" / "DOKScanner_Setup.exe"
    selftest = run([str(exe), "--selftest"], capture_output=True, text=True)
    print(selftest.stdout)
    if selftest.returncode:
        print("  PROBLEM: the exe's self-test failed")
        return 1
    commit = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True,
                            text=True).stdout.strip()
    record = (f"DOK Scanner {version()}\nbuilt {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC from commit {commit}\n"
              f"unsigned installer: sign it before distributing outside the pilot\n\n"
              f"{sha256(exe)}  {exe.name}\n{sha256(setup)}  {setup.name}\n")
    (ROOT / "dist" / "RELEASE.txt").write_text(record, encoding="utf-8")
    print(record)
    if args.tag:
        run(["git", "tag", "-a", f"v{version()}", "-m", f"DOK Scanner {version()}"])
        print(f"  tagged v{version()} (not pushed)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
