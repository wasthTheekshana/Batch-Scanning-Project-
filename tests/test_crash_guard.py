"""One failed button click must not take the whole application down."""
import sys
import threading
from pathlib import Path

import pytest

from core import crash_guard

SECRET = "S3CRET-VALUE-123"


@pytest.fixture(autouse=True)
def restore_hooks():
    hook, thread_hook = sys.excepthook, threading.excepthook
    yield
    sys.excepthook, threading.excepthook = hook, thread_hook
    crash_guard._guard["busy"] = False


def boom(message=SECRET):
    try:
        raise ValueError(message)
    except ValueError:
        return sys.exc_info()


def test_crash_log_has_the_error_type_and_where_it_happened_but_not_its_text(tmp_path):
    handler = crash_guard.install(tmp_path, show=None)
    handler(*boom(SECRET))
    text = (tmp_path / "crash.log").read_text(encoding="utf-8")
    assert "ValueError (message withheld)" in text
    assert "test_crash_guard.py" in text and " in boom" in text
    assert SECRET not in text


def test_a_chained_cause_never_leaks_its_text_either(tmp_path):
    handler = crash_guard.install(tmp_path, show=None)
    try:
        try:
            raise KeyError(SECRET)
        except KeyError as inner:
            raise RuntimeError("outer") from inner
    except RuntimeError:
        handler(*sys.exc_info())
    assert SECRET not in (tmp_path / "crash.log").read_text(encoding="utf-8")


def test_a_developer_can_opt_in_to_the_full_traceback_text(tmp_path, monkeypatch):
    monkeypatch.setenv("DOKSCANNER_CRASH_DETAIL", "1")
    handler = crash_guard.install(tmp_path, show=None)
    handler(*boom("the real reason"))
    text = (tmp_path / "crash.log").read_text(encoding="utf-8")
    assert "ValueError: the real reason" in text and "Traceback" in text


def test_the_operator_message_names_the_kind_of_error_never_its_text(tmp_path):
    shown = []
    handler = crash_guard.install(tmp_path, show=lambda t, m: shown.append((t, m)))
    handler(*boom())
    assert len(shown) == 1
    title, message = shown[0]
    assert title == "DOK Scanner"
    assert "ValueError" in message and "crash.log" in message
    assert SECRET not in message


def test_the_error_text_is_not_written_to_the_application_log(tmp_path, caplog):
    handler = crash_guard.install(tmp_path, show=None)
    with caplog.at_level("ERROR"):
        handler(*boom())
    assert SECRET not in caplog.text and "ValueError" in caplog.text


def test_it_becomes_the_excepthook_and_the_thread_hook(tmp_path):
    handler = crash_guard.install(tmp_path, show=None)
    assert sys.excepthook is handler
    errors = []
    t = threading.Thread(target=lambda: (_ for _ in ()).throw(KeyError("k")))
    t.start()
    t.join()
    assert "KeyError" in (tmp_path / "crash.log").read_text(encoding="utf-8")
    assert errors == []


def test_a_message_box_that_itself_fails_never_raises_out(tmp_path):
    def broken_box(title, text):
        raise RuntimeError("no display")
    handler = crash_guard.install(tmp_path, show=broken_box)
    handler(*boom())                      # must not raise
    assert (tmp_path / "crash.log").exists()


def test_only_one_message_box_is_open_at_a_time(tmp_path):
    shown = []
    handler = None

    def nested_box(title, text):
        shown.append(text)
        handler(*boom())                  # an error while the box is open
    handler = crash_guard.install(tmp_path, show=nested_box)
    handler(*boom())
    assert len(shown) == 1


def test_an_unwritable_log_folder_does_not_break_the_handler(tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("x")
    handler = crash_guard.install(blocker / "logs", show=None)   # cannot be created
    handler(*boom())


def test_ctrl_c_still_interrupts(tmp_path, monkeypatch):
    called = []
    monkeypatch.setattr(sys, "__excepthook__", lambda *a: called.append(a))
    handler = crash_guard.install(tmp_path, show=None)
    try:
        raise KeyboardInterrupt
    except KeyboardInterrupt:
        handler(*sys.exc_info())
    assert called and not (tmp_path / "crash.log").exists()


# -- the packaging mistake that caused it ----------------------------------------------------
def test_the_build_bundles_reportlab_barcode_modules_that_reportlab_loads_by_name():
    spec = (Path(__file__).parent.parent / "docscanner.spec").read_text(encoding="utf-8")
    assert 'collect_submodules("reportlab.graphics.barcode")' in spec
    assert "pythoncom" in spec


def test_the_modules_the_spec_collects_include_the_one_that_was_missing():
    from PyInstaller.utils.hooks import collect_submodules
    collected = collect_submodules("reportlab.graphics.barcode")
    assert "reportlab.graphics.barcode.code93" in collected
    assert "reportlab.graphics.barcode.code128" in collected


@pytest.mark.skipif(not __import__("ocr.tesseract_engine", fromlist=["x"]).is_available(),
                    reason="Tesseract is not installed")
def test_the_selftest_the_exe_offers_passes_on_a_good_install(tmp_path, monkeypatch, capsys):
    import main
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    assert main.selftest() == 0
    report = (tmp_path / "DOKScanner" / "selftest.txt").read_text(encoding="utf-8")
    assert "FAIL" not in report and "barcode round trip" in report


def test_a_qt_button_whose_handler_raises_no_longer_aborts_the_program(tmp_path):
    """The real failure: PyQt6 aborts the whole process when a slot raises.
    Run in a separate process, since the abort would kill the test run."""
    import subprocess
    code = (
        "import sys\n"
        f"sys.path.insert(0, {str(Path(__file__).parent.parent)!r})\n"
        "from PyQt6.QtWidgets import QApplication, QPushButton\n"
        "from core import crash_guard\n"
        "app = QApplication([])\n"
        f"crash_guard.install({str(tmp_path)!r}, show=lambda t, m: print('SHOWN', m[:30]))\n"
        "def slot():\n"
        "    raise ModuleNotFoundError('reportlab.graphics.barcode.code93')\n"
        "b = QPushButton('x'); b.clicked.connect(slot); b.click()\n"
        "print('STILL ALIVE')\n")
    env = {**__import__("os").environ, "QT_QPA_PLATFORM": "offscreen"}
    run = subprocess.run([sys.executable, "-c", code], capture_output=True,
                         text=True, env=env, timeout=60)
    assert run.returncode == 0, run.stderr[-400:]
    assert "STILL ALIVE" in run.stdout and "SHOWN" in run.stdout
    log = (tmp_path / "crash.log").read_text(encoding="utf-8")
    assert "ModuleNotFoundError" in log and "reportlab.graphics" not in log


def test_without_the_guard_the_same_click_kills_the_process(tmp_path):
    """Proves the test above is meaningful: this is what the user saw."""
    import subprocess
    code = (
        "from PyQt6.QtWidgets import QApplication, QPushButton\n"
        "app = QApplication([])\n"
        "def slot():\n"
        "    raise ModuleNotFoundError('x')\n"
        "b = QPushButton('x'); b.clicked.connect(slot); b.click()\n"
        "print('STILL ALIVE')\n")
    env = {**__import__("os").environ, "QT_QPA_PLATFORM": "offscreen"}
    run = subprocess.run([sys.executable, "-c", code], capture_output=True,
                         text=True, env=env, timeout=60)
    assert run.returncode != 0 and "STILL ALIVE" not in run.stdout
