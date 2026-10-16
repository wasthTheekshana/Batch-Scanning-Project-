"""The TWAIN scanner source, against a fake driver, and the choice of TWAIN for a
scanner that has its own driver."""
import time
import types

import cv2
import numpy as np
import pytest

from scanning import twain_source
from scanning.base import ScannerUnavailable
from scanning.sources import preferred_choice, same_family
from scanning.twain_source import (BUSY_MESSAGE, EMPTY_MESSAGE, NO_DRIVER_MESSAGE,
                                   TWAINSource, WM_QUIT)


class CancelAll(Exception):
    pass


class SequenceError(Exception):
    pass


FAKE = types.SimpleNamespace(
    ICAP_XRESOLUTION=0x1118, ICAP_YRESOLUTION=0x1119, ICAP_PIXELTYPE=0x0101,
    CAP_FEEDERENABLED=0x1002, CAP_DUPLEXENABLED=0x1013, CAP_DUPLEX=0x1012,
    CAP_XFERCOUNT=0x0001, TWTY_FIX32=7, TWTY_UINT16=4, TWTY_BOOL=6, TWTY_INT16=3,
    TWPT_BW=0, TWPT_GRAY=1, TWPT_RGB=2, CancelAll=CancelAll)


def bmp(shade: int) -> bytes:
    ok, buf = cv2.imencode(".bmp", np.full((20, 30), shade, np.uint8))
    assert ok
    return buf.tobytes()


class FakeImage:
    def __init__(self, shade):
        self.shade = shade

    def save(self, path):
        with open(path, "wb") as f:
            f.write(bmp(self.shade))


class FakeSource:
    def __init__(self, sheets, refuse=(), fail_after=None, raise_first=None, pause=0.0):
        self.sheets = list(sheets)
        self.caps = {}
        self.refuse = set(refuse)
        self.fail_after = fail_after
        self.raise_first = raise_first
        self.pause = pause
        self.destroyed = False

    def SetCapability(self, code, kind, value):
        if code in self.refuse:
            raise RuntimeError("refused")
        self.caps[code] = value

    def GetCapability(self, code):
        return (4, 1) if code == FAKE.CAP_DUPLEX else (6, 0)

    def acquire_natively(self, after, show_ui=True):
        if self.raise_first:
            raise self.raise_first
        for n, shade in enumerate(self.sheets):
            if self.fail_after is not None and n >= self.fail_after:
                raise RuntimeError("paper jam")
            time.sleep(self.pause)
            after(FakeImage(shade), len(self.sheets) - n - 1)

    def destroy(self):
        self.destroyed = True


class FakeManager:
    def __init__(self, source, names=("KODAK Scanner: S2000f/S3000",)):
        self.source, self.names, self.destroyed = source, list(names), False

    def GetSourceList(self):
        return self.names

    def OpenSource(self, name):
        if name not in self.names:
            raise RuntimeError("NoDataSourceError")
        return self.source

    def destroy(self):
        self.destroyed = True


@pytest.fixture()
def wire(monkeypatch):
    """Install the fake driver; returns a function that makes a connected source."""
    posted = []
    fake_ctypes = types.SimpleNamespace(windll=types.SimpleNamespace(
        kernel32=types.SimpleNamespace(GetCurrentThreadId=lambda: 4242),
        user32=types.SimpleNamespace(PostThreadMessageW=lambda *a: posted.append(a))))
    monkeypatch.setattr(twain_source, "ctypes", fake_ctypes)
    monkeypatch.setattr(twain_source, "_hidden_window", lambda: 0)

    def make(source, **kw):
        manager = FakeManager(source, **kw)
        twain_mod = types.SimpleNamespace(**vars(FAKE), SourceManager=lambda hwnd: manager)
        monkeypatch.setattr(twain_source, "_twain", lambda: twain_mod)
        s = TWAINSource()
        s.connect("KODAK Scanner: S2000f/S3000")
        s.posted = posted
        s.manager = manager
        return s
    return make


def test_the_devices_are_listed_and_a_missing_driver_stack_gives_none(wire, monkeypatch):
    s = wire(FakeSource([]))
    assert s.list_devices() == ["KODAK Scanner: S2000f/S3000"]
    monkeypatch.setattr(twain_source, "_twain", lambda: None)
    assert TWAINSource().list_devices() == []
    with pytest.raises(ScannerUnavailable):
        TWAINSource().connect("anything")


def test_a_source_that_will_not_open_gives_a_plain_message(wire):
    wire(FakeSource([]))                                            # installs the fake driver stack
    with pytest.raises(ScannerUnavailable, match="TWAIN driver could not be opened"):
        TWAINSource().connect("KODAK Scanner: S2000")               # not one of its sources: cannot open
    assert NO_DRIVER_MESSAGE.startswith("The scanner's TWAIN driver")


def test_every_side_comes_back_in_order_as_a_png_with_progress(wire):
    s = wire(FakeSource([10, 20, 30, 40]))
    seen = []
    pages = s.acquire(200, "grayscale", True, on_page=lambda data, n: seen.append(n))
    assert len(pages) == 4 and seen == [1, 2, 3, 4]
    assert all(p.startswith(b"\x89PNG") for p in pages)
    shades = [int(cv2.imdecode(np.frombuffer(p, np.uint8), 0)[0, 0]) for p in pages]
    assert shades == [10, 20, 30, 40]                          # front, back, front, back: in order


def test_the_scan_is_set_up_two_sided_with_the_feeder_and_all_pages(wire):
    src = FakeSource([1])
    s = wire(src)
    s.acquire(300, "color", True)
    caps = src.caps
    assert caps[FAKE.ICAP_XRESOLUTION] == caps[FAKE.ICAP_YRESOLUTION] == 300.0
    assert caps[FAKE.ICAP_PIXELTYPE] == FAKE.TWPT_RGB
    assert caps[FAKE.CAP_FEEDERENABLED] is True and caps[FAKE.CAP_DUPLEXENABLED] is True
    assert caps[FAKE.CAP_XFERCOUNT] == -1                      # everything in the feeder
    assert s.duplex_applied is True


def test_one_sided_scanning_switches_two_sided_off(wire):
    src = FakeSource([1])
    s = wire(src)
    s.acquire(200, "bw", False)
    assert src.caps[FAKE.CAP_DUPLEXENABLED] is False and s.duplex_applied is None
    assert src.caps[FAKE.ICAP_PIXELTYPE] == FAKE.TWPT_BW


def test_a_driver_that_refuses_two_sided_still_scans_and_says_so(wire):
    s = wire(FakeSource([5, 6], refuse={FAKE.CAP_DUPLEXENABLED}))
    assert len(s.acquire(200, "grayscale", True)) == 2
    assert s.duplex_applied is False


def test_pages_already_scanned_are_kept_when_a_later_sheet_jams(wire):
    s = wire(FakeSource([1, 2, 3, 4], fail_after=2))
    pages = s.acquire(200, "grayscale", True)
    assert len(pages) == 2                                     # not lost, not an exception


def test_a_jam_before_any_page_is_reported_as_an_empty_feeder(wire):
    s = wire(FakeSource([]))
    with pytest.raises(ScannerUnavailable, match="feeder is empty"):
        s.acquire(200, "grayscale", True)
    assert EMPTY_MESSAGE.startswith("The document feeder is empty")


def test_a_busy_scanner_gets_the_busy_message(wire):
    s = wire(FakeSource([], raise_first=SequenceError()))
    with pytest.raises(ScannerUnavailable, match="in use by another program"):
        s.acquire(200, "grayscale", True)
    assert BUSY_MESSAGE.startswith("The scanner is in use")


def test_the_scan_stops_after_max_pages(wire):
    s = wire(FakeSource(list(range(10))))
    pages = s.acquire(200, "grayscale", True, max_pages=3)
    assert len(pages) == 3


def test_a_driver_that_goes_quiet_is_stopped_cleanly(wire):
    s = wire(FakeSource([1], pause=0.4))
    s.acquire(200, "grayscale", True, idle_timeout=0.05)
    assert any(call[0] == 4242 and call[1] == WM_QUIT for call in s.posted)   # WM_QUIT to the scanning thread


def test_a_normal_scan_never_posts_a_quit(wire):
    s = wire(FakeSource([1, 2]))
    s.acquire(200, "grayscale", True, idle_timeout=30)
    assert s.posted == []


def test_disconnect_closes_the_driver_and_the_manager(wire):
    src = FakeSource([1])
    s = wire(src)
    s.disconnect()
    assert src.destroyed and s.manager.destroyed
    with pytest.raises(ScannerUnavailable):
        s.acquire(200, "grayscale", True)


def test_capabilities_report_two_sided_support(wire):
    assert wire(FakeSource([])).get_capabilities().supports_duplex is True


# -- choosing TWAIN for a scanner that has its own driver -----------------------------------------
def test_the_kodak_s3060_is_matched_to_its_driver_and_not_to_the_s2000_one():
    assert same_family("KODAK S3060 Scanner", "KODAK Scanner: S2000f/S3000")
    assert not same_family("KODAK S3060 Scanner", "KODAK Scanner: S2000")
    assert not same_family("EPSON DS-530", "KODAK Scanner: S2000f/S3000")
    assert not same_family("no model here", "KODAK Scanner: S2000f/S3000")


def test_twain_is_preferred_when_the_same_scanner_has_both():
    devices = {"twain": ["KODAK Scanner: S2000", "KODAK Scanner: S2000f/S3000"],
               "wia": ["KODAK S3060 Scanner"]}
    assert preferred_choice(devices) == ("twain", "KODAK Scanner: S2000f/S3000")
    assert preferred_choice({"wia": ["KODAK S3060 Scanner"]}) is None
    assert preferred_choice({"twain": ["Some Scanner"], "wia": ["Other Scanner"]}) is None


def test_the_wizard_preselects_the_twain_entry_and_shows_no_wia_warning(qtbot):
    from ui.scan_wizard import SourcePage
    devices = {"twain": ["KODAK Scanner: S2000", "KODAK Scanner: S2000f/S3000"],
               "wia": ["KODAK S3060 Scanner"]}
    page = SourcePage(devices=devices, settings=lambda: {"dpi": 300, "color": "grayscale",
                                                         "duplex": True})
    qtbot.addWidget(page)
    page.show()
    assert page.source.currentData() == ("twain", "KODAK Scanner: S2000f/S3000")
    assert page.duplex_hint.isHidden()                          # the WIA limitation does not apply
    page.source.setCurrentIndex(next(i for i in range(page.source.count())
                                     if page.source.itemData(i) == ("wia", "KODAK S3060 Scanner")))
    assert not page.duplex_hint.isHidden()                      # ... it does if WIA is chosen


def test_the_wizard_still_defaults_to_file_import_without_a_twain_match(qtbot):
    from ui.scan_wizard import SourcePage
    page = SourcePage(devices={"wia": ["KODAK S3060 Scanner"]})
    qtbot.addWidget(page)
    assert page.source.currentData() is None
