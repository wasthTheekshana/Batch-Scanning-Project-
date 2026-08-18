"""Guards against the class of bug where a module loads a JSON file
relative to its own __file__ (core/profile_manager.py, separation/
rule_engine.py) but PyInstaller's Analysis only auto-detects Python
imports, not data files — the packaged EXE then crashes at import time
with FileNotFoundError because the file never got bundled."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

_ADJACENT_DATA_FILE = re.compile(
    r'Path\(__file__\)\.parent\s*/\s*["\']([^"\']+\.\w+)["\']')


def _relative_adjacent_data_files() -> set[str]:
    """Every 'package/file.ext' referenced via Path(__file__).parent in
    the app's own source (not tests/, not the venv)."""
    found = set()
    for py_file in ROOT.rglob("*.py"):
        if any(part in {".venv", "tests", "__pycache__"}
               for part in py_file.parts):
            continue
        text = py_file.read_text(encoding="utf-8")
        for name in _ADJACENT_DATA_FILE.findall(text):
            found.add(f"{py_file.parent.name}/{name}")
    return found


def test_every_module_adjacent_data_file_is_bundled_in_spec():
    spec_text = (ROOT / "docscanner.spec").read_text(encoding="utf-8")
    missing = [f for f in _relative_adjacent_data_files()
              if f.replace("\\", "/") not in spec_text.replace("\\", "/")]
    assert not missing, (
        f"{missing} are loaded via Path(__file__).parent but not listed "
        "in docscanner.spec's datas — the packaged EXE will crash at "
        "import time with FileNotFoundError. Add them to datas=[...].")
