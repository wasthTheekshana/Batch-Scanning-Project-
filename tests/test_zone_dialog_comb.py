import numpy as np

from tests.test_comb import comb_strip
from ui.zone_dialog import ZoneDialog


def region_dialog(qtbot, zone=None, full_page=False):
    dlg = ZoneDialog(zone, full_page=full_page)
    qtbot.addWidget(dlg)
    return dlg


def with_region(dlg, name="nic_number"):
    dlg.name.setText(name)
    for box, value in ((dlg.x, 5), (dlg.y, 6), (dlg.w, 300), (dlg.h, 40)):
        box.setValue(value)


def test_a_plain_zone_has_no_comb_keys(qtbot):
    dlg = region_dialog(qtbot)
    with_region(dlg)
    assert "comb" not in dlg.build_zone() and "charset" not in dlg.build_zone()
    assert not dlg.charset.isEnabled()


def test_a_boxed_character_zone_records_comb_and_the_allowed_characters(qtbot):
    dlg = region_dialog(qtbot)
    with_region(dlg)
    dlg.comb.setChecked(True)
    assert dlg.charset.isEnabled()
    dlg.charset.setCurrentIndex(dlg.charset.findData("nic"))
    zone = dlg.build_zone()
    assert zone["comb"] is True and zone["charset"] == "nic"


def test_editing_a_comb_zone_shows_its_settings(qtbot):
    zone = {"name": "acct", "x": 1, "y": 2, "w": 3, "h": 4, "comb": True,
            "charset": "alnum"}
    dlg = region_dialog(qtbot, zone)
    assert dlg.comb.isChecked() and dlg.charset.currentData() == "alnum"
    assert dlg.build_zone()["charset"] == "alnum"


def test_text_pattern_mode_has_no_boxed_character_option(qtbot):
    dlg = region_dialog(qtbot, full_page=True)
    assert not dlg.comb.isEnabled() and not dlg.comb.isChecked()
    dlg.name.setText("n")
    dlg.regex.setText(r"(\d+)")
    assert "comb" not in dlg.build_zone()


def test_test_zone_reads_boxed_characters_one_per_cell(qtbot):
    dlg = region_dialog(qtbot)
    strip = comb_strip("0012345678")
    page = np.full((200, 600), 255, np.uint8)
    page[40:40 + strip.shape[0], 10:10 + strip.shape[1]] = strip
    dlg._sample = page
    dlg.name.setText("account_number")
    for box, value in ((dlg.x, 0), (dlg.y, 30), (dlg.w, 590), (dlg.h, 90)):
        box.setValue(value)
    dlg.comb.setChecked(True)
    dlg.charset.setCurrentIndex(dlg.charset.findData("digits"))
    dlg.test_zone()
    assert "0012345678" in dlg.result.text()


def test_lines_of_text_are_saved_only_when_set(qtbot):
    dlg = region_dialog(qtbot)
    with_region(dlg, "address")
    assert "lines" not in dlg.build_zone()
    dlg.lines.setValue(2)
    assert dlg.build_zone()["lines"] == 2
    again = region_dialog(qtbot, dlg.build_zone())
    assert again.lines.value() == 2


def test_lines_do_not_apply_in_text_pattern_mode(qtbot):
    dlg = region_dialog(qtbot, full_page=True)
    assert not dlg.lines.isEnabled()
