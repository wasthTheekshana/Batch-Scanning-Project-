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
PAPER = "#F2F4F7"        # workspace background
SURFACE = "#FFFFFF"      # cards, inputs, tables
LINE = "#D9DEE5"         # hairline borders
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


QSS = f"""
* {{
    font-family: "Segoe UI Variable", "Segoe UI", sans-serif;
    font-size: 10pt;
    color: {TEXT};
}}
QMainWindow, QDialog, QWizard {{ background: {PAPER}; }}

/* ---- header band ---------------------------------------------------- */
#HeaderBar {{
    background: {INK};
    border: none;
}}
#HeaderBar QLabel {{ color: #E8EDF2; background: transparent; }}
#HeaderTitle {{
    font-size: 13pt;
    font-weight: 600;
    color: #FFFFFF;
}}
#HeaderSub {{ color: #8FA3B3; font-size: 9pt; }}
#UserChip {{
    background: {INK_SOFT};
    color: #E8EDF2;
    border-radius: 12px;
    padding: 4px 12px;
    font-size: 9pt;
}}
#HeaderBar QPushButton {{
    background: transparent;
    color: #B9C6D0;
    border: 1px solid {INK_SOFT};
    border-radius: 4px;
    padding: 4px 12px;
}}
#HeaderBar QPushButton:hover {{ background: {INK_SOFT}; color: white; }}

/* ---- navigation tabs ------------------------------------------------- */
QTabWidget::pane {{
    border: none;
    background: {PAPER};
    padding: 8px;
}}
QTabBar {{ background: {SURFACE}; }}
QTabBar::tab {{
    background: {SURFACE};
    color: {TEXT_MUTED};
    padding: 10px 20px;
    border: none;
    border-bottom: 3px solid transparent;
    font-weight: 500;
    min-width: 60px;
}}
QTabBar::tab:hover {{ color: {TEXT}; background: #FAFBFC; }}
QTabBar::tab:selected {{
    color: {ACCENT};
    border-bottom: 3px solid {ACCENT};
    font-weight: 600;
}}

/* ---- cards ----------------------------------------------------------- */
QGroupBox {{
    background: {SURFACE};
    border: 1px solid {LINE};
    border-radius: 8px;
    margin-top: 14px;
    padding: 12px 10px 10px 10px;
    font-weight: 600;
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    left: 10px;
    padding: 0 4px;
    color: {TEXT_MUTED};
    font-size: 9pt;
}}

/* ---- buttons ---------------------------------------------------------- */
QPushButton {{
    background: {SURFACE};
    border: 1px solid {LINE};
    border-radius: 6px;
    padding: 7px 16px;
    font-weight: 500;
}}
QPushButton:hover {{ border-color: {ACCENT}; color: {ACCENT}; }}
QPushButton:pressed {{ background: {PAPER}; }}
QPushButton:disabled {{ color: #A8B3BF; background: {PAPER}; }}
QPushButton[class="primary"] {{
    background: {ACCENT};
    color: white;
    border: none;
}}
QPushButton[class="primary"]:hover {{ background: {ACCENT_HOVER}; color: white; }}
QPushButton[class="primary"]:pressed {{ background: {ACCENT_DOWN}; }}
QPushButton[class="primary"]:disabled {{ background: #9DBFB7; color: #F0F5F3; }}
QPushButton[class="danger"] {{
    background: transparent;
    color: {FAIL};
    border: 1px solid {FAIL};
}}
QPushButton[class="danger"]:hover {{ background: {FAIL}; color: white; }}
QPushButton[class="danger"]:disabled {{ color: #D5A6A0; border-color: #E5C4C0; background: transparent; }}

/* ---- inputs ------------------------------------------------------------ */
QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QTextEdit {{
    background: {SURFACE};
    border: 1px solid {LINE};
    border-radius: 6px;
    padding: 6px 10px;
    selection-background-color: {ACCENT};
}}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus {{
    border: 1px solid {ACCENT};
}}
QComboBox::drop-down {{ border: none; width: 24px; }}
QComboBox QAbstractItemView {{
    background: {SURFACE};
    border: 1px solid {LINE};
    selection-background-color: {PAPER};
    selection-color: {ACCENT};
}}

/* ---- lists & tables ------------------------------------------------------ */
QListWidget {{
    background: {SURFACE};
    border: 1px solid {LINE};
    border-radius: 8px;
    padding: 4px;
}}
QListWidget::item {{ padding: 8px 10px; border-radius: 5px; }}
QListWidget::item:hover {{ background: {PAPER}; }}
QListWidget::item:selected {{
    background: #E5F2EF;
    color: {ACCENT_DOWN};
}}
QTableWidget {{
    background: {SURFACE};
    border: 1px solid {LINE};
    border-radius: 8px;
    gridline-color: transparent;
    alternate-background-color: #F8FAFB;
}}
QTableWidget::item {{ padding: 6px 8px; border-bottom: 1px solid #EEF1F4; }}
QTableWidget::item:selected {{ background: #E5F2EF; color: {ACCENT_DOWN}; }}
QHeaderView::section {{
    background: {SURFACE};
    color: {TEXT_MUTED};
    font-weight: 600;
    font-size: 8.5pt;
    border: none;
    border-bottom: 2px solid {LINE};
    padding: 8px;
}}

/* ---- misc ---------------------------------------------------------------- */
QLabel[class="muted"] {{ color: {TEXT_MUTED}; }}
QLabel[class="h2"] {{ font-size: 12pt; font-weight: 600; }}
QSlider::groove:horizontal {{
    height: 4px; background: {LINE}; border-radius: 2px;
}}
QSlider::handle:horizontal {{
    width: 16px; height: 16px; margin: -6px 0;
    background: {ACCENT}; border-radius: 8px;
}}
QSlider::sub-page:horizontal {{ background: {ACCENT}; border-radius: 2px; }}
QProgressBar {{
    background: {LINE}; border: none; border-radius: 4px;
    height: 8px; text-align: center; font-size: 8pt;
}}
QProgressBar::chunk {{ background: {ACCENT}; border-radius: 4px; }}
QStatusBar {{
    background: {SURFACE};
    border-top: 1px solid {LINE};
    color: {TEXT_MUTED};
}}
QSplitter::handle {{ background: {PAPER}; width: 6px; }}
QScrollBar:vertical {{
    background: transparent; width: 10px; margin: 0;
}}
QScrollBar::handle:vertical {{
    background: #C4CDD6; border-radius: 5px; min-height: 30px;
}}
QScrollBar::handle:vertical:hover {{ background: {TEXT_MUTED}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; }}
QScrollBar::handle:horizontal {{
    background: #C4CDD6; border-radius: 5px; min-width: 30px;
}}
QToolTip {{
    background: {INK}; color: white; border: none;
    padding: 5px 8px; border-radius: 4px;
}}
QGraphicsView {{ background: #E7EAEF; border: 1px solid {LINE}; border-radius: 8px; }}
QScrollArea {{ border: none; background: transparent; }}
QScrollArea > QWidget > QWidget {{ background: transparent; }}
QWizard QLabel {{ background: transparent; }}
"""


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
    app.setStyleSheet(QSS)


def mark(widget, css_class: str) -> None:
    """Tag a widget for QSS class selectors, e.g. mark(btn, "primary")."""
    widget.setProperty("class", css_class)
    widget.style().unpolish(widget)
    widget.style().polish(widget)
