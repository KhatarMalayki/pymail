"""
Compose Email dialog (To/Cc/Bcc, subject, body, attachments).
"""
import os
from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QFormLayout, QLineEdit, QComboBox,
    QTextEdit, QPushButton, QFileDialog, QMessageBox, QListWidget,
    QListWidgetItem, QLabel, QToolBar, QAction, QWidget, QSizePolicy,
    QCompleter, QFontComboBox, QSpinBox, QColorDialog, QToolButton, QFrame,
)
from PyQt5.QtCore import Qt, QThread, pyqtSignal, QTimer
from PyQt5.QtGui import QIcon, QFont, QTextCharFormat, QTextListFormat, QColor
from core import database, smtp_client, contacts
from .recipient_completer import attach_to as attach_completer


class SendWorker(QThread):
    finished_ok = pyqtSignal(bytes)
    finished_err = pyqtSignal(str)

    def __init__(self, account, msg):
        super().__init__()
        self.account = account
        self.msg = msg

    def run(self):
        try:
            raw = smtp_client.send_email(self.account, self.msg)
            self.finished_ok.emit(raw)
        except Exception as e:
            self.finished_err.emit(str(e))


class ComposeDialog(QDialog):
    sent = pyqtSignal(int)  # account_id
    draft_saved = pyqtSignal(int)  # account_id (refresh Drafts folder)

    AUTOSAVE_INTERVAL_MS = 10_000  # 10 seconds

    def __init__(self, parent=None, account_id=None, prefill: dict = None,
                 draft_id: int | None = None):
        super().__init__(parent)
        self.setWindowTitle("Compose")
        self.resize(820, 620)
        self.attachments = []
        self.worker = None
        self.draft_id = draft_id
        self._outbox_id = None
        self._dirty = False
        self._sent = False  # if True, dialog closing should NOT save as draft
        self._build_ui()
        self._load_accounts(account_id)
        if prefill:
            self._apply_prefill(prefill)
        else:
            # New blank email: insert signature automatically
            self._apply_signature()
        self._wire_dirty_tracking()

        # Periodic auto-save
        self._autosave_timer = QTimer(self)
        self._autosave_timer.timeout.connect(self._autosave)
        self._autosave_timer.start(self.AUTOSAVE_INTERVAL_MS)

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Toolbar (top action bar)
        toolbar = QToolBar()
        toolbar.setStyleSheet(
            "QToolBar { background:#faf9f8; border:none; "
            "border-bottom:1px solid #e1dfdd; padding:6px 8px; }"
        )
        send_act = QAction("📤  Send", self); send_act.triggered.connect(self._send)
        attach_act = QAction("📎  Attach", self); attach_act.triggered.connect(self._add_attachment)
        toolbar.addAction(send_act)
        toolbar.addAction(attach_act)
        # Make Send button stand out
        send_btn = toolbar.widgetForAction(send_act)
        if send_btn is not None:
            send_btn.setStyleSheet(
                "QToolButton { background:#0078d4; color:white; "
                "padding:7px 14px; border-radius:4px; font-weight:600; }"
                "QToolButton:hover { background:#106ebe; }"
                "QToolButton:pressed { background:#005a9e; }"
            )
        layout.addWidget(toolbar)

        # Header form area (white background, padded)
        header = QWidget()
        header.setStyleSheet(
            "QWidget { background:#ffffff; border-bottom:1px solid #e1dfdd; }"
        )
        form = QFormLayout(header)
        form.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)
        form.setContentsMargins(20, 14, 20, 14)
        form.setSpacing(8)
        form.setHorizontalSpacing(14)

        self.account_combo = QComboBox()
        self.to_edit = QLineEdit(); self.to_edit.setPlaceholderText("recipient@example.com, ...")
        self.cc_edit = QLineEdit(); self.cc_edit.setPlaceholderText("(optional)")
        self.bcc_edit = QLineEdit(); self.bcc_edit.setPlaceholderText("(optional)")
        self.subject_edit = QLineEdit()
        self.subject_edit.setPlaceholderText("Subject")
        # Slightly larger subject font
        sub_font = QFont("Segoe UI", 10)
        sub_font.setWeight(QFont.DemiBold)
        self.subject_edit.setFont(sub_font)

        # Contact autocomplete (shared list, attached to all 3 recipient fields)
        self._completer_items = contacts.contacts_for_completer()
        for fld in (self.to_edit, self.cc_edit, self.bcc_edit):
            attach_completer(fld, self._completer_items)

        # Style label color
        def _row(label_text, widget):
            lbl = QLabel(label_text)
            lbl.setStyleSheet("color:#605e5c; font-weight:600;")
            form.addRow(lbl, widget)
        _row("From", self.account_combo)
        _row("To", self.to_edit)
        _row("Cc", self.cc_edit)
        _row("Bcc", self.bcc_edit)
        _row("Subject", self.subject_edit)
        layout.addWidget(header)

        # Attachments strip (only visible when there's at least one)
        self.att_widget = QWidget()
        self.att_widget.setObjectName("attachmentStrip")
        self.att_widget.setStyleSheet(
            "#attachmentStrip { "
            "background:#fffbe6; border-bottom:1px solid #f0d878; "
            "}"
            "#attachmentStrip QLabel { color:#604000; background:transparent; }"
            "#attachmentStrip QListWidget { "
            "background:#ffffff; border:1px solid #e6d090; "
            "border-radius:4px; padding:2px; }"
            "#attachmentStrip QListWidget::item { "
            "color:#201f1e; padding:4px 8px; border-bottom:1px solid #f3f2f1; "
            "}"
            "#attachmentStrip QListWidget::item:last { border-bottom:none; }"
            "#attachmentStrip QListWidget::item:selected { "
            "background:#fff3c0; color:#201f1e; }"
            "#attachmentStrip QPushButton { "
            "background:#ffffff; color:#201f1e; "
            "border:1px solid #c8c6c4; border-radius:4px; "
            "padding:6px 14px; min-width:70px; }"
            "#attachmentStrip QPushButton:hover { "
            "background:#fff3c0; border-color:#c89000; }"
        )
        att_row = QHBoxLayout(self.att_widget)
        att_row.setContentsMargins(20, 8, 20, 8)
        att_row.setSpacing(10)

        att_icon = QLabel("📎")
        att_icon.setStyleSheet("font-size:14pt;")
        att_row.addWidget(att_icon, 0, Qt.AlignTop)

        self.att_list = QListWidget()
        self.att_list.setMaximumHeight(80)
        # Allow multi-selection so user can remove several at once
        from PyQt5.QtWidgets import QAbstractItemView, QShortcut
        from PyQt5.QtGui import QKeySequence
        self.att_list.setSelectionMode(QAbstractItemView.ExtendedSelection)
        # Allow Delete key to remove the selected attachment
        del_sc = QShortcut(QKeySequence("Delete"), self.att_list)
        del_sc.activated.connect(self._remove_attachment)
        # Ctrl+A select all
        select_all_sc = QShortcut(QKeySequence("Ctrl+A"), self.att_list)
        select_all_sc.activated.connect(self.att_list.selectAll)
        # Double-click an item to remove it instantly
        self.att_list.itemDoubleClicked.connect(
            lambda _: self._remove_attachment()
        )
        att_row.addWidget(self.att_list, 1)

        rm_btn = QPushButton("Remove")
        rm_btn.setToolTip(
            "Remove the selected attachment(s).\n"
            "Tip: Ctrl+click or Shift+click to select multiple, then Remove "
            "(or press Delete, or double-click to remove one)."
        )
        rm_btn.clicked.connect(self._remove_attachment)
        att_row.addWidget(rm_btn, 0, Qt.AlignTop)

        layout.addWidget(self.att_widget)
        self.att_widget.setVisible(False)

        # Body
        from .paste_aware_edit import PasteAwareTextEdit
        self.body_edit = PasteAwareTextEdit()
        self.body_edit.setPlaceholderText("Write your message...")
        self.body_edit.setStyleSheet(
            "QTextEdit { background:#ffffff; border:none; padding:16px 20px; "
            "font-size:10pt; }"
        )

        # Formatting toolbar (Outlook-style: font, size, B/I/U, color, list)
        fmt_bar = self._build_formatting_toolbar()
        layout.addWidget(fmt_bar)
        layout.addWidget(self.body_edit, 1)

        # Status (bottom)
        self.status_label = QLabel("")
        self.status_label.setStyleSheet(
            "color:#605e5c; padding:6px 20px; "
            "border-top:1px solid #e1dfdd; background:#faf9f8;"
        )
        layout.addWidget(self.status_label)

    def _build_formatting_toolbar(self) -> QFrame:
        """Build an Outlook-style formatting toolbar that drives the
        body QTextEdit (font, size, B/I/U/strike, color, list, alignment,
        clear formatting)."""
        frame = QFrame()
        frame.setStyleSheet(
            "QFrame { background:#faf9f8; border:none; "
            "border-bottom:1px solid #e1dfdd; }"
            "QToolButton { background:transparent; border:1px solid transparent; "
            "padding:4px 6px; border-radius:4px; min-width:24px; }"
            "QToolButton:hover { background:#edebe9; border:1px solid #d2d0ce; }"
            "QToolButton:checked { background:#cfe4fa; border:1px solid #9ec7f0; }"
            "QToolButton:pressed { background:#c1deff; }"
            "QFontComboBox, QComboBox, QSpinBox { "
            "background:#ffffff; border:1px solid #d2d0ce; border-radius:4px; "
            "padding:2px 4px; }"
        )
        row = QHBoxLayout(frame)
        row.setContentsMargins(10, 6, 10, 6)
        row.setSpacing(4)

        # Font family
        self.font_combo = QFontComboBox()
        self.font_combo.setMaximumWidth(180)
        self.font_combo.setCurrentFont(QFont("Segoe UI"))
        self.font_combo.currentFontChanged.connect(self._on_font_family)
        row.addWidget(self.font_combo)

        # Font size
        self.size_combo = QComboBox()
        self.size_combo.setEditable(True)
        self.size_combo.setMaximumWidth(64)
        for s in (8, 9, 10, 11, 12, 14, 16, 18, 20, 24, 28, 32, 36, 48, 72):
            self.size_combo.addItem(str(s))
        self.size_combo.setCurrentText("10")
        self.size_combo.activated.connect(self._on_font_size)
        self.size_combo.lineEdit().editingFinished.connect(self._on_font_size)
        row.addWidget(self.size_combo)

        row.addWidget(self._sep())

        # Bold / Italic / Underline / Strike
        self.bold_btn = self._fmt_btn("B", "Bold (Ctrl+B)", self._toggle_bold, bold=True)
        self.italic_btn = self._fmt_btn("I", "Italic (Ctrl+I)", self._toggle_italic, italic=True)
        self.under_btn = self._fmt_btn("U", "Underline (Ctrl+U)", self._toggle_underline, underline=True)
        self.strike_btn = self._fmt_btn("S", "Strikethrough", self._toggle_strike)
        for b in (self.bold_btn, self.italic_btn, self.under_btn, self.strike_btn):
            row.addWidget(b)

        row.addWidget(self._sep())

        # Color
        color_btn = QToolButton()
        color_btn.setText("A")
        f = color_btn.font(); f.setBold(True); color_btn.setFont(f)
        color_btn.setToolTip("Text color")
        color_btn.clicked.connect(self._pick_color)
        self._color_btn = color_btn
        row.addWidget(color_btn)

        row.addWidget(self._sep())

        # Bullet / Numbered list
        bullet_btn = QToolButton()
        bullet_btn.setText("•")
        bullet_btn.setToolTip("Bulleted list")
        bullet_btn.clicked.connect(lambda: self._toggle_list(QTextListFormat.ListDisc))
        row.addWidget(bullet_btn)

        num_btn = QToolButton()
        num_btn.setText("1.")
        num_btn.setToolTip("Numbered list")
        num_btn.clicked.connect(lambda: self._toggle_list(QTextListFormat.ListDecimal))
        row.addWidget(num_btn)

        row.addWidget(self._sep())

        # Alignment
        for label, tip, align in [
            ("⇤", "Align left", Qt.AlignLeft),
            ("⇔", "Align center", Qt.AlignCenter),
            ("⇥", "Align right", Qt.AlignRight),
        ]:
            b = QToolButton()
            b.setText(label)
            b.setToolTip(tip)
            b.clicked.connect(lambda _=False, a=align: self.body_edit.setAlignment(a))
            row.addWidget(b)

        row.addWidget(self._sep())

        # Clear formatting
        clear_btn = QToolButton()
        clear_btn.setText("⌫A")
        clear_btn.setToolTip("Clear formatting")
        clear_btn.clicked.connect(self._clear_format)
        row.addWidget(clear_btn)

        row.addStretch(1)

        # Sync UI buttons when the cursor moves into a different format
        self.body_edit.cursorPositionChanged.connect(self._sync_format_buttons)
        self.body_edit.currentCharFormatChanged.connect(self._sync_format_buttons_from_fmt)
        return frame

    def _sep(self):
        sep = QFrame()
        sep.setFrameShape(QFrame.VLine)
        sep.setFrameShadow(QFrame.Sunken)
        sep.setStyleSheet("color:#d2d0ce;")
        sep.setMaximumWidth(8)
        return sep

    def _fmt_btn(self, label, tip, slot, bold=False, italic=False, underline=False):
        b = QToolButton()
        b.setText(label)
        b.setToolTip(tip)
        b.setCheckable(True)
        f = b.font()
        if bold: f.setBold(True)
        if italic: f.setItalic(True)
        if underline: f.setUnderline(True)
        b.setFont(f)
        b.clicked.connect(slot)
        return b

    # ---- Format actions ----
    def _on_font_family(self, font: QFont):
        fmt = QTextCharFormat()
        fmt.setFontFamily(font.family())
        self._merge_format(fmt)

    def _on_font_size(self, *_):
        try:
            sz = float(self.size_combo.currentText())
        except (TypeError, ValueError):
            return
        fmt = QTextCharFormat()
        fmt.setFontPointSize(sz)
        self._merge_format(fmt)

    def _toggle_bold(self):
        fmt = QTextCharFormat()
        cur_w = self.body_edit.fontWeight()
        fmt.setFontWeight(QFont.Normal if cur_w > QFont.Normal else QFont.Bold)
        self._merge_format(fmt)

    def _toggle_italic(self):
        fmt = QTextCharFormat()
        fmt.setFontItalic(not self.body_edit.fontItalic())
        self._merge_format(fmt)

    def _toggle_underline(self):
        fmt = QTextCharFormat()
        fmt.setFontUnderline(not self.body_edit.fontUnderline())
        self._merge_format(fmt)

    def _toggle_strike(self):
        fmt = QTextCharFormat()
        fmt.setFontStrikeOut(not self.body_edit.currentCharFormat().fontStrikeOut())
        self._merge_format(fmt)

    def _pick_color(self):
        color = QColorDialog.getColor(
            self.body_edit.textColor(), self, "Pick text color",
        )
        if not color.isValid():
            return
        fmt = QTextCharFormat()
        fmt.setForeground(color)
        self._merge_format(fmt)
        # Tint the toolbar "A" so user sees current color
        self._color_btn.setStyleSheet(
            f"QToolButton {{ color:{color.name()}; font-weight:bold; }}"
        )

    def _toggle_list(self, style):
        cursor = self.body_edit.textCursor()
        cursor.beginEditBlock()
        try:
            current_list = cursor.currentList()
            if current_list and current_list.format().style() == style:
                # toggling off: convert each item back to a normal block
                fmt = current_list.format()
                # easiest path: set list style to ListStyleUndefined
                lf = QTextListFormat()
                lf.setStyle(QTextListFormat.ListStyleUndefined)
                # Workaround: remove from the list by turning into plain block
                blk = cursor.block()
                current_list.removeItem(current_list.itemNumber(blk))
            else:
                lf = QTextListFormat()
                lf.setStyle(style)
                lf.setIndent(1)
                cursor.createList(lf)
        finally:
            cursor.endEditBlock()

    def _clear_format(self):
        cursor = self.body_edit.textCursor()
        if not cursor.hasSelection():
            return
        fmt = QTextCharFormat()
        fmt.setFont(QFont("Segoe UI", 10))
        cursor.setCharFormat(fmt)

    def _merge_format(self, fmt: QTextCharFormat):
        cursor = self.body_edit.textCursor()
        if cursor.hasSelection():
            cursor.mergeCharFormat(fmt)
        self.body_edit.mergeCurrentCharFormat(fmt)

    def _sync_format_buttons(self):
        self._sync_format_buttons_from_fmt(self.body_edit.currentCharFormat())

    def _sync_format_buttons_from_fmt(self, fmt: QTextCharFormat):
        try:
            self.bold_btn.setChecked(fmt.fontWeight() > QFont.Normal)
            self.italic_btn.setChecked(fmt.fontItalic())
            self.under_btn.setChecked(fmt.fontUnderline())
            self.strike_btn.setChecked(fmt.fontStrikeOut())
            family = fmt.fontFamily()
            if family:
                self.font_combo.blockSignals(True)
                self.font_combo.setCurrentFont(QFont(family))
                self.font_combo.blockSignals(False)
            sz = fmt.fontPointSize()
            if sz > 0:
                self.size_combo.blockSignals(True)
                self.size_combo.setCurrentText(str(int(sz)))
                self.size_combo.blockSignals(False)
        except Exception:
            pass

    def _load_accounts(self, preferred_id):
        self.accounts = database.list_accounts()
        if not self.accounts:
            QMessageBox.warning(self, "No account", "Please add an account first.")
            self.reject()
            return
        for a in self.accounts:
            self.account_combo.addItem(f'{a["name"]} <{a["email"]}>', a["id"])
        if preferred_id:
            idx = self.account_combo.findData(preferred_id)
            if idx >= 0:
                self.account_combo.setCurrentIndex(idx)
        # Apply signature for the currently selected account when starting
        # a fresh compose (no prefill body, not a draft re-open).
        self.account_combo.currentIndexChanged.connect(
            self._maybe_swap_signature
        )

    def _signature_for_current(self) -> str:
        if not self.accounts:
            return ""
        acc = database.get_account(self.account_combo.currentData())
        return (acc.get("signature") or "").strip() if acc else ""

    @staticmethod
    def _is_html(s: str) -> bool:
        return "<" in s and ">" in s and (
            "<html" in s.lower() or "<body" in s.lower()
            or "<p " in s.lower() or "<p>" in s.lower()
            or "<span" in s.lower() or "<div" in s.lower()
        )

    def _apply_signature(self, *_):
        """Insert the signature into a fresh compose body.

        Layout:
            <user typing area — default font, no inherited formatting>
            <empty line>
            <signature>

        The signature is inserted as HTML at the END, but we explicitly
        reset the editor's current char format afterwards and move the
        cursor to the TOP. This prevents the signature's font color/family
        (often blue links or branded fonts) from "leaking" into what the
        user types. Without this fix, typing in a fresh compose would
        come out in the signature's color (the user reported text turning
        blue and needing two enters).
        """
        sig = self._signature_for_current()
        if not sig:
            return
        body_plain = self.body_edit.toPlainText()
        # Cheap idempotency check: bail if a recognizable chunk of the
        # signature is already in the body.
        sig_plain_marker = self._sig_plain_excerpt(sig)
        if sig_plain_marker and sig_plain_marker in body_plain:
            return

        from PyQt5.QtGui import QTextCharFormat, QTextBlockFormat, QFont

        cursor = self.body_edit.textCursor()
        cursor.movePosition(cursor.End)

        # Insert exactly one paragraph break before the signature so there
        # is one empty line for the user to start typing in. (Was 2; that's
        # why the user had to press Enter twice to "escape" the formatting.)
        if self._is_html(sig):
            cursor.insertBlock()
            cursor.insertHtml(sig)
        else:
            cursor.insertText("\n-- \n" + sig + "\n")

        # Reset the editor's *current* char/block format to the document
        # default, then place the caret at the very top. The user's first
        # keystroke will use these default formats — not the signature's.
        cursor.movePosition(cursor.Start)
        default_char = QTextCharFormat()
        default_char.setFont(QFont("Segoe UI", 10))
        default_char.clearForeground()
        default_char.clearBackground()
        cursor.setCharFormat(default_char)

        default_block = QTextBlockFormat()
        cursor.setBlockFormat(default_block)

        self.body_edit.setTextCursor(cursor)
        self.body_edit.setCurrentCharFormat(default_char)

    def _prepend_signature_for_reply(self):
        """Insert signature at the TOP of the body, before the quoted
        block. This is what Outlook does for replies/forwards — user
        types above the signature, signature sits above the quoted
        original, original is at the bottom."""
        sig = self._signature_for_current()
        if not sig:
            return
        body_plain = self.body_edit.toPlainText()
        sig_marker = self._sig_plain_excerpt(sig)
        if sig_marker and sig_marker in body_plain:
            return  # already there

        from PyQt5.QtGui import QTextCharFormat, QTextBlockFormat, QFont

        cursor = self.body_edit.textCursor()
        cursor.movePosition(cursor.Start)

        # One empty line at top, then signature, then a separator line.
        if self._is_html(sig):
            cursor.insertBlock()
            cursor.insertHtml(sig)
            cursor.insertBlock()
        else:
            cursor.insertText("\n-- \n" + sig + "\n\n")

        # Caret to the very top + default char format so the user types
        # cleanly above the signature.
        cursor.movePosition(cursor.Start)
        default_char = QTextCharFormat()
        default_char.setFont(QFont("Segoe UI", 10))
        default_char.clearForeground()
        default_char.clearBackground()
        cursor.setCharFormat(default_char)

        default_block = QTextBlockFormat()
        cursor.setBlockFormat(default_block)

        self.body_edit.setTextCursor(cursor)
        self.body_edit.setCurrentCharFormat(default_char)

    @staticmethod
    def _sig_plain_excerpt(sig: str) -> str:
        """Return a short plain-text excerpt from the signature for
        idempotency checks."""
        if "<" in sig and ">" in sig:
            # Strip tags very crudely
            import re
            text = re.sub(r"<[^>]+>", " ", sig)
            text = re.sub(r"\s+", " ", text).strip()
        else:
            text = sig.strip()
        return text[:40]

    def _body_has_rich_content(self) -> bool:
        """True if the body has any non-default formatting or images."""
        from PyQt5.QtGui import QFont
        doc = self.body_edit.document()
        for i in range(doc.blockCount()):
            block = doc.findBlockByNumber(i)
            it = block.begin()
            while not it.atEnd():
                frag = it.fragment()
                if frag.isValid():
                    fmt = frag.charFormat()
                    if (fmt.fontWeight() != QFont.Normal
                            or fmt.fontItalic()
                            or fmt.fontUnderline()
                            or fmt.isImageFormat()):
                        return True
                    color = fmt.foreground().color()
                    if color.isValid() and color.name() not in ("#000000", "#000"):
                        return True
                it += 1
        return False

    def _maybe_swap_signature(self, *_):
        """Called when the From account changes. Swap signature accordingly."""
        if self.draft_id:
            # Don't touch a re-opened draft
            return
        # If body has a quoted block (reply/forward), prepend; otherwise append
        body_html = self.body_edit.toHtml()
        is_reply = (
            "<b>From:</b>" in body_html
            or self.body_edit.toPlainText().lstrip().startswith(">")
        )
        if is_reply:
            self._prepend_signature_for_reply()
        else:
            self._apply_signature()

    def _apply_prefill(self, p):
        if "to" in p:
            self.to_edit.setText(p["to"])
        if "cc" in p:
            self.cc_edit.setText(p["cc"])
        if "bcc" in p:
            self.bcc_edit.setText(p["bcc"])
        if "subject" in p:
            self.subject_edit.setText(p["subject"])

        # Body: prefer HTML when provided so quoted reply/forward keeps
        # original formatting, signatures, embedded images, and tables.
        body_html = p.get("body_html")
        body_plain = p.get("body")  # legacy plain-text path
        if body_html:
            self.body_edit.setHtml(body_html)
        elif body_plain is not None:
            self.body_edit.setPlainText(body_plain)

        # Forwarded attachments — attach binary payloads from the DB
        for att in (p.get("forwarded_attachments") or []):
            self._add_forwarded_attachment(att)

        # Prefill is the saved state — not yet "dirty" until user edits.
        self._dirty = False

        # Insert signature ABOVE the quoted block. We do this for replies,
        # reply-all, forward — i.e. anytime the body was prefilled by us
        # but not when reopening a draft (drafts already contain the user's
        # exact saved state).
        if not self.draft_id and (body_html or body_plain):
            self._prepend_signature_for_reply()

    def _add_forwarded_attachment(self, att: dict):
        """Add an attachment dict (from database.get_attachments_for_email)
        directly to the outgoing list, no disk file needed."""
        # Avoid duplicates (e.g. if the user re-opens a forward draft)
        for existing in self.attachments:
            if (isinstance(existing, dict)
                    and existing.get("filename") == att.get("filename")
                    and existing.get("size") == att.get("size")):
                return
        # Keep the original DB id so removal matches up cleanly
        att_id = att.get("id")
        self.attachments.append({
            "id": att_id,
            "filename": att.get("filename") or "attachment.bin",
            "mime_type": att.get("mime_type") or "application/octet-stream",
            "size": att.get("size") or 0,
            "data": att.get("data") or b"",
        })
        size_kb = (att.get("size") or 0) / 1024
        # ↻ prefix so it's visually distinct from disk-picked attachments
        item = QListWidgetItem(
            f"↻  {att.get('filename')}  ({size_kb:.1f} KB)"
        )
        item.setData(Qt.UserRole, ("forwarded", att_id))
        self.att_list.addItem(item)
        self.att_widget.setVisible(True)

    def _add_attachment(self):
        files, _ = QFileDialog.getOpenFileNames(self, "Select files")
        for f in files:
            if f not in self.attachments:
                self.attachments.append(f)
                size_kb = os.path.getsize(f) / 1024
                item = QListWidgetItem(f"{os.path.basename(f)}  ({size_kb:.1f} KB)")
                item.setData(Qt.UserRole, f)
                self.att_list.addItem(item)
        self.att_widget.setVisible(self.att_list.count() > 0)

    def _remove_attachment(self):
        # Decide which items to remove. User-intuitive behavior:
        #   1. If anything is selected, remove the selection
        #   2. Else if there's only one item, just remove it
        #   3. Else nudge the user to pick which one
        items = list(self.att_list.selectedItems())
        if not items:
            if self.att_list.count() == 1:
                items = [self.att_list.item(0)]
            else:
                # Use currentItem() if available (e.g. user clicked but
                # selection was cleared by stylesheet)
                cur = self.att_list.currentItem()
                if cur is not None:
                    items = [cur]
        if not items:
            self.status_label.setText(
                "Klik dulu attachment yang mau dihapus, lalu Remove."
            )
            return

        for item in items:
            data = item.data(Qt.UserRole)
            removed = False
            if isinstance(data, tuple) and data and data[0] == "forwarded":
                fwd_id = data[1]
                # Match by id when available
                if fwd_id is not None:
                    new_list = [
                        a for a in self.attachments
                        if not (isinstance(a, dict)
                                and a.get("id") == fwd_id)
                    ]
                    if len(new_list) != len(self.attachments):
                        self.attachments = new_list
                        removed = True
                # Fallback: match by filename (extracted from list label)
                if not removed:
                    label = item.text()
                    # Label format: "↻  filename.ext  (xx.x KB)"
                    fname = label.replace("↻", "").strip()
                    fname = fname.split("  (")[0].strip()
                    new_list = [
                        a for a in self.attachments
                        if not (isinstance(a, dict)
                                and a.get("filename") == fname)
                    ]
                    if len(new_list) != len(self.attachments):
                        self.attachments = new_list
            else:
                path = data if isinstance(data, str) else None
                if path and path in self.attachments:
                    self.attachments.remove(path)
            self.att_list.takeItem(self.att_list.row(item))

        self.att_widget.setVisible(self.att_list.count() > 0)
        self.status_label.setText("")

    def _split_addrs(self, value: str):
        return [a.strip() for a in (value or "").replace(";", ",").split(",") if a.strip()]

    def _send(self):
        if not self.accounts:
            return
        account_id = self.account_combo.currentData()
        account = database.get_account(account_id)

        to_list = self._split_addrs(self.to_edit.text())
        if not to_list:
            QMessageBox.warning(self, "Missing", "Please specify at least one recipient.")
            return
        cc_list = self._split_addrs(self.cc_edit.text())
        bcc_list = self._split_addrs(self.bcc_edit.text())

        subject = self.subject_edit.text().strip() or "(no subject)"
        body_plain = self.body_edit.toPlainText()
        body_html = None
        # If user (or signature) inserted any rich content, send multipart
        # with HTML alternative so recipients see the formatting.
        if self._body_has_rich_content():
            body_html = self.body_edit.toHtml()
        from_addr = f'{account["name"]} <{account["email"]}>' if account.get("name") else account["email"]

        msg = smtp_client.build_message(
            from_addr=from_addr,
            to_list=to_list, cc_list=cc_list, bcc_list=bcc_list,
            subject=subject, body_text=body_plain, body_html=body_html,
            attachments=self.attachments,
        )

        # Queue to Outbox immediately so the email is never lost — even if
        # the network drops mid-send, the message survives in Outbox.
        try:
            self._outbox_id = database.queue_outbox(
                account_id,
                {
                    "from": from_addr,
                    "to": ", ".join(to_list),
                    "cc": ", ".join(cc_list),
                    "bcc": ", ".join(bcc_list),
                    "subject": subject,
                    "body": body_plain,
                },
                msg.as_bytes(),
            )
        except Exception as e:
            QMessageBox.critical(self, "Outbox error", str(e))
            return

        # Delete the draft if this was originally a draft (it's now in Outbox)
        if self.draft_id:
            try:
                database.delete_draft(self.draft_id)
            except Exception:
                pass
            self.draft_id = None

        self.status_label.setText("Queued in Outbox, sending...")
        self.setCursor(Qt.WaitCursor)
        self.worker = SendWorker(account, msg)
        self.worker.finished_ok.connect(lambda raw: self._on_sent(account, raw))
        self.worker.finished_err.connect(self._on_send_error)
        self.worker.start()

    def _on_sent(self, account, raw_bytes):
        self.unsetCursor()
        # Move from Outbox to Sent (also re-parses the bytes for storage)
        try:
            if self._outbox_id:
                database.move_outbox_to_sent(self._outbox_id, raw_bytes)
                self._outbox_id = None
        except Exception:
            # Fallback: store directly in Sent
            from core.mail_parser import parse_message
            try:
                parsed, atts = parse_message(raw_bytes)
                database.insert_email(account["id"], "sent", parsed, atts)
            except Exception:
                pass
        self._sent = True
        self.sent.emit(account["id"])
        QMessageBox.information(self, "Sent", "Your email was sent successfully.")
        self.accept()

    def _on_send_error(self, err):
        self.unsetCursor()
        self.status_label.setText("")
        # Email stays in Outbox; user is informed but can keep working.
        QMessageBox.warning(
            self, "Send failed",
            f"{err}\n\nThe email is saved in your Outbox and will be retried "
            f"automatically when the connection is back. You can also "
            f"trigger a retry from the Outbox folder.",
        )
        # Treat the dialog as "sent" from our perspective so closing it
        # doesn't try to also save it as a draft (it's in Outbox).
        self._sent = True
        self.sent.emit(self.account_combo.currentData())
        self.accept()

    # ---- Draft tracking & auto-save ----
    def _wire_dirty_tracking(self):
        for w in (self.to_edit, self.cc_edit, self.bcc_edit, self.subject_edit):
            w.textChanged.connect(self._mark_dirty)
        self.body_edit.textChanged.connect(self._mark_dirty)

    def _mark_dirty(self, *_):
        self._dirty = True

    def _has_content(self) -> bool:
        return bool(
            self.to_edit.text().strip()
            or self.cc_edit.text().strip()
            or self.bcc_edit.text().strip()
            or self.subject_edit.text().strip()
            or self.body_edit.toPlainText().strip()
        )

    def _collect_fields(self) -> dict:
        if not self.accounts:
            return {}
        account_id = self.account_combo.currentData()
        account = database.get_account(account_id)
        from_addr = (
            f'{account["name"]} <{account["email"]}>'
            if account.get("name") else account["email"]
        )
        return {
            "from": from_addr,
            "to": self.to_edit.text().strip(),
            "cc": self.cc_edit.text().strip(),
            "bcc": self.bcc_edit.text().strip(),
            "subject": self.subject_edit.text().strip(),
            "body": self.body_edit.toPlainText(),
        }

    def save_draft(self) -> bool:
        """Persist current state as a draft. Returns True if anything saved."""
        if self._sent:
            return False
        if not self.accounts:
            return False
        if not self._has_content():
            return False
        try:
            account_id = self.account_combo.currentData()
            fields = self._collect_fields()
            self.draft_id = database.save_draft(
                account_id, self.draft_id, fields
            )
            self._dirty = False
            self.draft_saved.emit(account_id)
            return True
        except Exception:
            return False

    def _autosave(self):
        if self._dirty:
            ok = self.save_draft()
            if ok and not self._sent:
                self.status_label.setText("Draft auto-saved")
                QTimer.singleShot(
                    2500,
                    lambda: self.status_label.setText("")
                    if not self._sent else None,
                )

    def closeEvent(self, event):
        # If user closes the dialog without sending, save anything in flight
        try:
            self._autosave_timer.stop()
        except Exception:
            pass
        if not self._sent and self._dirty and self._has_content():
            self.save_draft()
        super().closeEvent(event)

    def reject(self):
        # Same flow when user clicks Cancel/Esc
        try:
            self._autosave_timer.stop()
        except Exception:
            pass
        if not self._sent and self._dirty and self._has_content():
            self.save_draft()
        super().reject()

    def force_save_for_shutdown(self) -> bool:
        """Called by MainWindow before app shuts down (e.g. for update).
        Always persist whatever is in the dialog so nothing is lost."""
        if self._sent:
            return False
        return self.save_draft()
