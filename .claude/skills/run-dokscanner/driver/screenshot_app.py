"""Launch the real DOKScanner headless, sign in, screenshot every tab.

    python .claude/skills/run-dokscanner/driver/screenshot_app.py [--out DIR]

Prints one line per tab and exits 0 when every screenshot is non-blank.
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import _harness as h  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=h.DEFAULT_OUT)
    args = ap.parse_args()
    shots = args.out / "shots"
    h.setup_env(args.out)
    report = h.Report()

    def scenario(app, win):
        from PyQt6.QtWidgets import QLabel
        print(f"signed in; {win.tabs.count()} tabs", flush=True)
        for i in range(win.tabs.count()):
            win.tabs.setCurrentIndex(i)
            name = win.tabs.tabText(i)
            path = shots / f"{i:02d}-{name.lower().replace(' ', '-')}.png"
            report.check(f"tab '{name}' renders", h.shot(app, win, path), f"-> {path}")
        labels = [w.text() for w in win.statusBar().findChildren(QLabel)
                  if w.text() and not w.isHidden()]
        print(f"status bar: {labels}", flush=True)
        return report.summary()

    code = h.run_main(scenario)
    print(f"main() returned {code}")
    return code


if __name__ == "__main__":
    sys.exit(main())
