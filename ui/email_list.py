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
from datetime import datetime, timezone
from PyQt5.QtCore import Qt, QSize, QRect, QModelIndex, pyqtSignal
from PyQt5.QtGui import (
    QPainter, QColor, QFont, QFontMetrics, QPainterPath, QPen, QBrush,
)
from PyQt5.QtWidgets import (
    QListWidget, QListWidgetItem, QStyledItemDelegate, QStyle, QApplication,
)
from .theme import avatar_color_for, initials_of, color as theme_color


# Roles
ROLE_EMAIL_ID = Qt.UserRole + 1
ROLE_SENDER = Qt.UserRole + 2
ROLE_SUBJECT = Qt.UserRole + 3
ROLE_DATE = Qt.UserRole + 4
ROLE_PREVIEW = Qt.UserRole + 5
ROLE_UNREAD = Qt.UserRole + 6
ROLE_HAS_ATTACH = Qt.UserRole + 7
# Threading roles
ROLE_THREAD_ROLE = Qt.UserRole + 8   # "head" | "child" | None
ROLE_THREAD_COUNT = Qt.UserRole + 9  # number of messages in the thread (head)
ROLE_THREAD_EXPANDED = Qt.UserRole + 10  # bool, head only
ROLE_THREAD_KEY = Qt.UserRole + 11   # root id grouping head + children
ROLE_FLAGGED = Qt.UserRole + 12      # bool — Outlook-style follow-up flag
ROLE_CATEGORIES = Qt.UserRole + 13   # list[{id,name,color}] — local tags


# Density presets for the email list. Each controls row height, avatar size,
# and which text lines are shown. "comfortable" reproduces the original look.
DENSITY_PRESETS = {
    "comfortable": {
        "row_height": 72,
        "avatar": 36,
        "lines": 3,          # sender/date, subject, preview
        "sender_y": 8,
        "subject_y": 28,
        "preview_y": 48,
    },
    "cozy": {
        "row_height": 54,
        "avatar": 32,
        "lines": 2,          # sender/date, subject (no preview)
        "sender_y": 8,
        "subject_y": 28,
        "preview_y": None,
    },
    "compact": {
        "row_height": 36,
        "avatar": 0,         # no avatar
        "lines": 1,          # single row: sender | subject ... date
        "sender_y": 10,
        "subject_y": 10,
        "preview_y": None,
    },
}
DEFAULT_DENSITY = "comfortable"


class EmailItemDelegate(QStyledItemDelegate):
    LEFT_PAD = 10
    RIGHT_PAD = 14
    AVATAR_GAP = 12
    UNREAD_BAR_WIDTH = 3

    def __init__(self, parent=None, density: str = DEFAULT_DENSITY):
        super().__init__(parent)
        self.set_density(density)

    def set_density(self, density: str):
        self._density = density if density in DENSITY_PRESETS else DEFAULT_DENSITY
        self._d = DENSITY_PRESETS[self._density]

    @property
    def ROW_HEIGHT(self):
        return self._d["row_height"]

    @property
    def AVATAR_DIAMETER(self):
        return self._d["avatar"]

    def sizeHint(self, option, index):
        return QSize(option.rect.width(), self._d["row_height"])

    def paint(self, painter: QPainter, option, index: QModelIndex):
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setRenderHint(QPainter.TextAntialiasing, True)

        rect = option.rect
        is_selected = bool(option.state & QStyle.State_Selected)
        is_hover = bool(option.state & QStyle.State_MouseOver)
        is_unread = bool(index.data(ROLE_UNREAD))

        # Theme-aware colors
        c_sel = theme_color("bg_selected")
        c_hover = theme_color("bg_hover")
        c_bg = theme_color("bg")
        c_sep = theme_color("border_subtle")
        c_primary = theme_color("unread")
        c_text = theme_color("text")
        c_text_subtle = theme_color("text_subtle")
        c_muted = theme_color("text_muted")

        # Background
        if is_selected:
            painter.fillRect(rect, QColor(c_sel))
        elif is_hover:
            painter.fillRect(rect, QColor(c_hover))
        else:
            painter.fillRect(rect, QColor(c_bg))

        # Bottom separator
        painter.setPen(QPen(QColor(c_sep), 1))
        painter.drawLine(rect.left(), rect.bottom(), rect.right(), rect.bottom())

        # Unread accent bar on the left
        if is_unread:
            bar = QRect(rect.left(), rect.top() + 6,
                        self.UNREAD_BAR_WIDTH, rect.height() - 12)
            painter.fillRect(bar, QColor(c_primary))

        sender = index.data(ROLE_SENDER) or ""
        d = self._d

        # ----- Threading: disclosure triangle (head) / indent (child) -----
        thread_role = index.data(ROLE_THREAD_ROLE)
        base_left = rect.left() + self.LEFT_PAD + self.UNREAD_BAR_WIDTH + 4
        if thread_role == "head":
            expanded = bool(index.data(ROLE_THREAD_EXPANDED))
            tri_w = 14
            tri_cx = base_left + 3
            tri_cy = rect.top() + rect.height() // 2
            painter.setPen(Qt.NoPen)
            painter.setBrush(QBrush(QColor(c_muted)))
            from PyQt5.QtGui import QPolygon
            from PyQt5.QtCore import QPoint
            if expanded:
                tri = QPolygon([
                    QPoint(tri_cx - 4, tri_cy - 3),
                    QPoint(tri_cx + 4, tri_cy - 3),
                    QPoint(tri_cx, tri_cy + 4),
                ])
            else:
                tri = QPolygon([
                    QPoint(tri_cx - 3, tri_cy - 4),
                    QPoint(tri_cx + 4, tri_cy),
                    QPoint(tri_cx - 3, tri_cy + 4),
                ])
            painter.drawPolygon(tri)
            base_left += tri_w
        elif thread_role == "child":
            # Indent children so they nest visually under the head.
            base_left += 22

        # Avatar (skipped entirely in compact mode where avatar size is 0)
        if self.AVATAR_DIAMETER > 0:
            avatar_color = QColor(avatar_color_for(sender))
            initials = initials_of(sender)
            avatar_x = base_left
            avatar_y = rect.top() + (rect.height() - self.AVATAR_DIAMETER) // 2
            avatar_rect = QRect(avatar_x, avatar_y,
                                self.AVATAR_DIAMETER, self.AVATAR_DIAMETER)
            painter.setPen(Qt.NoPen)
            painter.setBrush(QBrush(avatar_color))
            painter.drawEllipse(avatar_rect)
            af = QFont("Segoe UI", 10)
            af.setBold(True)
            painter.setFont(af)
            painter.setPen(QPen(QColor("#ffffff")))
            painter.drawText(avatar_rect, Qt.AlignCenter, initials)
            text_x = avatar_rect.right() + self.AVATAR_GAP
        else:
            text_x = base_left + 2

        text_w = rect.right() - text_x - self.RIGHT_PAD
        has_attach = bool(index.data(ROLE_HAS_ATTACH))
        date_str = self._format_date(index.data(ROLE_DATE) or "")

        if d["lines"] == 1:
            # ---- Compact: single row → [sender]  subject ...   📎 date ----
            self._paint_compact(
                painter, rect, index, is_unread, text_x, text_w,
                sender, date_str, has_attach,
                c_text, c_text_subtle, c_muted, c_primary,
            )
            painter.restore()
            return

        # ---- Cozy / Comfortable: multi-line layout ----
        # Date in top-right
        f_date = QFont("Segoe UI", 9)
        if is_unread:
            f_date.setBold(True)
        painter.setFont(f_date)
        fm_date = QFontMetrics(f_date)
        date_w = fm_date.horizontalAdvance(date_str) + 2
        date_color = c_primary if is_unread else c_muted
        painter.setPen(QPen(QColor(date_color)))
        date_rect = QRect(rect.right() - self.RIGHT_PAD - date_w,
                          rect.top() + d["sender_y"], date_w, 16)
        painter.drawText(date_rect, Qt.AlignRight | Qt.AlignVCenter, date_str)

        # Follow-up flag indicator (Outlook-style) on the top row, to the LEFT
        # of the date. Kept on the date row (not the subject row) so it never
        # overlaps the attachment paperclip, which lives on the subject line.
        is_flagged = bool(index.data(ROLE_FLAGGED))
        flag_w = 0
        if is_flagged:
            flag_w = 20
            painter.setFont(QFont("Segoe UI Symbol", 10))
            painter.setPen(QPen(QColor("#d13438")))  # red flag
            flag_rect = QRect(date_rect.left() - flag_w, rect.top() + d["sender_y"],
                              flag_w, 16)
            painter.drawText(flag_rect, Qt.AlignRight | Qt.AlignVCenter, "\U0001F6A9")

        # Sender
        f_sender = QFont("Segoe UI", 10)
        f_sender.setBold(is_unread)
        painter.setFont(f_sender)
        fm_sender = QFontMetrics(f_sender)
        sender_display = self._clean_sender(sender)
        sender_w_avail = (date_rect.left() - flag_w - 8) - text_x
        sender_elided = fm_sender.elidedText(
            sender_display, Qt.ElideRight, sender_w_avail
        )
        painter.setPen(QPen(QColor(c_text)))
        sender_rect = QRect(text_x, rect.top() + d["sender_y"],
                            sender_w_avail, 16)
        painter.drawText(sender_rect, Qt.AlignLeft | Qt.AlignVCenter,
                         sender_elided)

        # Subject
        subject = index.data(ROLE_SUBJECT) or "(no subject)"
        f_subj = QFont("Segoe UI", 9)
        f_subj.setBold(is_unread)
        painter.setFont(f_subj)
        fm_subj = QFontMetrics(f_subj)
        attach_w = 16 if has_attach else 0
        categories = index.data(ROLE_CATEGORIES) or []
        chip_layout, chip_w = self._category_chip_layout(
            categories, min(190, max(0, int(text_w * 0.45)))
        )
        chips_on_subject = d["preview_y"] is None and chip_w > 0
        subj_w_avail = text_w - attach_w - (chip_w + 7 if chips_on_subject else 0)
        subject_elided = fm_subj.elidedText(subject, Qt.ElideRight, subj_w_avail)
        subj_color = c_text if is_unread else c_text_subtle
        painter.setPen(QPen(QColor(subj_color)))
        subj_rect = QRect(text_x, rect.top() + d["subject_y"], subj_w_avail, 16)
        painter.drawText(subj_rect, Qt.AlignLeft | Qt.AlignVCenter,
                         subject_elided)
        if chips_on_subject:
            self._draw_category_chips(
                painter, chip_layout,
                text_x + text_w - attach_w - chip_w,
                rect.top() + d["subject_y"],
            )

        # Paperclip icon for attachments
        if has_attach:
            pc_x = text_x + text_w - attach_w + 4
            pc_rect = QRect(pc_x, rect.top() + d["subject_y"], attach_w, 16)
            painter.setPen(QPen(QColor(c_muted)))
            f_pc = QFont("Segoe UI Symbol", 10)
            painter.setFont(f_pc)
            painter.drawText(pc_rect, Qt.AlignLeft | Qt.AlignVCenter, "📎")

        # Preview line (only when the preset asks for 3 lines)
        if d["preview_y"] is not None:
            preview = self._clean_preview(index.data(ROLE_PREVIEW) or "")
            preview_w = text_w
            if chip_w:
                preview_w -= chip_w + 7
                self._draw_category_chips(
                    painter, chip_layout, text_x + preview_w + 7,
                    rect.top() + d["preview_y"],
                )
            if preview:
                f_prev = QFont("Segoe UI", 9)
                painter.setFont(f_prev)
                fm_prev = QFontMetrics(f_prev)
                preview_elided = fm_prev.elidedText(
                    preview, Qt.ElideRight, max(0, preview_w)
                )
                painter.setPen(QPen(QColor(c_muted)))
                prev_rect = QRect(
                    text_x, rect.top() + d["preview_y"], preview_w, 16
                )
                painter.drawText(prev_rect, Qt.AlignLeft | Qt.AlignVCenter,
                                 preview_elided)

        painter.restore()

    def _paint_compact(self, painter, rect, index, is_unread, text_x, text_w,
                       sender, date_str, has_attach,
                       c_text, c_text_subtle, c_muted, c_primary):
        """Single-row layout: sender (fixed width) | subject (fills) | 📎 date."""
        row_y = rect.top()
        row_h = rect.height()

        # Date (right)
        f_date = QFont("Segoe UI", 9)
        if is_unread:
            f_date.setBold(True)
        painter.setFont(f_date)
        fm_date = QFontMetrics(f_date)
        date_w = fm_date.horizontalAdvance(date_str) + 2
        painter.setPen(QPen(QColor(c_primary if is_unread else c_muted)))
        date_rect = QRect(rect.right() - self.RIGHT_PAD - date_w, row_y,
                          date_w, row_h)
        painter.drawText(date_rect, Qt.AlignRight | Qt.AlignVCenter, date_str)

        # Paperclip just left of date
        right_limit = date_rect.left() - 6
        if bool(index.data(ROLE_FLAGGED)):
            flag_rect = QRect(right_limit - 18, row_y, 18, row_h)
            painter.setPen(QPen(QColor("#d13438")))
            painter.setFont(QFont("Segoe UI Symbol", 9))
            painter.drawText(flag_rect, Qt.AlignRight | Qt.AlignVCenter, "🚩")
            right_limit -= 20
        if has_attach:
            pc_rect = QRect(right_limit - 16, row_y, 16, row_h)
            painter.setPen(QPen(QColor(c_muted)))
            painter.setFont(QFont("Segoe UI Symbol", 9))
            painter.drawText(pc_rect, Qt.AlignRight | Qt.AlignVCenter, "📎")
            right_limit -= 18

        categories = index.data(ROLE_CATEGORIES) or []
        chip_layout, chip_w = self._category_chip_layout(
            categories, min(170, max(0, int((right_limit - text_x) * 0.35)))
        )
        if chip_w:
            self._draw_category_chips(
                painter, chip_layout, right_limit - chip_w,
                row_y + (row_h - 16) // 2,
            )
            right_limit -= chip_w + 7

        # Sender (fixed-width column on the left)
        f_sender = QFont("Segoe UI", 9)
        f_sender.setBold(is_unread)
        painter.setFont(f_sender)
        fm_sender = QFontMetrics(f_sender)
        sender_display = self._clean_sender(sender)
        sender_col_w = min(200, max(120, int((right_limit - text_x) * 0.32)))
        sender_elided = fm_sender.elidedText(
            sender_display, Qt.ElideRight, sender_col_w
        )
        painter.setPen(QPen(QColor(c_text)))
        sender_rect = QRect(text_x, row_y, sender_col_w, row_h)
        painter.drawText(sender_rect, Qt.AlignLeft | Qt.AlignVCenter,
                         sender_elided)

        # Subject (fills remaining space)
        subj_x = text_x + sender_col_w + 12
        subj_w = right_limit - subj_x
        if subj_w > 20:
            subject = index.data(ROLE_SUBJECT) or "(no subject)"
            f_subj = QFont("Segoe UI", 9)
            f_subj.setBold(is_unread)
            painter.setFont(f_subj)
            fm_subj = QFontMetrics(f_subj)
            subject_elided = fm_subj.elidedText(subject, Qt.ElideRight, subj_w)
            painter.setPen(QPen(QColor(c_text if is_unread else c_text_subtle)))
            subj_rect = QRect(subj_x, row_y, subj_w, row_h)
            painter.drawText(subj_rect, Qt.AlignLeft | Qt.AlignVCenter,
                             subject_elided)

    @staticmethod
    def _category_text_color(color: QColor) -> QColor:
        luminance = (0.299 * color.red() + 0.587 * color.green()
                     + 0.114 * color.blue())
        return QColor("#202020" if luminance > 165 else "#ffffff")

    def _category_chip_layout(self, categories, max_width):
        if not categories or max_width < 24:
            return [], 0
        font = QFont("Segoe UI", 8)
        metrics = QFontMetrics(font)
        layout = []
        used = 0
        for index, category in enumerate(categories):
            name = str(category.get("name") or "")
            width = min(92, metrics.horizontalAdvance(name) + 14)
            gap = 4 if layout else 0
            remaining = len(categories) - index - 1
            overflow_width = metrics.horizontalAdvance(f"+{remaining}") + 12
            reserve = (4 + overflow_width) if remaining else 0
            if used + gap + width + reserve > max_width:
                hidden = len(categories) - index
                label = f"+{hidden}"
                extra = metrics.horizontalAdvance(label) + 12
                if used + (4 if layout else 0) + extra <= max_width:
                    layout.append((label, "#767676", extra))
                    used += (4 if used else 0) + extra
                break
            layout.append((name, category.get("color") or "#767676", width))
            used += gap + width
        return layout, used

    def _draw_category_chips(self, painter, layout, x, y):
        painter.save()
        painter.setFont(QFont("Segoe UI", 8))
        cursor = x
        for index, (label, color_value, width) in enumerate(layout):
            if index:
                cursor += 4
            color = QColor(color_value)
            chip = QRect(cursor, y, width, 16)
            painter.setPen(Qt.NoPen)
            painter.setBrush(QBrush(color))
            painter.drawRoundedRect(chip, 4, 4)
            painter.setPen(QPen(self._category_text_color(color)))
            painter.drawText(chip.adjusted(6, 0, -6, 0),
                             Qt.AlignLeft | Qt.AlignVCenter, label)
            cursor += width
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
            # Parse the ISO timestamp, interpreting 'Z' as UTC
            dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
            # Convert to local time so the UI shows the user's timezone
            dt = dt.astimezone()
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
    left_item_pressed = pyqtSignal(QListWidgetItem)

    def __init__(self, parent=None):
        super().__init__(parent)
        from core import config
        density = config.get("list_density", DEFAULT_DENSITY)
        self._delegate = EmailItemDelegate(self, density=density)
        self.setItemDelegate(self._delegate)
        self.setUniformItemSizes(True)
        self.setVerticalScrollMode(self.ScrollPerPixel)
        self.setMouseTracking(True)
        self.setSelectionMode(self.SingleSelection)
        self.setFocusPolicy(Qt.StrongFocus)
        # Threading: maps a thread_key -> list of child email dicts (the
        # messages below the head), plus the set of expanded keys.
        self._thread_children: dict = {}
        self._expanded_threads: set = set()
        self._folder = "inbox"
        # Trigger pagination when the user scrolls near the bottom
        self.verticalScrollBar().valueChanged.connect(self._maybe_request_more)

    def set_density(self, density: str):
        """Switch the list density (compact/cozy/comfortable) live."""
        self._delegate.set_density(density)
        # Force the view to recompute row heights and repaint.
        self.setItemDelegate(self._delegate)
        self.doItemsLayout()
        self.viewport().update()

    def current_density(self) -> str:
        return self._delegate._density

    def _maybe_request_more(self, value: int):
        sb = self.verticalScrollBar()
        # Within 10% of bottom → ask for more
        if sb.maximum() > 0 and value >= sb.maximum() * 0.9:
            self.request_more.emit()

    def add_email(self, email: dict, folder: str):
        """Add a row from an emails-table dict.

        Thread metadata (optional) is read from these keys when present:
          _thread_role  : "head" | "child"
          _thread_count : int (head only)
          _thread_key   : grouping id shared by head + its children
          _thread_expanded : bool (head only)
        """
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
        item.setData(ROLE_THREAD_ROLE, email.get("_thread_role"))
        item.setData(ROLE_THREAD_COUNT, email.get("_thread_count"))
        item.setData(ROLE_THREAD_KEY, email.get("_thread_key"))
        item.setData(ROLE_THREAD_EXPANDED, bool(email.get("_thread_expanded")))
        item.setData(ROLE_FLAGGED, bool(email.get("is_flagged")))
        item.setData(ROLE_CATEGORIES, email.get("categories") or [])
        self.addItem(item)
        return item

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

    def set_flagged_visual(self, email_id, flagged: bool):
        """Update the flag indicator for the row with this email_id."""
        for r in range(self.count()):
            it = self.item(r)
            if it is not None and it.data(ROLE_EMAIL_ID) == email_id:
                it.setData(ROLE_FLAGGED, bool(flagged))
                self.update(self.indexFromItem(it))
                break

    def email_ids_for_item(self, item) -> list:
        """Return every email id represented by a row. For a collapsed thread
        head that's the head plus all its children; for a normal row it's just
        that one id. Lets actions (mark read, flag) apply to the whole
        conversation the user sees, not only the visible head."""
        if item is None:
            return []
        ids = [item.data(ROLE_EMAIL_ID)]
        if item.data(ROLE_THREAD_ROLE) == "head":
            key = item.data(ROLE_THREAD_KEY)
            for child in self._thread_children.get(key, []):
                cid = child.get("id")
                if cid is not None:
                    ids.append(cid)
        return [i for i in ids if i is not None]

    # ---------- Threading: expand / collapse ----------
    def reset_threads(self, folder: str):
        """Clear stored thread state before (re)populating the list."""
        self.clear()
        self._thread_children = {}
        self._folder = folder

    def register_thread(self, thread_key, children: list):
        """Register the child messages (everything below the head) for a
        thread so they can be inserted when the head is expanded."""
        self._thread_children[thread_key] = children

    def _row_is_thread_toggle(self, item, pos) -> bool:
        """True if a click at viewport pos landed on a thread head's
        disclosure triangle (left ~22px of the row)."""
        if item is None:
            return False
        if item.data(ROLE_THREAD_ROLE) != "head":
            return False
        rect = self.visualItemRect(item)
        # Triangle sits in the first ~24px after the left pad.
        left = rect.left() + EmailItemDelegate.LEFT_PAD
        return left - 4 <= pos.x() <= left + 22

    def mousePressEvent(self, event):
        item = self.itemAt(event.pos())
        if item is not None and self._row_is_thread_toggle(item, event.pos()):
            self.toggle_thread(item)
            event.accept()
            return
        super().mousePressEvent(event)
        if event.button() == Qt.LeftButton and item is not None:
            self.left_item_pressed.emit(item)

    def toggle_thread(self, head_item):
        """Expand or collapse the thread whose head is head_item."""
        key = head_item.data(ROLE_THREAD_KEY)
        if key is None:
            return
        head_row = self.row(head_item)
        if key in self._expanded_threads:
            # Collapse: remove child rows directly below the head.
            self._expanded_threads.discard(key)
            head_item.setData(ROLE_THREAD_EXPANDED, False)
            r = head_row + 1
            while r < self.count():
                it = self.item(r)
                if it is not None and it.data(ROLE_THREAD_KEY) == key \
                        and it.data(ROLE_THREAD_ROLE) == "child":
                    self.takeItem(r)
                else:
                    break
        else:
            # Expand: insert child rows below the head.
            self._expanded_threads.add(key)
            head_item.setData(ROLE_THREAD_EXPANDED, True)
            children = self._thread_children.get(key, [])
            insert_at = head_row + 1
            for child in children:
                child = dict(child)
                child["_thread_role"] = "child"
                child["_thread_key"] = key
                it = self._make_item(child, self._folder)
                self.insertItem(insert_at, it)
                insert_at += 1
        self.update(self.indexFromItem(head_item))

    def _make_item(self, email: dict, folder: str) -> QListWidgetItem:
        """Build a QListWidgetItem (used for inserting thread children)."""
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
        item.setData(ROLE_THREAD_ROLE, email.get("_thread_role"))
        item.setData(ROLE_THREAD_KEY, email.get("_thread_key"))
        item.setData(ROLE_FLAGGED, bool(email.get("is_flagged")))
        item.setData(ROLE_CATEGORIES, email.get("categories") or [])
        return item
