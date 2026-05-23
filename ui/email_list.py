"""
Outlook/eM Client-style email list.

Each row shows:
  [Avatar] Sender                           Date
           Subject (bold if unread)         📎
           Preview text (1 line, muted)

Rows are taller than a standard table to fit 2 lines + avatar.
Unread rows have:
  - Blue accent bar on the left
  - Bold sender + subject
  - Date in primary color
"""
from datetime import datetime
from PyQt5.QtCore import Qt, QSize, QRect, QModelIndex, pyqtSignal
from PyQt5.QtGui import (
    QPainter, QColor, QFont, QFontMetrics, QPainterPath, QPen, QBrush,
)
from PyQt5.QtWidgets import (
    QListWidget, QListWidgetItem, QStyledItemDelegate, QStyle, QApplication,
)
from .theme import avatar_color_for, initials_of


# Roles
ROLE_EMAIL_ID = Qt.UserRole + 1
ROLE_SENDER = Qt.UserRole + 2
ROLE_SUBJECT = Qt.UserRole + 3
ROLE_DATE = Qt.UserRole + 4
ROLE_PREVIEW = Qt.UserRole + 5
ROLE_UNREAD = Qt.UserRole + 6
ROLE_HAS_ATTACH = Qt.UserRole + 7


class EmailItemDelegate(QStyledItemDelegate):
    ROW_HEIGHT = 72
    AVATAR_DIAMETER = 36
    LEFT_PAD = 10
    RIGHT_PAD = 14
    AVATAR_GAP = 12
    UNREAD_BAR_WIDTH = 3

    def sizeHint(self, option, index):
        return QSize(option.rect.width(), self.ROW_HEIGHT)

    def paint(self, painter: QPainter, option, index: QModelIndex):
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setRenderHint(QPainter.TextAntialiasing, True)

        rect = option.rect
        is_selected = bool(option.state & QStyle.State_Selected)
        is_hover = bool(option.state & QStyle.State_MouseOver)
        is_unread = bool(index.data(ROLE_UNREAD))

        # Background
        if is_selected:
            painter.fillRect(rect, QColor("#cfe4fa"))
        elif is_hover:
            painter.fillRect(rect, QColor("#f3f2f1"))
        else:
            painter.fillRect(rect, QColor("#ffffff"))

        # Bottom separator
        painter.setPen(QPen(QColor("#f3f2f1"), 1))
        painter.drawLine(rect.left(), rect.bottom(), rect.right(), rect.bottom())

        # Unread accent bar on the left
        if is_unread:
            bar = QRect(rect.left(), rect.top() + 6,
                        self.UNREAD_BAR_WIDTH, rect.height() - 12)
            painter.fillRect(bar, QColor("#0078d4"))

        # Avatar
        sender = index.data(ROLE_SENDER) or ""
        avatar_color = QColor(avatar_color_for(sender))
        initials = initials_of(sender)

        avatar_x = rect.left() + self.LEFT_PAD + self.UNREAD_BAR_WIDTH + 4
        avatar_y = rect.top() + (rect.height() - self.AVATAR_DIAMETER) // 2
        avatar_rect = QRect(avatar_x, avatar_y,
                             self.AVATAR_DIAMETER, self.AVATAR_DIAMETER)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QBrush(avatar_color))
        painter.drawEllipse(avatar_rect)

        f = QFont("Segoe UI", 10)
        f.setBold(True)
        painter.setFont(f)
        painter.setPen(QPen(QColor("#ffffff")))
        painter.drawText(avatar_rect, Qt.AlignCenter, initials)

        # Text area
        text_x = avatar_rect.right() + self.AVATAR_GAP
        text_w = rect.right() - text_x - self.RIGHT_PAD

        # Date in top-right
        date_str = self._format_date(index.data(ROLE_DATE) or "")
        f_date = QFont("Segoe UI", 9)
        if is_unread:
            f_date.setBold(True)
        painter.setFont(f_date)
        fm_date = QFontMetrics(f_date)
        date_w = fm_date.horizontalAdvance(date_str) + 2
        date_color = "#0078d4" if is_unread else "#605e5c"
        painter.setPen(QPen(QColor(date_color)))
        date_rect = QRect(rect.right() - self.RIGHT_PAD - date_w,
                          rect.top() + 8, date_w, 16)
        painter.drawText(date_rect, Qt.AlignRight | Qt.AlignVCenter, date_str)

        # Sender (top-left of text area)
        f_sender = QFont("Segoe UI", 10)
        f_sender.setBold(is_unread)
        painter.setFont(f_sender)
        fm_sender = QFontMetrics(f_sender)
        sender_display = self._clean_sender(sender)
        sender_w_avail = (date_rect.left() - 8) - text_x
        sender_elided = fm_sender.elidedText(
            sender_display, Qt.ElideRight, sender_w_avail
        )
        sender_color = "#201f1e"
        painter.setPen(QPen(QColor(sender_color)))
        sender_rect = QRect(text_x, rect.top() + 8, sender_w_avail, 16)
        painter.drawText(sender_rect, Qt.AlignLeft | Qt.AlignVCenter,
                         sender_elided)

        # Subject (middle row)
        subject = index.data(ROLE_SUBJECT) or "(no subject)"
        f_subj = QFont("Segoe UI", 9)
        f_subj.setBold(is_unread)
        painter.setFont(f_subj)
        fm_subj = QFontMetrics(f_subj)

        # If has attachment, reserve space for paperclip
        has_attach = bool(index.data(ROLE_HAS_ATTACH))
        attach_w = 16 if has_attach else 0
        subj_w_avail = text_w - attach_w
        subject_elided = fm_subj.elidedText(
            subject, Qt.ElideRight, subj_w_avail
        )
        subj_color = "#201f1e" if is_unread else "#323130"
        painter.setPen(QPen(QColor(subj_color)))
        subj_rect = QRect(text_x, rect.top() + 28, subj_w_avail, 16)
        painter.drawText(subj_rect, Qt.AlignLeft | Qt.AlignVCenter,
                         subject_elided)

        # Paperclip icon for attachments
        if has_attach:
            pc_x = text_x + subj_w_avail + 4
            pc_rect = QRect(pc_x, rect.top() + 28, attach_w, 16)
            painter.setPen(QPen(QColor("#605e5c")))
            f_pc = QFont("Segoe UI Symbol", 10)
            painter.setFont(f_pc)
            painter.drawText(pc_rect, Qt.AlignLeft | Qt.AlignVCenter, "📎")

        # Preview line (bottom)
        preview = index.data(ROLE_PREVIEW) or ""
        preview = self._clean_preview(preview)
        if preview:
            f_prev = QFont("Segoe UI", 9)
            painter.setFont(f_prev)
            fm_prev = QFontMetrics(f_prev)
            preview_elided = fm_prev.elidedText(
                preview, Qt.ElideRight, text_w
            )
            painter.setPen(QPen(QColor("#605e5c")))
            prev_rect = QRect(text_x, rect.top() + 48, text_w, 16)
            painter.drawText(prev_rect, Qt.AlignLeft | Qt.AlignVCenter,
                             preview_elided)

        painter.restore()

    @staticmethod
    def _clean_sender(sender: str) -> str:
        # "Alice Smith <alice@x.com>" -> "Alice Smith"
        s = sender.split("<")[0].strip()
        if not s:
            s = sender
        return s

    @staticmethod
    def _clean_preview(text: str) -> str:
        # Collapse whitespace
        return " ".join((text or "").split())[:200]

    @staticmethod
    def _format_date(iso: str) -> str:
        if not iso:
            return ""
        try:
            dt = datetime.fromisoformat(iso.replace("Z", "").replace("+00:00", ""))
            today = datetime.now().date()
            if dt.date() == today:
                return dt.strftime("%H:%M")
            if dt.year == today.year:
                return dt.strftime("%b %d")
            return dt.strftime("%Y-%m-%d")
        except Exception:
            return iso[:10]


class EmailListWidget(QListWidget):
    """Themed email list, ready for the delegate above."""

    request_more = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setItemDelegate(EmailItemDelegate(self))
        self.setUniformItemSizes(True)
        self.setVerticalScrollMode(self.ScrollPerPixel)
        self.setMouseTracking(True)
        self.setSelectionMode(self.SingleSelection)
        self.setFocusPolicy(Qt.StrongFocus)
        # Trigger pagination when the user scrolls near the bottom
        self.verticalScrollBar().valueChanged.connect(self._maybe_request_more)

    def _maybe_request_more(self, value: int):
        sb = self.verticalScrollBar()
        # Within 10% of bottom → ask for more
        if sb.maximum() > 0 and value >= sb.maximum() * 0.9:
            self.request_more.emit()

    def add_email(self, email: dict, folder: str):
        """Add a row from an emails-table dict."""
        item = QListWidgetItem()
        sender = (email["recipients"]
                  if folder == "sent" else email["sender"]) or ""
        item.setData(ROLE_EMAIL_ID, email["id"])
        item.setData(ROLE_SENDER, sender)
        item.setData(ROLE_SUBJECT, email.get("subject") or "(no subject)")
        item.setData(ROLE_DATE, email.get("date_received") or "")
        item.setData(ROLE_PREVIEW, email.get("preview") or "")
        item.setData(ROLE_UNREAD, not email.get("is_read"))
        item.setData(ROLE_HAS_ATTACH, bool(email.get("has_attachments")))
        self.addItem(item)

    def selected_email_id(self):
        items = self.selectedItems()
        if not items:
            return None
        return items[0].data(ROLE_EMAIL_ID)

    def mark_read_visual(self, row: int):
        item = self.item(row)
        if item:
            item.setData(ROLE_UNREAD, False)
            self.update(self.indexFromItem(item))
