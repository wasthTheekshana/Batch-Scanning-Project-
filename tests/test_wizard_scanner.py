"""Scanning from the wizard's source page (fake scanner, real Qt threads)."""
import threading

import cv2
import numpy as np

from scanning.base import ScannerUnavailable
from ui.scan_wizard import ScanWizard

DEVICES = {"wia": ["KODAK S2050 Scanner"]}


def png(shade: int) -> bytes:
    page = np.full((60, 40), shade, np.uint8)
    ok, buf = cv2.imencode(".png", page)
    assert ok
    return buf.tobytes()


class FakeSource:
    def __init__(self, pages=(), error=None, gate=None):
        self.pages, self.error, self.gate = list(pages), error, gate
        self.calls = []
        self.connected = None
        self.disconnected = False

    def connect(self, device):
        self.connected = device

    def acquire(self, dpi, color, duplex, on_page=None):
        self.calls.append((dpi, color, duplex))
        if self.gate:
            self.gate.wait(5)
        for n, data in enumerate(self.pages, 1):
            on_page(data, n)
        if self.error:
            raise self.error
        return self.pages

    def disconnect(self):
        self.disconnected = True


def project(scanner=None):
    cfg = {"scanner": scanner} if scanner else {}
    return {"project_id": "p1", "name": "P", "config": cfg}


def make(qtbot, tmp_path, source, devices=DEVICES, scanner=None):
    wiz = ScanWizard([project(scanner)], tmp_path, devices=devices,
                     source_factory=lambda name: source)
    qtbot.addWidget(wiz)
    return wiz


def choose_scanner(wiz):
    wiz.source_page.source.setCurrentIndex(1)
    return wiz.source_page


def scan(qtbot, page):
    page.scan_button.click()
    qtbot.waitUntil(lambda: not page.scanning, timeout=5000)


def test_without_scanners_only_file_import_is_offered(qtbot, tmp_path):
    page = make(qtbot, tmp_path, FakeSource(), devices={}).source_page
    assert page.source.count() == 1
    assert page.source.currentData() is None
    assert page.scan_button.isHidden() and not page.pick_button.isHidden()


def test_a_detected_scanner_is_listed_and_swaps_the_buttons(qtbot, tmp_path):
    page = choose_scanner(make(qtbot, tmp_path, FakeSource()))
    assert page.source.count() == 2
    assert page.source.currentText() == "Scanner: KODAK S2050 Scanner (WIA)"
    assert page.source.currentData() == ("wia", "KODAK S2050 Scanner")
    assert page.pick_button.isHidden() and not page.scan_button.isHidden()


def test_scanned_pages_become_files_in_scan_order(qtbot, tmp_path):
    src = FakeSource([png(10), png(20), png(30)])
    page = choose_scanner(make(qtbot, tmp_path, src))
    scan(qtbot, page)
    files = page.selected_files()
    assert len(files) == 3
    assert [cv2.imread(f, cv2.IMREAD_GRAYSCALE)[0, 0] for f in files] == [10, 20, 30]
    assert all(f.startswith(str(tmp_path)) for f in files)
    assert page.status.text().startswith("Scan complete: 3 page(s)")
    assert src.connected == "KODAK S2050 Scanner" and src.disconnected


def test_the_chosen_projects_scanner_settings_are_used(qtbot, tmp_path):
    src = FakeSource([png(1)])
    page = choose_scanner(make(
        qtbot, tmp_path, src,
        scanner={"dpi": 200, "color": "bw", "duplex": False}))
    scan(qtbot, page)
    # a black-and-white project is scanned in grayscale; the page is made black and white
    # afterwards, locally adaptive (the scanner's own 1-bit mode loses thin strokes)
    assert src.calls == [(200, "grayscale", False)]


def test_defaults_apply_when_the_project_has_no_scanner_section(qtbot, tmp_path):
    src = FakeSource([png(1)])
    scan(qtbot, choose_scanner(make(qtbot, tmp_path, src)))
    assert src.calls == [(300, "grayscale", True)]


def test_an_empty_feeder_message_is_shown_and_scanning_can_be_retried(qtbot, tmp_path):
    err = ScannerUnavailable("The document feeder is empty - load paper and "
                             "scan again.")
    page = choose_scanner(make(qtbot, tmp_path, FakeSource(error=err)))
    scan(qtbot, page)
    assert page.status.text() == str(err)
    assert page.selected_files() == []
    assert page.scan_button.isEnabled() and page.source.isEnabled()


def test_pages_scanned_before_a_jam_are_kept(qtbot, tmp_path):
    src = FakeSource([png(5), png(6)], error=ScannerUnavailable("Paper jam"))
    page = choose_scanner(make(qtbot, tmp_path, src))
    scan(qtbot, page)
    assert len(page.selected_files()) == 2
    assert "Paper jam" in page.status.text()
    assert "2 page(s) already scanned were kept" in page.status.text()


def test_an_unexpected_error_shows_only_its_type(qtbot, tmp_path):
    page = choose_scanner(make(qtbot, tmp_path, FakeSource(
        error=RuntimeError("S3CRET-VALUE-123"))))
    scan(qtbot, page)
    assert page.status.text() == "The scan failed (RuntimeError)."


def test_a_second_scan_adds_to_the_same_batch(qtbot, tmp_path):
    src = FakeSource([png(1), png(2)])
    page = choose_scanner(make(qtbot, tmp_path, src))
    scan(qtbot, page)
    scan(qtbot, page)
    files = page.selected_files()
    assert len(files) == 4 and len(set(files)) == 4


def test_next_is_blocked_and_the_wizard_cannot_close_during_a_scan(qtbot, tmp_path):
    gate = threading.Event()
    wiz = make(qtbot, tmp_path, FakeSource([png(1)], gate=gate))
    page = choose_scanner(wiz)
    wiz.show()
    page.scan_button.click()
    try:
        assert page.scanning and not page.isComplete()
        assert not page.scan_button.isEnabled()
        wiz.reject()
        assert wiz.isVisible()                 # refused while scanning
    finally:
        gate.set()
    qtbot.waitUntil(lambda: not page.scanning, timeout=5000)
    assert page.isComplete()
    wiz.reject()
    assert not wiz.isVisible()


def test_finishing_hands_the_scanned_pages_to_the_pipeline_and_cleans_up(qtbot, tmp_path):
    wiz = make(qtbot, tmp_path, FakeSource([png(10), png(20), png(30)]))
    page = choose_scanner(wiz)
    scan(qtbot, page)
    scan_dir = page.scan_dir
    assert scan_dir.exists()
    with qtbot.waitSignal(wiz.batch_ready, timeout=5000) as sig:
        wiz.accept()
    batch_id, jobs = sig.args
    assert [j.page_no for j in jobs] == [1, 2, 3]
    assert all(j.project_id == "p1" for j in jobs)
    assert not scan_dir.exists()


def test_start_without_pages_still_warns(qtbot, tmp_path, monkeypatch):
    shown = []
    monkeypatch.setattr("ui.scan_wizard.QMessageBox.warning",
                        lambda *a, **k: shown.append(a[1]))
    wiz = make(qtbot, tmp_path, FakeSource())
    choose_scanner(wiz)
    wiz.accept()
    assert shown == ["Nothing to scan"]
