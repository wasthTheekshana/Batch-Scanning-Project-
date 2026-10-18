"""The version is written in one place, and the places that repeat it cannot drift."""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import release  # noqa: E402


def test_version_installer_and_changelog_agree():
    assert release.consistency_problems() == []


def test_the_version_is_major_minor_patch():
    assert re.fullmatch(r"\d+\.\d+\.\d+", release.version())


def test_a_release_with_a_mismatched_installer_is_refused(monkeypatch):
    monkeypatch.setattr(release, "installer_version", lambda: "0.0.1")
    assert any("setup.iss" in p for p in release.consistency_problems())


def test_a_changelog_without_the_current_version_is_refused(monkeypatch):
    monkeypatch.setattr(release, "changelog_version", lambda: "9.9.9")
    assert any("CHANGELOG" in p for p in release.consistency_problems())


def test_the_changelog_has_an_entry_per_release_with_a_date():
    text = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    entries = re.findall(r"^## \[([^\]]+)\] - (\d{4}-\d{2}-\d{2})$", text, re.M)
    assert entries and entries[0][0] == release.version()


def test_the_selftest_and_the_window_title_show_the_version():
    main_py = (ROOT / "main.py").read_text(encoding="utf-8")
    assert "version" in main_py and "__version__" in main_py
    assert "__version__" in (ROOT / "ui" / "main_window.py").read_text(encoding="utf-8")


def test_the_install_test_checks_the_model_and_the_clean_profile():
    text = (ROOT / "scripts" / "install_test.py").read_text(encoding="utf-8")
    assert "LOCALAPPDATA" in text and "models/trocr/encoder_model.onnx" in text and "/VERYSILENT" in text


def test_an_unreleased_section_above_the_newest_release_does_not_break_the_check():
    text = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    assert text.index("## [Unreleased]") < text.index(f"## [{release.version()}]")
    assert release.changelog_version() == release.version()
    assert release.consistency_problems() == []
