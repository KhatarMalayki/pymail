"""
Detached email reading window — opens when the user double-clicks an email
in the list. Outlook-style: a separate top-level window with the same
EmailView widget the main window uses on the right pane.

Reply / Reply-All / Forward buttons emit `compose_requested(kind, email_id)`
back to the main window so the parent can spawn ComposeDialog with the
proper prefill (we keep the compose flow centralized in main_window).
"""
from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtWidgets import QMainWindow, QStatusBar
from .email_view import EmailView


class EmailWindow(QMainWindow):
    compose_requested = pyqtSignal(str, int)  # kind, email_id

    def __init__(self, email_id: int, parent=None):
        super().__init__(parent)
        self._email_id = email_id

        # Independent top-level window — appears in taskbar like Outlook
        self.setWindowFlags(
            self.windowFlags()
            | Qt.Window
            | Qt.WindowMinimizeButtonHint
            | Qt.WindowMaximizeButtonHint
            | Qt.WindowCloseButtonHint
        )

        self.setMinimumSize(720, 520)
        self.resize(960, 720)

        self.viewer = EmailView()
        self.viewer.reply_requested.connect(self._on_reply_requested)
        self.setCentralWidget(self.viewer)
        self.setStatusBar(QStatusBar())

        self._load(email_id)

    def _load(self, email_id: int):
        self.viewer.show_email(email_id)
        # Title bar shows subject so the taskbar entry is identifiable
        try:
            email = self.viewer.email or {}
            subject = email.get("subject") or "(no subject)"
            sender = self._extract_sender_name(email.get("sender") or "")
            if sender:
                self.setWindowTitle(f"{subject} — {sender}")
            else:
                self.setWindowTitle(subject)
        except Exception:
            self.setWindowTitle("Email")

    def _on_reply_requested(self, email: dict, mode: str):
        # Bubble up to the main window so the central compose flow handles it
        eid = email.get("id") if email else self._email_id
        if eid is not None:
            self.compose_requested.emit(mode, eid)

    @staticmethod
    def _extract_sender_name(raw: str) -> str:
        # "Name <addr>" -> "Name"; plain addr -> ""
        if "<" in raw:
            return raw.split("<", 1)[0].strip().strip('"')
        return ""
