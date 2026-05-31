"""
Global Qt theme for RunLab Mail. Outlook/eM Client-inspired flat modern look,
now with light + dark palettes driven by a central token system.

How it works:
  - PALETTES holds two dicts of semantic color tokens ("bg", "text", ...).
  - set_mode("light"|"dark") selects the active palette and is persisted.
  - colors() returns the active token dict; color("text") returns one token.
  - build_qss() renders the global stylesheet from the active tokens.
  - apply_theme(app) applies the font + QSS.

Widgets that PAINT manually (the email-list delegate, avatars, the email
viewer) must read colors via color()/colors() instead of hardcoding hex, so
they follow the active theme too.
"""
from PyQt5.QtGui import QFont, QColor
from core import config

# ---------- Theme tokens ----------
# Light mode keeps the exact values the app shipped with, so light looks
# identical to before. Dark mode is a Fluent-style dark palette.
PALETTES = {
    "light": {
        "primary":        "#0078d4",
        "primary_hover":  "#106ebe",
        "primary_pressed":"#005a9e",
        "on_primary":     "#ffffff",
        "bg":             "#ffffff",  # main content background
        "bg_sidebar":     "#faf9f8",  # tree / list headers / status bar
        "bg_hover":       "#f3f2f1",
        "bg_pressed":     "#e1dfdd",
        "bg_selected":    "#cfe4fa",  # strong selection
        "bg_selected_soft":"#e1efff", # soft selection (tree)
        "bg_alt":         "#faf9f8",  # alternating rows
        "bg_input":       "#ffffff",
        "bg_search":      "#f3f2f1",
        "text":           "#201f1e",
        "text_strong":    "#004578",  # selected text
        "text_muted":     "#605e5c",
        "text_subtle":    "#323130",
        "text_disabled":  "#a19f9d",
        "border":         "#e1dfdd",
        "border_strong":  "#c8c6c4",
        "border_subtle":  "#f3f2f1",
        "separator":      "#c8c6c4",
        "unread":         "#0078d4",
        "warn":           "#d83b01",
        "tooltip_bg":     "#323130",
        "tooltip_text":   "#ffffff",
        "scroll_handle":  "#c8c6c4",
        "scroll_handle_hover":"#a19f9d",
        # attachment strip (compose/viewer)
        "attach_bg":      "#fffbe6",
        "attach_border":  "#f0d878",
        "attach_text":    "#604000",
    },
    "dark": {
        "primary":        "#4ca3ee",
        "primary_hover":  "#6cb6f2",
        "primary_pressed":"#3a8fd6",
        "on_primary":     "#0b1220",
        "bg":             "#1f1f1f",
        "bg_sidebar":     "#252526",
        "bg_hover":       "#2d2d2e",
        "bg_pressed":     "#3a3a3b",
        "bg_selected":    "#0e3a5f",
        "bg_selected_soft":"#16324a",
        "bg_alt":         "#232323",
        "bg_input":       "#2a2a2b",
        "bg_search":      "#2a2a2b",
        "text":           "#e6e6e6",
        "text_strong":    "#9cc7f0",
        "text_muted":     "#a6a6a6",
        "text_subtle":    "#cccccc",
        "text_disabled":  "#6b6b6b",
        "border":         "#3a3a3b",
        "border_strong":  "#4a4a4b",
        "border_subtle":  "#2d2d2e",
        "separator":      "#4a4a4b",
        "unread":         "#4ca3ee",
        "warn":           "#f1707b",
        "tooltip_bg":     "#3a3a3b",
        "tooltip_text":   "#f5f5f5",
        "scroll_handle":  "#4a4a4b",
        "scroll_handle_hover":"#5f5f60",
        "attach_bg":      "#2e2a1a",
        "attach_border":  "#5a4f2a",
        "attach_text":    "#e6d08a",
    },
}

_DEFAULT_MODE = "light"
_mode = config.get("theme_mode", _DEFAULT_MODE)
if _mode not in PALETTES:
    _mode = _DEFAULT_MODE


def current_mode() -> str:
    return _mode


def is_dark() -> bool:
    return _mode == "dark"


def set_mode(mode: str) -> None:
    """Select the active palette and persist it. Does NOT repaint open
    widgets — the caller should re-apply the theme (apply_theme) and refresh
    custom-painted views."""
    global _mode
    if mode not in PALETTES:
        mode = _DEFAULT_MODE
    _mode = mode
    config.set_value("theme_mode", mode)


def toggle_mode() -> str:
    set_mode("dark" if _mode == "light" else "light")
    return _mode


def colors() -> dict:
    return PALETTES[_mode]


def color(token: str) -> str:
    return PALETTES[_mode].get(token, "#000000")


def qcolor(token: str) -> QColor:
    return QColor(color(token))


# ---------- Avatar palette ----------
# Used to color the sender-initial avatar circles. Picked from a Microsoft-ish
# rotating palette; we hash sender email -> index. These read well on both
# light and dark backgrounds, so they're shared across themes.
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


# ---------- Global stylesheet (built from tokens) ----------
def build_qss() -> str:
    c = colors()
    return f"""
* {{
    font-family: "Segoe UI", "Segoe UI Variable", system-ui, sans-serif;
}}

QMainWindow, QWidget {{
    background-color: {c['bg']};
    color: {c['text']};
}}

/* ---------- Toolbar ---------- */
QToolBar {{
    background-color: {c['bg']};
    border: none;
    border-bottom: 1px solid {c['border']};
    spacing: 2px;
    padding: 6px 8px;
}}
QToolBar::separator {{
    background-color: {c['border']};
    width: 1px;
    margin: 6px 8px;
}}
QToolButton {{
    background-color: transparent;
    color: {c['text']};
    padding: 6px 12px;
    border-radius: 4px;
    border: 1px solid transparent;
    font-size: 9pt;
}}
QToolButton:hover {{
    background-color: {c['bg_hover']};
    border: 1px solid {c['border']};
}}
QToolButton:pressed {{
    background-color: {c['bg_pressed']};
}}
QToolButton:checked {{
    background-color: {c['bg_selected']};
    border: 1px solid {c['primary']};
    color: {c['primary']};
}}

/* ---------- Tree (accounts/folders sidebar) ---------- */
QTreeWidget {{
    background-color: {c['bg_sidebar']};
    border: none;
    border-right: 1px solid {c['border']};
    outline: 0;
    padding: 6px 0;
}}
QTreeWidget::item {{
    padding: 7px 8px;
    border: none;
    color: {c['text']};
}}
QTreeWidget::item:hover {{
    background-color: {c['bg_hover']};
}}
QTreeWidget::item:selected {{
    background-color: {c['bg_selected_soft']};
    color: {c['primary']};
}}
QTreeWidget::item:selected:active {{
    background-color: {c['bg_selected']};
    color: {c['text_strong']};
}}
QTreeWidget::branch {{
    background-color: transparent;
}}

/* ---------- Email list (QTableWidget when used) ---------- */
QTableWidget {{
    background-color: {c['bg']};
    alternate-background-color: {c['bg_alt']};
    border: none;
    border-right: 1px solid {c['border']};
    gridline-color: {c['border_subtle']};
    selection-background-color: {c['bg_selected']};
    selection-color: {c['text_strong']};
}}
QTableWidget::item {{
    padding: 8px 6px;
    border-bottom: 1px solid {c['border_subtle']};
}}
QTableWidget::item:selected {{
    background-color: {c['bg_selected']};
    color: {c['text_strong']};
}}
QHeaderView::section {{
    background-color: {c['bg_sidebar']};
    color: {c['text_muted']};
    padding: 8px 8px;
    border: none;
    border-right: 1px solid {c['border_subtle']};
    border-bottom: 1px solid {c['border']};
    font-weight: 600;
    font-size: 11px;
}}

/* ---------- Email list (QListWidget when used) ---------- */
QListWidget {{
    background-color: {c['bg']};
    border: none;
    border-right: 1px solid {c['border']};
    outline: 0;
}}
QListWidget::item {{
    padding: 0;
    border-bottom: 1px solid {c['border_subtle']};
}}
QListWidget::item:hover {{
    background-color: {c['bg_hover']};
}}
QListWidget::item:selected {{
    background-color: {c['bg_selected']};
}}
QListWidget::item:selected:active {{
    background-color: {c['bg_selected']};
}}

/* ---------- Inputs ---------- */
QLineEdit, QTextEdit, QPlainTextEdit, QSpinBox, QComboBox {{
    background-color: {c['bg_input']};
    color: {c['text']};
    border: 1px solid {c['border_strong']};
    border-radius: 4px;
    padding: 6px 8px;
    selection-background-color: {c['bg_selected']};
}}
QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus, QSpinBox:focus, QComboBox:focus {{
    border: 2px solid {c['primary']};
    padding: 5px 7px;  /* compensate for border increase */
}}
QLineEdit:hover, QSpinBox:hover, QComboBox:hover {{
    border: 1px solid {c['text_muted']};
}}
QComboBox QAbstractItemView {{
    background-color: {c['bg_input']};
    color: {c['text']};
    border: 1px solid {c['border']};
    selection-background-color: {c['bg_selected']};
    selection-color: {c['text_strong']};
}}
QComboBox::drop-down {{
    border: none;
    width: 20px;
}}
QComboBox::down-arrow {{
    image: none;
    width: 0;
    height: 0;
    border-left: 4px solid transparent;
    border-right: 4px solid transparent;
    border-top: 5px solid {c['text_muted']};
    margin-right: 6px;
}}

/* QSpinBox arrows */
QSpinBox {{
    padding-right: 22px;
}}
QSpinBox::up-button, QSpinBox::down-button {{
    subcontrol-origin: border;
    background: transparent;
    border: none;
    width: 18px;
    margin: 1px;
    border-radius: 2px;
}}
QSpinBox::up-button {{
    subcontrol-position: top right;
}}
QSpinBox::down-button {{
    subcontrol-position: bottom right;
}}
QSpinBox::up-button:hover, QSpinBox::down-button:hover {{
    background: {c['bg_pressed']};
}}
QSpinBox::up-button:pressed, QSpinBox::down-button:pressed {{
    background: {c['border_strong']};
}}
QSpinBox::up-arrow {{
    image: none;
    width: 0;
    height: 0;
    border-left: 4px solid transparent;
    border-right: 4px solid transparent;
    border-bottom: 5px solid {c['text_muted']};
}}
QSpinBox::down-arrow {{
    image: none;
    width: 0;
    height: 0;
    border-left: 4px solid transparent;
    border-right: 4px solid transparent;
    border-top: 5px solid {c['text_muted']};
}}
QSpinBox::up-arrow:disabled, QSpinBox::up-arrow:off {{
    border-bottom-color: {c['border_strong']};
}}
QSpinBox::down-arrow:disabled, QSpinBox::down-arrow:off {{
    border-top-color: {c['border_strong']};
}}

/* ---------- Buttons ---------- */
QPushButton {{
    background-color: {c['bg_input']};
    color: {c['text']};
    border: 1px solid {c['border_strong']};
    border-radius: 4px;
    padding: 7px 14px;
    min-width: 60px;
}}
QPushButton:hover {{
    background-color: {c['bg_hover']};
    border: 1px solid {c['text_muted']};
}}
QPushButton:pressed {{
    background-color: {c['bg_pressed']};
}}
QPushButton:default {{
    background-color: {c['primary']};
    color: {c['on_primary']};
    border: 1px solid {c['primary']};
}}
QPushButton:default:hover {{
    background-color: {c['primary_hover']};
    border: 1px solid {c['primary_hover']};
}}
QPushButton:default:pressed {{
    background-color: {c['primary_pressed']};
}}
QPushButton:disabled {{
    background-color: {c['bg_hover']};
    color: {c['text_disabled']};
    border: 1px solid {c['border']};
}}

/* ---------- Tabs ---------- */
QTabWidget::pane {{
    border: 1px solid {c['border']};
    border-radius: 4px;
    background-color: {c['bg']};
    top: -1px;
}}
QTabBar::tab {{
    background-color: transparent;
    color: {c['text_muted']};
    padding: 8px 16px;
    border: none;
    border-bottom: 2px solid transparent;
}}
QTabBar::tab:hover {{
    color: {c['text']};
}}
QTabBar::tab:selected {{
    color: {c['primary']};
    border-bottom: 2px solid {c['primary']};
    font-weight: 600;
}}

/* ---------- Status bar ---------- */
QStatusBar {{
    background-color: {c['bg_sidebar']};
    color: {c['text_muted']};
    border-top: 1px solid {c['border']};
    padding: 2px 8px;
}}
QStatusBar::item {{
    border: none;
}}

/* ---------- Splitter ---------- */
QSplitter::handle {{
    background-color: {c['border']};
}}
QSplitter::handle:horizontal {{
    width: 1px;
}}
QSplitter::handle:vertical {{
    height: 1px;
}}

/* ---------- Scrollbars ---------- */
QScrollBar:vertical {{
    background: transparent;
    width: 12px;
    margin: 0;
}}
QScrollBar::handle:vertical {{
    background: {c['scroll_handle']};
    min-height: 30px;
    border-radius: 6px;
    margin: 2px;
}}
QScrollBar::handle:vertical:hover {{
    background: {c['scroll_handle_hover']};
}}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
    height: 0;
    background: transparent;
}}
QScrollBar:horizontal {{
    background: transparent;
    height: 12px;
    margin: 0;
}}
QScrollBar::handle:horizontal {{
    background: {c['scroll_handle']};
    min-width: 30px;
    border-radius: 6px;
    margin: 2px;
}}
QScrollBar::handle:horizontal:hover {{
    background: {c['scroll_handle_hover']};
}}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{
    width: 0;
    background: transparent;
}}

/* ---------- Menus ---------- */
QMenu {{
    background-color: {c['bg']};
    color: {c['text']};
    border: 1px solid {c['border']};
    padding: 4px 0;
}}
QMenu::item {{
    padding: 7px 24px;
    background-color: transparent;
}}
QMenu::item:selected {{
    background-color: {c['bg_hover']};
}}
QMenu::separator {{
    height: 1px;
    background-color: {c['border']};
    margin: 4px 6px;
}}

/* ---------- Group boxes ---------- */
QGroupBox {{
    border: 1px solid {c['border']};
    border-radius: 4px;
    margin-top: 14px;
    padding-top: 8px;
    font-weight: 600;
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    left: 12px;
    padding: 0 6px;
    color: {c['text_muted']};
}}

/* ---------- Progress bar ---------- */
QProgressBar {{
    border: 1px solid {c['border']};
    border-radius: 4px;
    background-color: {c['bg_hover']};
    text-align: center;
    height: 8px;
    color: {c['text']};
}}
QProgressBar::chunk {{
    background-color: {c['primary']};
    border-radius: 3px;
}}

/* ---------- Tooltips ---------- */
QToolTip {{
    background-color: {c['tooltip_bg']};
    color: {c['tooltip_text']};
    border: none;
    padding: 6px 10px;
    border-radius: 4px;
}}

/* ---------- Dialog ---------- */
QDialog {{
    background-color: {c['bg']};
}}
QLabel {{
    color: {c['text']};
    background: transparent;
}}

/* ---------- TextBrowser (email viewer) ---------- */
QTextBrowser {{
    background-color: {c['bg']};
    color: {c['text']};
    border: none;
    padding: 16px 20px;
    selection-background-color: {c['bg_selected']};
}}

/* ---------- Search field special class ---------- */
QLineEdit#searchInput {{
    background-color: {c['bg_search']};
    border: 1px solid transparent;
    border-radius: 14px;
    padding: 6px 14px;
}}
QLineEdit#searchInput:focus {{
    background-color: {c['bg_input']};
    border: 1px solid {c['primary']};
    padding: 5px 13px;
}}
"""


# Backwards-compat: some modules import QSS directly. Keep a snapshot of the
# active theme's stylesheet available as a module attribute.
QSS = build_qss()


def apply_theme(app):
    """Set global font + stylesheet on the QApplication for the active mode."""
    global QSS
    QSS = build_qss()
    font = QFont("Segoe UI", 9)
    app.setFont(font)
    app.setStyleSheet(QSS)
