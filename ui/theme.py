"""Application theme — "paper & ink instrument".

A calm, professional look for all-day production use: ink-dark identity
band, cool paper-gray workspace, white document cards, and a scanner-lamp
teal accent. QC grade colors (pass/warn/fail) are the app-wide status
language. Applied once via apply_theme(app); every dialog inherits.
"""
from __future__ import annotations

from PyQt6.QtGui import QColor, QFont, QIcon, QPalette

# ---- design tokens -----------------------------------------------------------
INK = "#16232E"          # identity band, headings
INK_SOFT = "#233647"     # hover on ink
PAPER = "#F4F6F9"        # workspace background
SURFACE = "#FFFFFF"      # cards, inputs, tables
LINE = "#E3E8EF"         # hairline borders
TEXT = "#1C2733"         # primary text
TEXT_MUTED = "#64748B"   # secondary text
ACCENT = "#0E7C66"       # scanner-lamp teal — primary actions
ACCENT_HOVER = "#0A9377"
ACCENT_DOWN = "#0A5F4F"
PASS = "#1E8E5A"
WARN = "#B7791F"
FAIL = "#C0392B"
FAIL_HOVER = "#A93226"

STATUS_COLORS = {"PASS": PASS, "WARN": WARN, "FAIL": FAIL,
                 "auto_approved": PASS, "corrected": PASS,
                 "pending_qc": WARN, "rejected": FAIL,
                 "delivered_local": PASS, "delivered_sftp": PASS,
                 "burned_to_disc": PASS, "pending": WARN}


def icon(name: str, color: str = TEXT_MUTED) -> QIcon:
    """Font Awesome icon (offline via qtawesome); empty icon if the
    library is unavailable so the UI never breaks."""
    try:
        import qtawesome as qta
        return qta.icon(name, color=color)
    except Exception:
        return QIcon()


def nav_icon(name: str) -> QIcon:
    """Sidebar icon: pale on the dark rail, white when its page is the current one."""
    try:
        import qtawesome as qta
        out = QIcon()
        out.addPixmap(qta.icon(name, color="#A9B8C6").pixmap(36, 36), QIcon.Mode.Normal, QIcon.State.Off)
        out.addPixmap(qta.icon(name, color="#FFFFFF").pixmap(36, 36), QIcon.Mode.Normal, QIcon.State.On)
        return out
    except Exception:
        return QIcon()


GLYPHS = ("check", "down", "up", "dot")


def _make_assets() -> dict[str, str]:
    """Small glyphs the stylesheet needs (check mark, arrows, radio dot), drawn once
    into a temp folder: QSS can only reference image files."""
    import tempfile
    from pathlib import Path

    from PyQt6.QtCore import QPointF, Qt
    from PyQt6.QtGui import QPainter, QPen, QPixmap
    folder = Path(tempfile.gettempdir()) / "dokscanner_theme"
    folder.mkdir(exist_ok=True)

    def draw(name: str, painter_fn, color: str) -> str:
        pm = QPixmap(36, 36)
        pm.fill(Qt.GlobalColor.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        pen = QPen(QColor(color), 4.5)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        p.setPen(pen)
        painter_fn(p)
        p.end()
        path = folder / f"{name}.png"
        pm.save(str(path))
        return path.as_posix()

    def check(p):
        p.drawPolyline([QPointF(9, 19), QPointF(15, 25), QPointF(27, 11)])

    def down(p):
        p.drawPolyline([QPointF(10, 14), QPointF(18, 22), QPointF(26, 14)])

    def up(p):
        p.drawPolyline([QPointF(10, 22), QPointF(18, 14), QPointF(26, 22)])

    def dot(p):
        p.setBrush(QColor("white"))
        p.drawEllipse(QPointF(18, 18), 5, 5)

    return {"check": draw("check", check, "#FFFFFF"), "down": draw("down", down, TEXT_MUTED),
            "up": draw("up", up, TEXT_MUTED), "dot": draw("dot", dot, "#FFFFFF")}


def build_qss(a: dict[str, str]) -> str:
    return f"""
* {{
    font-family: "Segoe UI Variable", "Segoe UI", sans-serif;
    font-size: 10pt;
    color: {TEXT};
}}
QMainWindow, QDialog, QWizard {{ background: {PAPER}; }}
QWidget {{ selection-background-color: {ACCENT}; selection-color: white; }}
QLabel {{ background: transparent; }}

/* ---- sidebar ------------------------------------------------------------ */
#Rail {{
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #14222F, stop:1 #0E1822);
    border: none;
}}
#Rail QLabel {{ background: transparent; }}
#HeaderTitle {{ font-size: 15pt; font-weight: 700; color: #FFFFFF; }}
#HeaderSub {{ color: #7F93A5; font-size: 8.5pt; }}
#NavButton {{
    background: transparent;
    color: #B4C2CF;
    border: none;
    border-radius: 9px;
    padding: 10px 12px;
    text-align: left;
    font-weight: 500;
}}
#NavButton:hover {{ background: rgba(255,255,255,0.07); color: #FFFFFF; }}
#NavButton:checked {{ background: {ACCENT}; color: #FFFFFF; font-weight: 600; }}
#NavButton[compact="true"] {{ text-align: center; padding: 11px 0; }}
#UserChip {{
    background: rgba(255,255,255,0.07);
    color: #DCE5EC;
    border-radius: 9px;
    padding: 7px 10px;
    font-size: 9pt;
}}
#SignOut {{
    background: transparent;
    color: #B4C2CF;
    border: 1px solid #2B3D4D;
    border-radius: 9px;
    padding: 7px 10px;
}}
#SignOut:hover {{ background: rgba(255,255,255,0.08); color: #FFFFFF; border-color: #3C5367; }}

/* ---- page title bar ----------------------------------------------------- */
#PageBar {{ background: {SURFACE}; border: none; border-bottom: 1px solid {LINE}; }}
#PageTitle {{ font-size: 15pt; font-weight: 650; color: {INK}; }}

/* ---- inner tabs (Admin, Custody): underlined, not boxed --------------------- */
QTabWidget::pane {{ border: none; background: transparent; padding: 0; }}
QTabBar {{ background: transparent; }}
QTabBar::tab {{
    background: transparent;
    color: {TEXT_MUTED};
    padding: 8px 14px;
    margin-right: 4px;
    border: none;
    border-bottom: 2px solid transparent;
    font-weight: 500;
}}
QTabBar::tab:hover {{ color: {TEXT}; }}
QTabBar::tab:selected {{ color: {ACCENT}; border-bottom: 2px solid {ACCENT}; font-weight: 600; }}
QTabBar QToolButton {{ background: {SURFACE}; border: 1px solid {LINE}; border-radius: 6px; }}

/* ---- cards ---------------------------------------------------------------- */
QGroupBox {{
    background: {SURFACE};
    border: 1px solid {LINE};
    border-radius: 12px;
    margin-top: 16px;
    padding: 14px 12px 12px 12px;
    font-weight: 600;
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    left: 12px;
    padding: 0 6px;
    color: {TEXT_MUTED};
    font-size: 9pt;
    font-weight: 600;
}}

/* ---- buttons ------------------------------------------------------------------ */
QPushButton {{
    background: {SURFACE};
    border: 1px solid {LINE};
    border-radius: 8px;
    padding: 7px 16px;
    min-height: 20px;
    font-weight: 500;
}}
QPushButton:hover {{ border-color: {ACCENT}; color: {ACCENT}; background: #F4FBF9; }}
QPushButton:pressed {{ background: #E5F2EF; }}
QPushButton:focus {{ border: 1px solid {ACCENT}; }}
QPushButton:disabled {{ color: #A8B3BF; background: #F1F4F7; border-color: #E8ECF1; }}
QPushButton[class="primary"] {{ background: {ACCENT}; color: white; border: 1px solid {ACCENT}; font-weight: 600; }}
QPushButton[class="primary"]:hover {{ background: {ACCENT_HOVER}; color: white; border-color: {ACCENT_HOVER}; }}
QPushButton[class="primary"]:pressed {{ background: {ACCENT_DOWN}; }}
QPushButton[class="primary"]:disabled {{ background: #A9CBC3; color: #F0F5F3; border-color: #A9CBC3; }}
QPushButton[class="danger"] {{ background: transparent; color: {FAIL}; border: 1px solid {FAIL}; }}
QPushButton[class="danger"]:hover {{ background: {FAIL}; color: white; }}
QPushButton[class="danger"]:disabled {{ color: #D5A6A0; border-color: #E5C4C0; background: transparent; }}
QToolButton {{ background: transparent; border: none; border-radius: 6px; padding: 4px; }}
QToolButton:hover {{ background: {PAPER}; }}

/* ---- inputs ------------------------------------------------------------------------ */
QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QDateEdit, QTextEdit, QPlainTextEdit {{
    background: {SURFACE};
    border: 1px solid #D3DAE3;
    border-radius: 8px;
    padding: 6px 10px;
    min-height: 20px;
}}
QLineEdit:hover, QComboBox:hover, QSpinBox:hover, QDoubleSpinBox:hover, QDateEdit:hover {{ border-color: #B5C0CD; }}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus, QDateEdit:focus,
QTextEdit:focus, QPlainTextEdit:focus {{ border: 1px solid {ACCENT}; background: #FDFFFE; }}
QLineEdit:disabled, QComboBox:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled, QDateEdit:disabled {{
    background: #F1F4F7; color: #98A4B1; }}
QComboBox {{ padding-right: 28px; }}
QComboBox::drop-down {{ border: none; width: 28px; }}
QComboBox::down-arrow {{ image: url({a['down']}); width: 12px; height: 12px; }}
QComboBox QAbstractItemView {{
    background: {SURFACE};
    border: 1px solid {LINE};
    border-radius: 8px;
    padding: 4px;
    outline: none;
    selection-background-color: #E5F2EF;
    selection-color: {ACCENT_DOWN};
}}
QSpinBox::up-button, QSpinBox::down-button, QDoubleSpinBox::up-button, QDoubleSpinBox::down-button {{
    border: none; background: transparent; width: 22px; }}
QSpinBox::up-arrow, QDoubleSpinBox::up-arrow {{ image: url({a['up']}); width: 10px; height: 10px; }}
QSpinBox::down-arrow, QDoubleSpinBox::down-arrow {{ image: url({a['down']}); width: 10px; height: 10px; }}
QDateEdit::drop-down {{ border: none; width: 26px; }}
QDateEdit::down-arrow {{ image: url({a['down']}); width: 12px; height: 12px; }}

QCheckBox, QRadioButton {{ spacing: 9px; padding: 3px 0; }}
QCheckBox::indicator {{ width: 18px; height: 18px; border: 1.5px solid #B5C0CD; border-radius: 5px; background: {SURFACE}; }}
QCheckBox::indicator:hover {{ border-color: {ACCENT}; }}
QCheckBox::indicator:checked {{ background: {ACCENT}; border-color: {ACCENT}; image: url({a['check']}); }}
QCheckBox::indicator:disabled {{ background: #F1F4F7; border-color: #D9DEE5; }}
QRadioButton::indicator {{ width: 18px; height: 18px; border: 1.5px solid #B5C0CD; border-radius: 10px; background: {SURFACE}; }}
QRadioButton::indicator:hover {{ border-color: {ACCENT}; }}
QRadioButton::indicator:checked {{ background: {ACCENT}; border-color: {ACCENT}; image: url({a['dot']}); }}

/* ---- lists & tables -------------------------------------------------------------------- */
QListWidget, QListView, QTreeView {{
    background: {SURFACE};
    border: 1px solid {LINE};
    border-radius: 12px;
    padding: 6px;
    outline: none;
}}
QListWidget::item {{ padding: 8px 10px; border-radius: 8px; margin: 1px 0; }}
QListWidget::item:hover {{ background: #F3F6F9; }}
QListWidget::item:selected {{ background: #E5F2EF; color: {ACCENT_DOWN}; }}
QTableWidget, QTableView {{
    background: {SURFACE};
    border: 1px solid {LINE};
    border-radius: 12px;
    gridline-color: transparent;
    alternate-background-color: #F9FAFC;
    outline: none;
}}
QTableWidget::item {{ padding: 5px 10px; border-bottom: 1px solid #EEF1F5; }}
QTableWidget::item:hover {{ background: #F5F8FA; }}
QTableWidget::item:selected {{ background: #E5F2EF; color: {ACCENT_DOWN}; }}
QHeaderView {{ background: transparent; }}
QHeaderView::section {{
    background: #F8FAFC;
    color: {TEXT_MUTED};
    font-weight: 600;
    font-size: 8.5pt;
    border: none;
    border-bottom: 1px solid {LINE};
    padding: 9px 10px;
}}
QTableCornerButton::section {{ background: #F8FAFC; border: none; }}

/* ---- misc ----------------------------------------------------------------------------------- */
QLabel[class="muted"] {{ color: {TEXT_MUTED}; }}
QLabel[class="h2"] {{ font-size: 12pt; font-weight: 600; }}
QSlider::groove:horizontal {{ height: 4px; background: {LINE}; border-radius: 2px; }}
QSlider::handle:horizontal {{ width: 16px; height: 16px; margin: -6px 0; background: {ACCENT}; border-radius: 8px; }}
QSlider::sub-page:horizontal {{ background: {ACCENT}; border-radius: 2px; }}
QProgressBar {{ background: {LINE}; border: none; border-radius: 5px; height: 10px; text-align: center; font-size: 8pt; }}
QProgressBar::chunk {{ background: {ACCENT}; border-radius: 5px; }}
QStatusBar {{ background: {SURFACE}; border-top: 1px solid {LINE}; color: {TEXT_MUTED}; font-size: 9pt; }}
QStatusBar::item {{ border: none; }}
QSplitter::handle {{ background: transparent; }}
QSplitter::handle:horizontal {{ width: 10px; }}
QSplitter::handle:vertical {{ height: 10px; }}
QSplitter::handle:hover {{ background: #E3E8EF; }}
QScrollBar:vertical {{ background: transparent; width: 12px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: #CBD3DC; border-radius: 4px; min-height: 36px; margin: 0 2px; }}
QScrollBar::handle:vertical:hover {{ background: #9AA7B5; }}
QScrollBar:horizontal {{ background: transparent; height: 12px; margin: 2px; }}
QScrollBar::handle:horizontal {{ background: #CBD3DC; border-radius: 4px; min-width: 36px; margin: 2px 0; }}
QScrollBar::handle:horizontal:hover {{ background: #9AA7B5; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
QToolTip {{ background: {INK}; color: white; border: none; padding: 6px 10px; border-radius: 6px; }}
QMenu {{ background: {SURFACE}; border: 1px solid {LINE}; border-radius: 10px; padding: 6px; }}
QMenu::item {{ padding: 7px 24px 7px 14px; border-radius: 6px; }}
QMenu::item:selected {{ background: #E5F2EF; color: {ACCENT_DOWN}; }}
QMessageBox {{ background: {SURFACE}; }}
QGraphicsView {{ background: #E9EDF2; border: 1px solid {LINE}; border-radius: 12px; }}
QScrollArea {{ border: none; background: transparent; }}
QScrollArea > QWidget > QWidget {{ background: transparent; }}
QWizard QLabel {{ background: transparent; }}
QDialogButtonBox QPushButton {{ min-width: 84px; }}
"""


QSS = build_qss({k: "" for k in GLYPHS})   # without glyph images (no QApplication needed)


def apply_theme(app) -> None:
    app.setStyle("Fusion")
    font = QFont("Segoe UI", 10)
    app.setFont(font)
    palette = QPalette()
    palette.setColor(QPalette.ColorRole.Window, QColor(PAPER))
    palette.setColor(QPalette.ColorRole.Base, QColor(SURFACE))
    palette.setColor(QPalette.ColorRole.Text, QColor(TEXT))
    palette.setColor(QPalette.ColorRole.WindowText, QColor(TEXT))
    palette.setColor(QPalette.ColorRole.Button, QColor(SURFACE))
    palette.setColor(QPalette.ColorRole.ButtonText, QColor(TEXT))
    palette.setColor(QPalette.ColorRole.Highlight, QColor(ACCENT))
    palette.setColor(QPalette.ColorRole.HighlightedText, QColor("white"))
    app.setPalette(palette)
    try:
        assets = _make_assets()
    except Exception:
        assets = {k: "" for k in GLYPHS}
    app.setStyleSheet(build_qss(assets))


def fit_to_screen(widget, width: int, height: int, share: float = 0.92) -> None:
    """Resize `widget` to width x height, but never larger than `share` of the
    usable area of the screen it will appear on (so a 1280x840 window still
    fits a 1366x768 laptop, and its buttons stay reachable), and centre it."""
    from PyQt6.QtGui import QGuiApplication
    screen = (widget.screen() if widget.screen() is not None
              else QGuiApplication.primaryScreen())
    if screen is None:
        widget.resize(width, height)
        return
    area = screen.availableGeometry()
    w = min(width, int(area.width() * share))
    h = min(height, int(area.height() * share))
    widget.resize(w, h)
    widget.move(area.x() + (area.width() - w) // 2,
                area.y() + (area.height() - h) // 2)


def mark(widget, css_class: str) -> None:
    """Tag a widget for QSS class selectors, e.g. mark(btn, "primary")."""
    widget.setProperty("class", css_class)
    widget.style().unpolish(widget)
    widget.style().polish(widget)


def tidy_list(widget, min_width: int = 240) -> None:
    """A work-queue list that stays readable at any width: rows are cut with an
    ellipsis (full text in the tooltip) instead of a sideways scroll bar, and the
    list never gets narrower than a document line needs."""
    from PyQt6.QtCore import Qt
    widget.setTextElideMode(Qt.TextElideMode.ElideRight)
    widget.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    widget.setMinimumWidth(min_width)
    widget.setUniformItemSizes(True)


def scroll_column(content, min_width: int = 300, max_width: int | None = None):
    """Put a tall form in its own vertical scroll area, so its rows keep their
    natural height (a layout would squash them) and the window can still be small."""
    from PyQt6.QtCore import Qt
    from PyQt6.QtWidgets import QScrollArea
    area = QScrollArea()
    area.setWidgetResizable(True)
    area.setFrameShape(QScrollArea.Shape.NoFrame)
    area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    area.setWidget(content)
    area.setMinimumWidth(min_width)
    if max_width:
        area.setMaximumWidth(max_width)
    return area


def two_lines(item, first: str, second: str) -> None:
    """A list row with its identity on the first line and its state on the second."""
    item.setText(first + chr(10) + second)
    item.setToolTip(first + chr(10) + second)
