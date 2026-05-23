"""
Right-side email viewer widget: header info + body + attachments.
"""
import os
import tempfile
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QTextBrowser, QFrame,
    QPushButton, QListWidget, QListWidgetItem, QFileDialog, QMessageBox,
)
from PyQt5.QtCore import Qt, QUrl, pyqtSignal, QSize, QRect
from PyQt5.QtGui import (
    QDesktopServices, QFont, QPainter, QColor, QPixmap, QBrush, QPen,
)
from core import database
from .theme import avatar_color_for, initials_of


def _make_avatar_pixmap(text: str, size: int = 40) -> QPixmap:
    """Render a circular avatar with initials. Returns transparent QPixmap."""
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing, True)
    p.setRenderHint(QPainter.TextAntialiasing, True)
    p.setPen(Qt.NoPen)
    p.setBrush(QBrush(QColor(avatar_color_for(text))))
    p.drawEllipse(0, 0, size, size)
    p.setPen(QPen(QColor("#ffffff")))
    f = QFont("Segoe UI", int(size * 0.36))
    f.setBold(True)
    p.setFont(f)
    p.drawText(QRect(0, 0, size, size), Qt.AlignCenter, initials_of(text))
    p.end()
    return pm


class EmailView(QWidget):
    reply_requested = pyqtSignal(dict, str)  # email, mode (reply, reply_all, forward)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.email = None
        self._build_ui()
        self.show_empty()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Header panel
        self.header_frame = QFrame()
        self.header_frame.setStyleSheet(
            "QFrame { background: #ffffff; border-bottom: 1px solid #e1dfdd; }"
        )
        h_layout = QVBoxLayout(self.header_frame)
        h_layout.setContentsMargins(24, 18, 24, 16)
        h_layout.setSpacing(8)

        self.subject_label = QLabel()
        f = QFont(); f.setPointSize(15); f.setWeight(QFont.DemiBold)
        self.subject_label.setFont(f)
        self.subject_label.setWordWrap(True)
        self.subject_label.setStyleSheet("color:#201f1e;")
        h_layout.addWidget(self.subject_label)

        # Sender row: avatar + name/email + date on the right
        sender_row = QHBoxLayout()
        sender_row.setContentsMargins(0, 8, 0, 4)
        sender_row.setSpacing(12)

        self.avatar_label = QLabel()
        self.avatar_label.setFixedSize(44, 44)
        self.avatar_label.setStyleSheet("background:transparent;")
        sender_row.addWidget(self.avatar_label, 0, Qt.AlignTop)

        sender_text_col = QVBoxLayout()
        sender_text_col.setContentsMargins(0, 0, 0, 0)
        sender_text_col.setSpacing(2)
        self.sender_name_label = QLabel()
        f2 = QFont(); f2.setPointSize(10); f2.setWeight(QFont.DemiBold)
        self.sender_name_label.setFont(f2)
        self.sender_name_label.setStyleSheet("color:#201f1e;")
        self.sender_email_label = QLabel()
        self.sender_email_label.setStyleSheet("color:#605e5c; font-size:9pt;")
        self.recipients_label = QLabel()
        self.recipients_label.setStyleSheet("color:#605e5c; font-size:9pt;")
        self.recipients_label.setWordWrap(True)
        sender_text_col.addWidget(self.sender_name_label)
        sender_text_col.addWidget(self.sender_email_label)
        sender_text_col.addWidget(self.recipients_label)
        sender_row.addLayout(sender_text_col, 1)

        self.date_label = QLabel()
        self.date_label.setStyleSheet("color:#605e5c; font-size:9pt;")
        self.date_label.setAlignment(Qt.AlignRight | Qt.AlignTop)
        sender_row.addWidget(self.date_label, 0, Qt.AlignTop)

        h_layout.addLayout(sender_row)

        # Cc (only shown if present)
        self.cc_label = QLabel()
        self.cc_label.setStyleSheet("color:#605e5c; font-size:9pt; padding-left:56px;")
        self.cc_label.setWordWrap(True)
        h_layout.addWidget(self.cc_label)

        # Action buttons row
        btn_row = QHBoxLayout()
        btn_row.setContentsMargins(0, 10, 0, 0)
        btn_row.setSpacing(6)
        self.reply_btn = QPushButton("↩  Reply")
        self.reply_all_btn = QPushButton("↩↩  Reply All")
        self.forward_btn = QPushButton("➡  Forward")
        for b in (self.reply_btn, self.reply_all_btn, self.forward_btn):
            b.setCursor(Qt.PointingHandCursor)
            btn_row.addWidget(b)
        btn_row.addStretch(1)
        self.reply_btn.clicked.connect(lambda: self.reply_requested.emit(self.email, "reply"))
        self.reply_all_btn.clicked.connect(lambda: self.reply_requested.emit(self.email, "reply_all"))
        self.forward_btn.clicked.connect(lambda: self.reply_requested.emit(self.email, "forward"))
        h_layout.addLayout(btn_row)

        layout.addWidget(self.header_frame)

        # Attachments
        self.att_frame = QFrame()
        self.att_frame.setStyleSheet(
            "QFrame { background:#fff8e1; border-bottom: 1px solid #e0d090; }"
        )
        a_layout = QVBoxLayout(self.att_frame)
        a_layout.setContentsMargins(14, 6, 14, 6)
        att_label = QLabel("📎 Attachments (double-click to save):")
        att_label.setStyleSheet("color:#604000;")
        a_layout.addWidget(att_label)
        self.att_list = QListWidget()
        self.att_list.setMaximumHeight(80)
        self.att_list.itemDoubleClicked.connect(self._save_attachment)
        a_layout.addWidget(self.att_list)
        layout.addWidget(self.att_frame)
        self.att_frame.setVisible(False)

        # Body viewer
        self.body_view = QTextBrowser()
        self.body_view.setOpenExternalLinks(True)
        layout.addWidget(self.body_view, 1)

    def show_empty(self):
        self.email = None
        self.subject_label.setText(
            "<span style='color:#a19f9d;'>Select an email to read</span>"
        )
        self.subject_label.setTextFormat(Qt.RichText)
        self.avatar_label.setPixmap(QPixmap())
        self.sender_name_label.setText("")
        self.sender_email_label.setText("")
        self.recipients_label.setText("")
        self.date_label.setText("")
        self.cc_label.setText("")
        self.cc_label.setVisible(False)
        self.att_frame.setVisible(False)
        self.body_view.setHtml("")
        for b in (self.reply_btn, self.reply_all_btn, self.forward_btn):
            b.setEnabled(False)

    def show_email(self, email_id: int):
        email = database.get_email(email_id)
        if not email:
            self.show_empty()
            return
        self.email = email

        self.subject_label.setText(email.get("subject") or "(no subject)")
        self.subject_label.setTextFormat(Qt.PlainText)

        # Avatar from sender
        sender_raw = email.get("sender") or ""
        self.avatar_label.setPixmap(_make_avatar_pixmap(sender_raw, 44))

        # Split "Name <email>" if possible
        name, addr = self._split_addr(sender_raw)
        self.sender_name_label.setText(name or addr or "(unknown)")
        self.sender_email_label.setText(f"<{addr}>" if addr and name else "")

        # Recipients
        to = email.get("recipients") or ""
        self.recipients_label.setText(f"to: {to}" if to else "")

        # Date (friendly format)
        date = email.get("date_sent") or email.get("date_received") or ""
        self.date_label.setText(self._format_date(date))

        cc = email.get("cc")
        if cc:
            self.cc_label.setText(f"cc: {cc}")
            self.cc_label.setVisible(True)
        else:
            self.cc_label.setVisible(False)

        for b in (self.reply_btn, self.reply_all_btn, self.forward_btn):
            b.setEnabled(True)

        # Attachments
        self.att_list.clear()
        atts = email.get("attachments") or []
        for a in atts:
            kb = (a.get("size") or 0) / 1024
            item = QListWidgetItem(f"📄 {a['filename']}  ({kb:.1f} KB)")
            item.setData(Qt.UserRole, a["id"])
            self.att_list.addItem(item)
        self.att_frame.setVisible(bool(atts))

        # Body: prefer HTML when available
        html = email.get("body_html")
        plain = email.get("body_plain") or ""
        if html:
            self.body_view.setHtml(html)
        else:
            safe = (plain
                    .replace("&", "&amp;")
                    .replace("<", "&lt;")
                    .replace(">", "&gt;"))
            self.body_view.setHtml(
                f'<div style="font-family: Segoe UI, sans-serif; '
                f'font-size: 10pt; color:#201f1e; '
                f'white-space: pre-wrap;">{safe}</div>'
            )

    @staticmethod
    def _split_addr(raw: str) -> tuple:
        """'Alice <a@x.com>' -> ('Alice', 'a@x.com'). Plain email -> ('', email)."""
        raw = (raw or "").strip()
        if "<" in raw and ">" in raw:
            name = raw.split("<")[0].strip().strip('"').strip("'")
            email = raw.split("<", 1)[1].split(">", 1)[0].strip()
            return name, email
        return "", raw

    @staticmethod
    def _format_date(iso: str) -> str:
        if not iso:
            return ""
        try:
            from datetime import datetime
            dt = datetime.fromisoformat(iso.replace("Z", "").replace("+00:00", ""))
            return dt.strftime("%a, %d %b %Y, %H:%M")
        except Exception:
            return iso[:16]

    def _save_attachment(self, item: QListWidgetItem):
        att_id = item.data(Qt.UserRole)
        att = database.get_attachment(att_id)
        if not att:
            return
        path, _ = QFileDialog.getSaveFileName(self, "Save attachment", att["filename"])
        if not path:
            return
        try:
            with open(path, "wb") as f:
                f.write(att["data"])
            QMessageBox.information(self, "Saved", f"Saved to {path}")
        except Exception as e:
            QMessageBox.critical(self, "Error", str(e))
