import subprocess
import sys

import pytest

from core.safe_paths import is_safe_relative_subpath, safe_join

SAFE = ["", "Routed", "Exceptions/Missing", "ClientA/Invoices", "ok/sub",
        "a\\b", "console", "com10", "my.folder", "a b"]
UNSAFE = ["..", "../x", "a/../../b", "a/..", "D:/x", "C:x", "C:\\x",
          "\\\\h\\s", "//h/s", "/abs", "\\abs", "CON", "con", "a/NUL.txt",
          "ok/COM1", "LPT9.log", "a/", "a//b", "./a", "a/.", "trail.", "a /b",
          "bad\x00name", "bad\nname", "a:b", 5, None, ["a"]]


@pytest.mark.parametrize("sub", SAFE)
def test_safe_subpaths_accepted(sub, tmp_path):
    assert is_safe_relative_subpath(sub) is True
    assert safe_join(tmp_path, sub).is_relative_to(tmp_path)


@pytest.mark.parametrize("sub", UNSAFE)
def test_unsafe_subpaths_rejected(sub, tmp_path):
    assert is_safe_relative_subpath(sub) is False
    with pytest.raises(ValueError, match="unsafe output folder"):
        safe_join(tmp_path, sub)


def test_safe_join_empty_is_the_base(tmp_path):
    assert safe_join(tmp_path, "") == tmp_path


def _make_dir_link(link, target):
    """Directory symlink, or a junction on Windows; skip if neither works."""
    try:
        link.symlink_to(target, target_is_directory=True)
        return
    except (OSError, NotImplementedError):
        pass
    if sys.platform == "win32":
        r = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)],
                           capture_output=True)
        if r.returncode == 0:
            return
    pytest.skip("cannot create a symlink or junction on this machine")


def test_safe_join_rejects_link_escaping_the_base(tmp_path):
    base, outside = tmp_path / "base", tmp_path / "outside"
    base.mkdir()
    outside.mkdir()
    _make_dir_link(base / "link", outside)
    with pytest.raises(ValueError, match="unsafe output folder"):
        safe_join(base, "link/inner")
