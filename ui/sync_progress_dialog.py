"""
Outlook-style Send/Receive progress dialog.

Shows:
- Per-account task list with progress
- Real-time log of every action (connecting, fetching, etc.)
- Overall progress bar
- "Hide" button so the user can keep working while it runs
- "Cancel all" stop button
"""
from datetime import datetime
from PyQt5.QtCore import Qt, pyqtSignal, QTimer
from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QProgressBar,
    QTextEdit, QListWidget, QListWidgetItem, QFrame, QSplitter, QWidget,
)
from PyQt5.QtGui import QFont, QColor


class SyncProgressDialog(QDialog):
    cancel_requested = pyqtSignal()

    LEVEL_COLORS = {
        "info": "#605e5c",
        "success": "#107c10",
        "error": "#a4262c",
        "warn": "#bf6900",
    }

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("RunLab Mail Send/Receive Progress")
        self.resize(680, 420)
        self.setWindowFlags(
            self.windowFlags() | Qt.WindowMinimizeButtonHint
        )
        self._tasks: dict[str, dict] = {}  # account name -> {row, status, ...}
        self._cancelled = False
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(8)

        # Header
        header = QLabel(
            "<b>Synchronizing your accounts</b>"
        )
        header.setTextFormat(Qt.RichText)
        header.setStyleSheet("font-size:11pt;")
        layout.addWidget(header)

        # Overall progress
        self.overall_status = QLabel("Initializing...")
        self.overall_status.setStyleSheet("color:#605e5c;")
        layout.addWidget(self.overall_status)
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 0)  # indeterminate
        self.progress_bar.setTextVisible(False)
        self.progress_bar.setMaximumHeight(6)
        layout.addWidget(self.progress_bar)

        # Splitter: tasks list (top) + log (bottom)
        splitter = QSplitter(Qt.Vertical)
        splitter.setStyleSheet("QSplitter::handle { background:#e1dfdd; height:1px; }")

        # Tasks list
        self.tasks_list = QListWidget()
        self.tasks_list.setStyleSheet(
            "QListWidget { background:#faf9f8; border:1px solid #e1dfdd; "
            "border-radius:4px; }"
            "QListWidget::item { padding:6px 10px; "
            "border-bottom:1px solid #f3f2f1; }"
        )
        splitter.addWidget(self.tasks_list)

        # Log area
        log_container = QWidget()
        lc_layout = QVBoxLayout(log_container)
        lc_layout.setContentsMargins(0, 6, 0, 0)
        lc_layout.setSpacing(4)
        log_label = QLabel("Activity log:")
        log_label.setStyleSheet("color:#605e5c; font-size:9pt;")
        lc_layout.addWidget(log_label)
        self.log_view = QTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setStyleSheet(
            "QTextEdit { background:#1e1e1e; color:#d4d4d4; "
            "font-family:Consolas, 'Courier New', monospace; "
            "font-size:9pt; border:1px solid #3a3a3a; "
            "border-radius:4px; padding:6px; }"
        )
        lc_layout.addWidget(self.log_view)
        splitter.addWidget(log_container)

        splitter.setSizes([120, 240])
        layout.addWidget(splitter, 1)

        # Buttons
        btn_row = QHBoxLayout()
        self.copy_log_btn = QPushButton("Copy log")
        self.copy_log_btn.clicked.connect(self._copy_log)
        btn_row.addWidget(self.copy_log_btn)
        btn_row.addStretch(1)
        self.hide_btn = QPushButton("Hide")
        self.hide_btn.setToolTip("Hide this window — sync continues in the background")
        self.hide_btn.clicked.connect(self.hide)
        btn_row.addWidget(self.hide_btn)
        self.cancel_btn = QPushButton("Cancel all")
        self.cancel_btn.clicked.connect(self._cancel)
        btn_row.addWidget(self.cancel_btn)
        self.close_btn = QPushButton("Close")
        self.close_btn.setEnabled(False)
        self.close_btn.clicked.connect(self.accept)
        btn_row.addWidget(self.close_btn)
        layout.addLayout(btn_row)

    # ----- Public API -----
    def add_task(self, key: str, title: str):
        """Register a new sync task (e.g. one per account)."""
        item = QListWidgetItem(f"⏳  {title} — waiting")
        item.setForeground(QColor("#605e5c"))
        self.tasks_list.addItem(item)
        self._tasks[key] = {"item": item, "title": title, "status": "waiting"}
        self._update_overall()

    def start_task(self, key: str):
        t = self._tasks.get(key)
        if not t:
            return
        t["status"] = "running"
        t["item"].setText(f"⟳  {t['title']} — connecting...")
        t["item"].setForeground(QColor("#0078d4"))
        self._update_overall()

    def update_task(self, key: str, status_text: str, current=None, total=None):
        t = self._tasks.get(key)
        if not t:
            return
        if current is not None and total is not None and total > 0:
            label = f"⟳  {t['title']} — {status_text} ({current}/{total})"
        else:
            label = f"⟳  {t['title']} — {status_text}"
        t["item"].setText(label)

    def finish_task(self, key: str, success: bool, summary: str):
        t = self._tasks.get(key)
        if not t:
            return
        if success:
            t["status"] = "ok"
            t["item"].setText(f"✓  {t['title']} — {summary}")
            t["item"].setForeground(QColor("#107c10"))
        else:
            t["status"] = "error"
            t["item"].setText(f"✗  {t['title']} — {summary}")
            t["item"].setForeground(QColor("#a4262c"))
        self._update_overall()

    def log(self, message: str, level: str = "info"):
        """Append a line to the log with a timestamp."""
        ts = datetime.now().strftime("%H:%M:%S")
        color = self.LEVEL_COLORS.get(level, "#d4d4d4")
        # Use HTML for color
        html = (
            f'<span style="color:#888;">{ts}</span> '
            f'<span style="color:{color};">{self._escape(message)}</span>'
        )
        self.log_view.append(html)
        # Auto-scroll
        sb = self.log_view.verticalScrollBar()
        sb.setValue(sb.maximum())

    def all_done(self):
        """Call when every task has finished. Stops the indeterminate
        progress bar, enables Close, disables Cancel."""
        self.progress_bar.setRange(0, 1)
        self.progress_bar.setValue(1)
        self.cancel_btn.setEnabled(False)
        self.close_btn.setEnabled(True)
        ok = sum(1 for t in self._tasks.values() if t["status"] == "ok")
        err = sum(1 for t in self._tasks.values() if t["status"] == "error")
        parts = [f"{ok} succeeded"]
        if err:
            parts.append(f"{err} failed")
        self.overall_status.setText("Sync complete — " + ", ".join(parts) + ".")

    @property
    def is_cancelled(self) -> bool:
        return self._cancelled

    # ----- Internals -----
    def _cancel(self):
        if self._cancelled:
            return
        self._cancelled = True
        self.cancel_btn.setEnabled(False)
        self.log("Cancellation requested by user...", level="warn")
        self.cancel_requested.emit()

    def _update_overall(self):
        running = sum(1 for t in self._tasks.values() if t["status"] == "running")
        waiting = sum(1 for t in self._tasks.values() if t["status"] == "waiting")
        done = sum(1 for t in self._tasks.values()
                   if t["status"] in ("ok", "error"))
        total = len(self._tasks)
        self.overall_status.setText(
            f"{running} running · {waiting} pending · {done}/{total} complete"
        )

    def _copy_log(self):
        from PyQt5.QtWidgets import QApplication
        QApplication.clipboard().setText(self.log_view.toPlainText())
        self.log("(log copied to clipboard)", level="info")

    @staticmethod
    def _escape(text: str) -> str:
        return (str(text)
                .replace("&", "&amp;")
                .replace("<", "&lt;")
                .replace(">", "&gt;"))
