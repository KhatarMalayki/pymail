"""
Global Qt stylesheet for RunLab Mail. Outlook/eM Client-inspired flat modern look.

Color palette:
  primary       #0078d4   (Microsoft-style blue)
  primary_dark  #005a9e
  bg_main       #ffffff
  bg_sidebar    #f3f2f1
  bg_email_row  #ffffff
  bg_hover      #edebe9
  bg_selected   #e1efff
  text_primary  #201f1e
  text_muted    #605e5c
  border        #e1dfdd
  unread_dot    #0078d4
  warn          #d83b01
"""
from PyQt5.QtGui import QFont, QColor


# ---------- Avatar palette ----------
# Used to color the sender-initial avatar circles. Picked from a Microsoft-ish
# rotating palette; we hash sender email -> index.
AVATAR_COLORS = [
    "#0078d4", "#107c10", "#5c2d91", "#a4262c",
    "#038387", "#ca5010", "#8764b8", "#498205",
    "#e3008c", "#0099bc", "#bf0077", "#525252",
]


def avatar_color_for(text: str) -> str:
    if not text:
        return AVATAR_COLORS[0]
    idx = abs(hash(text.lower().strip())) % len(AVATAR_COLORS)
    return AVATAR_COLORS[idx]


def initials_of(text: str) -> str:
    if not text:
        return "?"
    # Strip "Name" out of "Name <email@x.com>"
    s = text.split("<")[0].strip()
    if not s:
        s = text.split("@")[0]
    parts = [p for p in s.replace(".", " ").replace("_", " ").split() if p]
    if not parts:
        return text[:2].upper()
    if len(parts) == 1:
        return parts[0][:2].upper()
    return (parts[0][0] + parts[-1][0]).upper()


# ---------- Global stylesheet ----------
QSS = """
* {
    font-family: "Segoe UI", "Segoe UI Variable", system-ui, sans-serif;
}

QMainWindow, QWidget {
    background-color: #ffffff;
    color: #201f1e;
}

/* ---------- Toolbar ---------- */
QToolBar {
    background-color: #ffffff;
    border: none;
    border-bottom: 1px solid #e1dfdd;
    spacing: 2px;
    padding: 6px 8px;
}
QToolBar::separator {
    background-color: #e1dfdd;
    width: 1px;
    margin: 6px 8px;
}
QToolButton {
    background-color: transparent;
    color: #201f1e;
    padding: 6px 12px;
    border-radius: 4px;
    border: 1px solid transparent;
    font-size: 9pt;
}
QToolButton:hover {
    background-color: #f3f2f1;
    border: 1px solid #e1dfdd;
}
QToolButton:pressed {
    background-color: #e1dfdd;
}
QToolButton:checked {
    background-color: #cfe4fa;
    border: 1px solid #0078d4;
    color: #0078d4;
}

/* ---------- Tree (accounts/folders sidebar) ---------- */
QTreeWidget {
    background-color: #faf9f8;
    border: none;
    border-right: 1px solid #e1dfdd;
    outline: 0;
    padding: 6px 0;
}
QTreeWidget::item {
    padding: 7px 8px;
    border: none;
    color: #201f1e;
}
QTreeWidget::item:hover {
    background-color: #f3f2f1;
}
QTreeWidget::item:selected {
    background-color: #e1efff;
    color: #0078d4;
}
QTreeWidget::item:selected:active {
    background-color: #cfe4fa;
    color: #004578;
}
QTreeWidget::branch {
    background-color: transparent;
}

/* ---------- Email list (QTableWidget when used) ---------- */
QTableWidget {
    background-color: #ffffff;
    alternate-background-color: #faf9f8;
    border: none;
    border-right: 1px solid #e1dfdd;
    gridline-color: #f3f2f1;
    selection-background-color: #cfe4fa;
    selection-color: #004578;
}
QTableWidget::item {
    padding: 8px 6px;
    border-bottom: 1px solid #f3f2f1;
}
QTableWidget::item:selected {
    background-color: #cfe4fa;
    color: #004578;
}
QHeaderView::section {
    background-color: #faf9f8;
    color: #605e5c;
    padding: 8px 8px;
    border: none;
    border-right: 1px solid #f3f2f1;
    border-bottom: 1px solid #e1dfdd;
    font-weight: 600;
    font-size: 11px;
}

/* ---------- Email list (QListWidget when used) ---------- */
QListWidget {
    background-color: #ffffff;
    border: none;
    border-right: 1px solid #e1dfdd;
    outline: 0;
}
QListWidget::item {
    padding: 0;
    border-bottom: 1px solid #f3f2f1;
}
QListWidget::item:hover {
    background-color: #f3f2f1;
}
QListWidget::item:selected {
    background-color: #cfe4fa;
}
QListWidget::item:selected:active {
    background-color: #cfe4fa;
}

/* ---------- Inputs ---------- */
QLineEdit, QTextEdit, QPlainTextEdit, QSpinBox, QComboBox {
    background-color: #ffffff;
    border: 1px solid #c8c6c4;
    border-radius: 4px;
    padding: 6px 8px;
    selection-background-color: #cfe4fa;
}
QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus, QSpinBox:focus, QComboBox:focus {
    border: 2px solid #0078d4;
    padding: 5px 7px;  /* compensate for border increase */
}
QLineEdit:hover, QSpinBox:hover, QComboBox:hover {
    border: 1px solid #605e5c;
}
QComboBox::drop-down {
    border: none;
    width: 20px;
}
QComboBox::down-arrow {
    image: none;
    width: 0;
    height: 0;
    border-left: 4px solid transparent;
    border-right: 4px solid transparent;
    border-top: 5px solid #605e5c;
    margin-right: 6px;
}

/* QSpinBox arrows: clean Outlook-style, replaces ugly default black squares */
QSpinBox {
    padding-right: 22px;  /* room for buttons */
}
QSpinBox::up-button, QSpinBox::down-button {
    subcontrol-origin: border;
    background: transparent;
    border: none;
    width: 18px;
    margin: 1px;
    border-radius: 2px;
}
QSpinBox::up-button {
    subcontrol-position: top right;
}
QSpinBox::down-button {
    subcontrol-position: bottom right;
}
QSpinBox::up-button:hover, QSpinBox::down-button:hover {
    background: #e1dfdd;
}
QSpinBox::up-button:pressed, QSpinBox::down-button:pressed {
    background: #c8c6c4;
}
QSpinBox::up-arrow {
    image: none;
    width: 0;
    height: 0;
    border-left: 4px solid transparent;
    border-right: 4px solid transparent;
    border-bottom: 5px solid #605e5c;
}
QSpinBox::down-arrow {
    image: none;
    width: 0;
    height: 0;
    border-left: 4px solid transparent;
    border-right: 4px solid transparent;
    border-top: 5px solid #605e5c;
}
QSpinBox::up-arrow:disabled, QSpinBox::up-arrow:off {
    border-bottom-color: #c8c6c4;
}
QSpinBox::down-arrow:disabled, QSpinBox::down-arrow:off {
    border-top-color: #c8c6c4;
}

/* ---------- Buttons ---------- */
QPushButton {
    background-color: #ffffff;
    color: #201f1e;
    border: 1px solid #c8c6c4;
    border-radius: 4px;
    padding: 7px 14px;
    min-width: 60px;
}
QPushButton:hover {
    background-color: #f3f2f1;
    border: 1px solid #605e5c;
}
QPushButton:pressed {
    background-color: #e1dfdd;
}
QPushButton:default {
    background-color: #0078d4;
    color: #ffffff;
    border: 1px solid #0078d4;
}
QPushButton:default:hover {
    background-color: #106ebe;
    border: 1px solid #106ebe;
}
QPushButton:default:pressed {
    background-color: #005a9e;
}
QPushButton:disabled {
    background-color: #f3f2f1;
    color: #a19f9d;
    border: 1px solid #e1dfdd;
}

/* ---------- Tabs ---------- */
QTabWidget::pane {
    border: 1px solid #e1dfdd;
    border-radius: 4px;
    background-color: #ffffff;
    top: -1px;
}
QTabBar::tab {
    background-color: transparent;
    color: #605e5c;
    padding: 8px 16px;
    border: none;
    border-bottom: 2px solid transparent;
}
QTabBar::tab:hover {
    color: #201f1e;
}
QTabBar::tab:selected {
    color: #0078d4;
    border-bottom: 2px solid #0078d4;
    font-weight: 600;
}

/* ---------- Status bar ---------- */
QStatusBar {
    background-color: #faf9f8;
    color: #605e5c;
    border-top: 1px solid #e1dfdd;
    padding: 2px 8px;
}
QStatusBar::item {
    border: none;
}

/* ---------- Splitter ---------- */
QSplitter::handle {
    background-color: #e1dfdd;
}
QSplitter::handle:horizontal {
    width: 1px;
}
QSplitter::handle:vertical {
    height: 1px;
}

/* ---------- Scrollbars ---------- */
QScrollBar:vertical {
    background: transparent;
    width: 12px;
    margin: 0;
}
QScrollBar::handle:vertical {
    background: #c8c6c4;
    min-height: 30px;
    border-radius: 6px;
    margin: 2px;
}
QScrollBar::handle:vertical:hover {
    background: #a19f9d;
}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
    height: 0;
    background: transparent;
}
QScrollBar:horizontal {
    background: transparent;
    height: 12px;
    margin: 0;
}
QScrollBar::handle:horizontal {
    background: #c8c6c4;
    min-width: 30px;
    border-radius: 6px;
    margin: 2px;
}
QScrollBar::handle:horizontal:hover {
    background: #a19f9d;
}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {
    width: 0;
    background: transparent;
}

/* ---------- Menus ---------- */
QMenu {
    background-color: #ffffff;
    border: 1px solid #e1dfdd;
    padding: 4px 0;
}
QMenu::item {
    padding: 7px 24px;
    background-color: transparent;
}
QMenu::item:selected {
    background-color: #f3f2f1;
}
QMenu::separator {
    height: 1px;
    background-color: #e1dfdd;
    margin: 4px 6px;
}

/* ---------- Group boxes ---------- */
QGroupBox {
    border: 1px solid #e1dfdd;
    border-radius: 4px;
    margin-top: 14px;
    padding-top: 8px;
    font-weight: 600;
}
QGroupBox::title {
    subcontrol-origin: margin;
    left: 12px;
    padding: 0 6px;
    color: #605e5c;
}

/* ---------- Progress bar ---------- */
QProgressBar {
    border: 1px solid #e1dfdd;
    border-radius: 4px;
    background-color: #f3f2f1;
    text-align: center;
    height: 8px;
}
QProgressBar::chunk {
    background-color: #0078d4;
    border-radius: 3px;
}

/* ---------- Tooltips ---------- */
QToolTip {
    background-color: #323130;
    color: #ffffff;
    border: none;
    padding: 6px 10px;
    border-radius: 4px;
}

/* ---------- Dialog ---------- */
QDialog {
    background-color: #ffffff;
}
QLabel {
    color: #201f1e;
}

/* ---------- TextBrowser (email viewer) ---------- */
QTextBrowser {
    background-color: #ffffff;
    border: none;
    padding: 16px 20px;
    selection-background-color: #cfe4fa;
}

/* ---------- Search field special class ---------- */
QLineEdit#searchInput {
    background-color: #f3f2f1;
    border: 1px solid transparent;
    border-radius: 14px;
    padding: 6px 14px;
}
QLineEdit#searchInput:focus {
    background-color: #ffffff;
    border: 1px solid #0078d4;
    padding: 5px 13px;
}
"""


def apply_theme(app):
    """Set global font + stylesheet on the QApplication."""
    font = QFont("Segoe UI", 9)
    app.setFont(font)
    app.setStyleSheet(QSS)
