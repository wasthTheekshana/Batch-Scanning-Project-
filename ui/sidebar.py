"""Sidebar navigation: a dark rail of icon + label buttons on the left, the page title
and the current page on the right. It offers the same methods as QTabWidget
(addTab, widget, tabText, setCurrentWidget, currentChanged ...) so the rest of the
application does not care which one it is talking to.

Below COMPACT_BELOW pixels of window width the rail folds to icons only (labels move
to tooltips) so the work area keeps the room.
"""
from __future__ import annotations

from PyQt6.QtCore import QSize, Qt, pyqtSignal
from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import (QButtonGroup, QFrame, QHBoxLayout, QLabel, QPushButton,
                             QScrollArea, QSizePolicy, QStackedWidget, QVBoxLayout, QWidget)

COMPACT_BELOW = 1120
RAIL_WIDE, RAIL_COMPACT = 218, 64


def _restyle(widget: QWidget) -> None:
    """Re-apply the stylesheet after a dynamic property changed."""
    style = widget.style()
    if style is not None:
        style.unpolish(widget)
        style.polish(widget)


class SideNav(QWidget):
    currentChanged = pyqtSignal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._texts: list[str] = []
        self._pages: list[QWidget] = []
        self._buttons: list[QPushButton] = []
        self._compact = False
        self._icon_size = QSize(18, 18)

        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self.rail = QFrame()
        self.rail.setObjectName("Rail")
        self.rail.setFixedWidth(RAIL_WIDE)
        rail_layout = QVBoxLayout(self.rail)
        rail_layout.setContentsMargins(12, 14, 12, 12)
        rail_layout.setSpacing(4)
        self._brand_slot = QVBoxLayout()
        rail_layout.addLayout(self._brand_slot)
        rail_layout.addSpacing(10)
        self._nav = QVBoxLayout()
        self._nav.setSpacing(3)
        rail_layout.addLayout(self._nav)
        rail_layout.addStretch(1)
        self._footer_slot = QVBoxLayout()
        rail_layout.addLayout(self._footer_slot)
        root.addWidget(self.rail)

        content = QVBoxLayout()
        content.setContentsMargins(0, 0, 0, 0)
        content.setSpacing(0)
        self.title_bar = QFrame()
        self.title_bar.setObjectName("PageBar")
        bar = QHBoxLayout(self.title_bar)
        bar.setContentsMargins(24, 12, 24, 12)
        self.page_title = QLabel("")
        self.page_title.setObjectName("PageTitle")
        bar.addWidget(self.page_title)
        bar.addStretch(1)
        self.bar_actions = QHBoxLayout()
        bar.addLayout(self.bar_actions)
        content.addWidget(self.title_bar)
        self.stack = QStackedWidget()
        self.stack.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.stack.setContentsMargins(14, 12, 14, 8)      # air around every page
        content.addWidget(self.stack, stretch=1)
        root.addLayout(content, stretch=1)

        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        self._group.idClicked.connect(self.setCurrentIndex)

    # -- the parts the window fills in --------------------------------------------
    def set_brand(self, widget: QWidget) -> None:
        self._brand_slot.addWidget(widget)

    def set_footer(self, widget: QWidget) -> None:
        self._footer_slot.addWidget(widget)

    # -- QTabWidget-compatible API ---------------------------------------------------
    def setIconSize(self, size: QSize) -> None:
        self._icon_size = size
        for b in self._buttons:
            b.setIconSize(size)

    def setDocumentMode(self, _on: bool) -> None:      # tab-bar styling: not applicable
        pass

    def addTab(self, widget: QWidget, icon_or_text, text: str | None = None) -> int:
        icon = icon_or_text if isinstance(icon_or_text, QIcon) else QIcon()
        label = text if text is not None else str(icon_or_text)
        button = QPushButton(label)
        button.setObjectName("NavButton")
        button.setCheckable(True)
        button.setIcon(icon)
        button.setIconSize(self._icon_size)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.setToolTip(label)
        # Each page sits in a scroll area: a page that needs more room than the window has (a small
        # screen, wider fonts) scrolls instead of forcing the window wider than the screen.
        area = QScrollArea()
        area.setWidgetResizable(True)
        area.setFrameShape(QFrame.Shape.NoFrame)
        area.setWidget(widget)
        self._pages.append(widget)
        index = self.stack.addWidget(area)
        self._group.addButton(button, index)
        self._nav.addWidget(button)
        self._buttons.append(button)
        self._texts.append(label)
        if index == 0:
            self.setCurrentIndex(0)
        self._apply_compact()
        return index

    def count(self) -> int:
        return self.stack.count()

    def widget(self, index: int) -> QWidget | None:
        """The page itself (not the scroll area it sits in)."""
        return self._pages[index] if 0 <= index < len(self._pages) else None

    def tabText(self, index: int) -> str:
        return self._texts[index]

    def indexOf(self, widget: QWidget) -> int:
        return self._pages.index(widget) if widget in self._pages else -1

    def currentIndex(self) -> int:
        return self.stack.currentIndex()

    def currentWidget(self) -> QWidget | None:
        return self.widget(self.stack.currentIndex())

    def setCurrentWidget(self, widget: QWidget) -> None:
        self.setCurrentIndex(self.indexOf(widget))

    def setCurrentIndex(self, index: int) -> None:
        if not 0 <= index < self.stack.count():
            return
        changed = index != self.stack.currentIndex()
        self.stack.setCurrentIndex(index)
        self._buttons[index].setChecked(True)
        self.page_title.setText(self._texts[index])
        if changed:
            self.currentChanged.emit(index)

    # -- compact rail --------------------------------------------------------------------
    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        compact = self.width() < COMPACT_BELOW
        if compact != self._compact:
            self._compact = compact
            self._apply_compact()

    def _apply_compact(self) -> None:
        self.rail.setFixedWidth(RAIL_COMPACT if self._compact else RAIL_WIDE)
        for button, text in zip(self._buttons, self._texts):
            button.setText("" if self._compact else text)
            button.setProperty("compact", self._compact)
            _restyle(button)
        self.rail.setProperty("compact", self._compact)
        _restyle(self.rail)
        for slot in (self._brand_slot, self._footer_slot):
            for i in range(slot.count()):
                w = slot.itemAt(i).widget()
                if w is not None and hasattr(w, "set_compact"):
                    w.set_compact(self._compact)

    def minimumSizeHint(self) -> QSize:
        """As small as the page allows with the rail folded: the rail folds by itself
        whenever the window is narrower than COMPACT_BELOW."""
        page = self.stack.minimumSizeHint()
        margins = self.stack.contentsMargins()
        width = RAIL_COMPACT + page.width() + margins.left() + margins.right()
        height = max(self.rail.minimumSizeHint().height(),
                     self.title_bar.sizeHint().height() + page.height()
                     + margins.top() + margins.bottom())
        return QSize(width, height)

    @property
    def compact(self) -> bool:
        return self._compact
