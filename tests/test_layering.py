"""The packages depend on each other in one direction only (see README, "Layering")."""
import importlib.util
from pathlib import Path

SCRIPT = Path(__file__).parent.parent / "scripts" / "check_layers.py"


def load():
    spec = importlib.util.spec_from_file_location("check_layers", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_no_package_imports_one_it_must_not():
    assert load().violations() == []


def test_the_check_notices_a_wrong_way_import(tmp_path, monkeypatch):
    module = load()
    (tmp_path / "shared").mkdir()
    (tmp_path / "shared" / "bad.py").write_text("from ui.main_window import MainWindow\n", encoding="utf-8")
    monkeypatch.setattr(module, "ROOT", tmp_path)
    monkeypatch.setattr(module, "ALLOWED", {"shared": set(), "ui": set()})
    monkeypatch.setattr(module, "INTERNAL", {"shared", "ui"})
    assert module.violations() == ["shared/bad.py: shared must not import ui"]
