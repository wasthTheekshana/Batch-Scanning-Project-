"""WIA document-feeder scanning, against fake COM objects."""
import cv2
import numpy as np
import pytest

from scanning import wia_source
from scanning.base import ScannerUnavailable
from scanning.wia_source import WIASource


def sheet(shade: int) -> bytes:
    ok, buf = cv2.imencode(".png", np.full((8, 8), shade, np.uint8))
    assert ok
    return buf.tobytes()


P1, P2, P3, X = sheet(1), sheet(2), sheet(3), sheet(9)


class ComErr(Exception):
    """Stands in for pywintypes.com_error (carries .hresult)."""

    def __init__(self, scode):
        # pywin32 shape: DISP_E_EXCEPTION, text, excepinfo (scode last), None
        scode = scode - (1 << 32) if scode >= 1 << 31 else scode
        super().__init__(-2147352567, "Exception occurred.",
                         (0, None, "device text", None, 0, scode), None)


class Prop:
    def __init__(self, pid, value=0):
        self.PropertyID = pid
        self.Value = value


class Image:
    def __init__(self, data):
        self._data = data

    def SaveFile(self, path):
        with open(path, "wb") as f:
            f.write(self._data)


class Item:
    def __init__(self, sheets, error_after=None):
        self.Properties = [Prop(int(p)) for p in (
            "6147", "6148", "6151", "6152", "4103", "4104")]
        self._sheets = list(sheets)
        self._error_after = error_after
        self.transfers = 0

    def Transfer(self, fmt):
        assert fmt == wia_source.WIA_FORMAT_PNG
        self.transfers += 1
        if self._error_after is not None and self.transfers > self._error_after:
            raise ComErr(0x80210002)          # paper jam
        if not self._sheets:
            raise ComErr(0x80210003)          # feeder empty
        return Image(self._sheets.pop(0))

    def value(self, pid):
        return next(p.Value for p in self.Properties if str(p.PropertyID) == pid)


class Device:
    def __init__(self, item):
        self.Properties = [Prop(3088), Prop(3096, 5)]
        self.Items = lambda n: item
        self.item = item

    def value(self, pid):
        return next(p.Value for p in self.Properties if str(p.PropertyID) == pid)


def source(item, **kw):
    s = WIASource()
    s._device = Device(item, **kw)
    return s


def test_every_sheet_in_the_feeder_is_returned_in_order():
    item = Item([P1, P2, P3])
    seen = []
    pages = source(item).acquire(300, "grayscale", True,
                                 on_page=lambda data, n: seen.append((data, n)))
    assert pages == [P1, P2, P3]
    assert seen == [(P1, 1), (P2, 2), (P3, 3)]
    assert item.transfers == 4                 # the 4th call found it empty


def test_settings_are_written_to_the_device_and_the_item():
    item = Item([X])
    src = source(item)
    src.acquire(300, "color", True)
    assert src._device.value("3088") == wia_source.FEEDER | wia_source.DUPLEX
    assert src._device.value("3096") == 1      # one sheet per Transfer
    assert item.value("6147") == item.value("6148") == 300
    assert item.value("6151") == 2480            # 8.27 in at 300 dpi, rounded to even
    assert item.value("6152") == 3508            # 11.69 in at 300 dpi, rounded to even
    assert (item.value("4103"), item.value("4104")) == (3, 24)


@pytest.mark.parametrize("mode,expected", [("bw", (0, 1)), ("grayscale", (2, 8))])
def test_colour_modes_map_to_wia_data_types(mode, expected):
    item = Item([X])
    source(item).acquire(200, mode, False)
    assert (item.value("4103"), item.value("4104")) == expected


def test_simplex_asks_for_the_feeder_only():
    src = source(Item([X]))
    src.acquire(200, "grayscale", False)
    assert src._device.value("3088") == wia_source.FEEDER


def test_empty_feeder_is_a_clear_error_not_an_empty_list():
    with pytest.raises(ScannerUnavailable, match="feeder is empty"):
        source(Item([])).acquire(300, "grayscale", True)


def test_a_jam_after_some_pages_reports_the_jam_and_keeps_the_pages_seen():
    item = Item([P1, P2, P3], error_after=2)
    got = []
    with pytest.raises(ScannerUnavailable, match="Paper jam"):
        source(item).acquire(300, "grayscale", False,
                             on_page=lambda data, n: got.append(data))
    assert got == [P1, P2]


def test_unknown_device_error_is_wrapped():
    item = Item([X])
    item.Transfer = lambda fmt: (_ for _ in ()).throw(RuntimeError("boom"))
    with pytest.raises(ScannerUnavailable, match="WIA acquire failed: boom"):
        source(item).acquire(300, "grayscale", False)


def test_runaway_feeder_is_capped():
    item = Item([X] * 10)
    assert len(source(item).acquire(300, "grayscale", False, max_pages=4)) == 4


def test_requires_a_connected_device():
    with pytest.raises(ScannerUnavailable):
        WIASource().acquire(300, "grayscale", False)


class RangeProp:
    SubType = 1

    def __init__(self, low, high, step):
        self.SubTypeMin, self.SubTypeMax, self.SubTypeStep = low, high, step


def test_scan_area_snaps_to_the_devices_own_step():
    from scanning.wia_source import _snap
    # Kodak S2050 at 300 dpi: 300..2580 in steps of 3 (2480 is not valid)
    assert _snap(RangeProp(300, 2580, 3), 2481.0) == 2481
    assert _snap(RangeProp(300, 2580, 3), 2480.0) == 2481
    # at 200 dpi: steps of 2
    assert _snap(RangeProp(200, 1720, 2), 1654.0) == 1654
    # never outside the device's range
    assert _snap(RangeProp(300, 2580, 3), 9999) == 2580
    assert _snap(RangeProp(300, 2580, 3), 10) == 300


def test_without_range_information_pixels_are_even():
    from scanning.wia_source import _snap
    assert _snap(Prop(1), 2481.0) % 2 == 0


def test_bmp_data_from_the_driver_is_returned_as_a_real_png():
    page = np.full((40, 30), 77, np.uint8)
    ok, bmp = cv2.imencode(".bmp", page)
    assert ok and bmp.tobytes().startswith(b"BM")
    got = source(Item([bmp.tobytes()])).acquire(200, "grayscale", False)
    assert got[0].startswith(wia_source._PNG_SIGNATURE)
    assert (cv2.imdecode(np.frombuffer(got[0], np.uint8),
                         cv2.IMREAD_GRAYSCALE) == 77).all()


def test_garbage_image_data_is_a_clear_error():
    with pytest.raises(ScannerUnavailable, match="unreadable image"):
        source(Item([b"not an image"])).acquire(200, "grayscale", False)


class StrictProp(Prop):
    SubType = 1
    SubTypeMin, SubTypeMax, SubTypeStep = 300, 4200, 3

    """Rejects a scan-area value until the resolution has been raised, like
    the real driver (valid extent depends on the current dpi)."""

    def __init__(self, pid, owner):
        self._pid, self._owner, self._value = pid, owner, 0
        self.PropertyID = pid

    @property
    def Value(self):
        return self._value

    @Value.setter
    def Value(self, v):
        if self._pid in (6151, 6152) and not self._owner.dpi_raised:
            raise ComErr(0x80070057)           # parameter is incorrect
        if self._pid == 6151 and (v - 300) % 3:
            raise ComErr(0x80070057)           # 300 dpi: steps of 3
        if self._pid == 6147:
            self._owner.dpi_raised = v >= 300
        self._value = v


def test_scan_area_is_written_after_the_resolution_whatever_the_device_order():
    item = Item([X])
    item.dpi_raised = False
    # the device lists the extents BEFORE the resolution
    item.Properties = [StrictProp(int(pid), item) for pid in (
        "6151", "6152", "6147", "6148", "4103", "4104")]
    pages = source(item).acquire(300, "grayscale", False)
    assert pages == [X]
    assert item.value("6151") == 2481


class FakeInfo:
    Type = 1

    def __init__(self, name, device):
        self._name, self._device = name, device

    def Properties(self, key):
        assert key == "Name"
        return Prop(0, self._name)

    def Connect(self):
        return self._device


class FakeManager:
    def __init__(self, infos):
        self.DeviceInfos = infos


def test_devices_are_listed_and_connect_finds_them_by_name(monkeypatch):
    device = Device(Item([X]))
    manager = FakeManager([FakeInfo("KODAK S2050 Scanner", device)])
    monkeypatch.setattr(wia_source, "_wia_device_manager", lambda: manager)
    src = WIASource()
    assert src.list_devices() == ["KODAK S2050 Scanner"]
    src.connect("KODAK S2050 Scanner")
    assert src.acquire(200, "grayscale", False) == [X]
    with pytest.raises(ScannerUnavailable, match="not found"):
        src.connect("Other Scanner")


def test_no_wia_stack_means_no_devices(monkeypatch):
    monkeypatch.setattr(wia_source, "_wia_device_manager", lambda: None)
    assert WIASource().list_devices() == []


# -- the scanner is busy / locked by something else ---------------------------------------
class BusyInfo(FakeInfo):
    """Connect() fails with the given HRESULT for the first `fail` calls."""

    def __init__(self, name, device, code, fail):
        super().__init__(name, device)
        self.code, self.fail, self.calls = code, fail, 0

    def Connect(self):
        self.calls += 1
        if self.calls <= self.fail:
            raise ComErr(self.code)
        return self._device


def busy_source(monkeypatch, code, fail):
    info = BusyInfo("KODAK S2050 Scanner", Device(Item([X])), code, fail)
    monkeypatch.setattr(wia_source, "_wia_device_manager",
                        lambda: FakeManager([info]))
    waits = []
    monkeypatch.setattr(wia_source.time, "sleep", lambda s: waits.append(s))
    return info, waits


def test_a_lock_that_clears_within_a_few_seconds_is_waited_out(monkeypatch):
    info, waits = busy_source(monkeypatch, 0x80070021, fail=2)
    src = WIASource()
    src.connect("KODAK S2050 Scanner")                  # no error raised
    assert info.calls == 3 and waits == [wia_source.CONNECT_WAIT] * 2
    assert src.acquire(200, "grayscale", False) == [X]


def test_a_lock_that_stays_gives_plain_instructions_not_a_windows_error(monkeypatch):
    info, waits = busy_source(monkeypatch, 0x80070021, fail=99)
    with pytest.raises(ScannerUnavailable) as caught:
        WIASource().connect("KODAK S2050 Scanner")
    message = str(caught.value)
    assert message == wia_source.BUSY_MESSAGE
    assert "another program" in message and "Smart Touch" in message
    assert "0x" not in message and "locked a portion" not in message
    assert info.calls == wia_source.CONNECT_TRIES
    assert len(waits) == wia_source.CONNECT_TRIES - 1


def test_the_wia_busy_code_is_treated_the_same_way(monkeypatch):
    busy_source(monkeypatch, 0x80210006, fail=99)
    with pytest.raises(ScannerUnavailable, match="another program"):
        WIASource().connect("KODAK S2050 Scanner")


def test_other_connect_errors_are_not_retried_and_are_described(monkeypatch):
    info, waits = busy_source(monkeypatch, 0x80210005, fail=99)    # offline
    with pytest.raises(ScannerUnavailable, match="offline"):
        WIASource().connect("KODAK S2050 Scanner")
    assert info.calls == 1 and waits == []


def test_an_unknown_device_is_still_reported_without_waiting(monkeypatch):
    info, waits = busy_source(monkeypatch, 0x80070021, fail=0)
    with pytest.raises(ScannerUnavailable, match="not found"):
        WIASource().connect("Some Other Scanner")
    assert waits == []
