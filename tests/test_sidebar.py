"""The sidebar navigation behaves like the tab widget it replaced, and folds to icons on a narrow window."""
from PyQt6.QtWidgets import QLabel, QWidget

from ui.sidebar import COMPACT_BELOW, RAIL_COMPACT, RAIL_WIDE, SideNav


def make(qtbot, n=3):
    nav = SideNav()
    qtbot.addWidget(nav)
    pages = [QLabel(f"page {i}") for i in range(n)]
    for i, p in enumerate(pages):
        nav.addTab(p, f"Tab {i}")
    return nav, pages


def test_it_offers_the_tab_widget_methods_the_application_uses(qtbot):
    nav, pages = make(qtbot)
    seen = []
    nav.currentChanged.connect(seen.append)
    assert nav.count() == 3 and nav.tabText(1) == "Tab 1" and nav.widget(2) is pages[2]
    assert nav.currentWidget() is pages[0] and nav.page_title.text() == "Tab 0"
    nav.setCurrentWidget(pages[2])
    assert nav.currentIndex() == 2 and nav.page_title.text() == "Tab 2" and seen == [2]
    nav.setCurrentIndex(1)
    assert nav.indexOf(pages[1]) == 1 and seen == [2, 1]


def test_clicking_a_button_opens_its_page(qtbot):
    nav, pages = make(qtbot)
    nav._buttons[2].click()
    assert nav.currentWidget() is pages[2] and nav._buttons[2].isChecked()


def test_the_rail_folds_to_icons_when_narrow_and_opens_when_wide(qtbot):
    nav, _ = make(qtbot)
    nav.resize(COMPACT_BELOW - 100, 600)
    nav.show()
    assert nav.compact and nav.rail.width() == RAIL_COMPACT and nav._buttons[0].text() == ""
    assert nav._buttons[0].toolTip() == "Tab 0"                     # the label stays reachable
    nav.resize(COMPACT_BELOW + 300, 600)
    assert not nav.compact and nav.rail.width() == RAIL_WIDE and nav._buttons[0].text() == "Tab 0"


def test_the_minimum_size_counts_the_folded_rail(qtbot):
    nav, _ = make(qtbot)
    page = QWidget()
    page.setMinimumWidth(700)
    nav.addTab(page, "Wide")
    assert nav.minimumSizeHint().width() < RAIL_WIDE + 700 + 40
