"""
Non-intrusive notification banner shown at the top of the window.

Appears below the toolbar, doesn't block any interaction, auto-hides after
a configurable duration. Has a close button for manual dismiss.
"""
from PyQt5.QtWidgets import QFrame, QHBoxLayout, QLabel, QToolButton
from PyQt5.QtCore import Qt, QTimer


_STYLES = {
    "info": (
        "background-color: #e3f2fd;"
        "color: #0d47a1;"
        "border-bottom: 1px solid #90caf9;"
    ),
    "success": (
        "background-color: #e8f5e9;"
        "color: #1b5e20;"
        "border-bottom: 1px solid #a5d6a7;"
    ),
    "warning": (
        "background-color: #fff8e1;"
        "color: #6d4c00;"
        "border-bottom: 1px solid #ffd54f;"
    ),
    "error": (
        "background-color: #ffebee;"
        "color: #b71c1c;"
        "border-bottom: 1px solid #ef9a9a;"
    ),
}


class NotificationBanner(QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.hide)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 6, 8, 6)
        layout.setSpacing(8)

        self.label = QLabel("")
        self.label.setTextFormat(Qt.RichText)
        layout.addWidget(self.label, 1)

        self.close_btn = QToolButton()
        self.close_btn.setText("×")
        self.close_btn.setAutoRaise(True)
        self.close_btn.setStyleSheet(
            "QToolButton { font-size: 16px; font-weight: bold; "
            "padding: 0 6px; border: none; color: inherit; }"
            "QToolButton:hover { background: rgba(0,0,0,0.06); border-radius: 3px; }"
        )
        self.close_btn.clicked.connect(self.hide)
        layout.addWidget(self.close_btn, 0)

        self.hide()

    def show_message(self, text: str, level: str = "info", duration_ms: int = 6000):
        """Show the banner with the given message.

        Args:
            text: Message (rich text supported).
            level: 'info' | 'success' | 'warning' | 'error'.
            duration_ms: Auto-hide after this many ms. 0 = stay until closed.
        """
        style = _STYLES.get(level, _STYLES["info"])
        self.setStyleSheet(f"NotificationBanner {{ {style} }} QLabel {{ background: transparent; }}")
        self.label.setText(text)
        self.show()
        self._timer.stop()
        if duration_ms > 0:
            self._timer.start(duration_ms)
