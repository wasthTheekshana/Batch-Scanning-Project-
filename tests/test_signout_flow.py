"""Sign out keeps the program running and brings back the sign-in page; dialogs of the old session are closed;
closing the window quits. Drives the real main() headless (scripts/smoke_signout.py), so it takes ~12 seconds."""
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent


@pytest.mark.slow
def test_sign_out_shows_the_sign_in_page_again_and_closing_the_window_quits():
    done = subprocess.run([sys.executable, str(ROOT / "scripts" / "smoke_signout.py")], capture_output=True, text=True,
                          timeout=180, cwd=str(ROOT))
    lines = [ln for ln in done.stdout.splitlines() if ln and " INFO " not in ln and "Warning" not in ln]
    assert done.returncode == 0, done.stderr[-500:]
    assert lines.count("sign-in page shown") == 2                      # at start-up, and again after Sign out
    assert "main windows open: 1" in lines
    assert "extras visible before sign out: [True, True]" in lines
    assert "modal exec returned: 0" in lines                           # a dialog running its own loop was ended
    assert any("main windows open: 1; old dialogs still visible: [False, False]" in ln for ln in lines)
    assert lines[-1] == "exit code 0"
