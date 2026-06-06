"""
Compose Email dialog (To/Cc/Bcc, subject, body, attachments).
"""
import os
from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QFormLayout, QLineEdit, QComboBox,
    QTextEdit, QPushButton, QFileDialog, QMessageBox, QListWidget,
    QListWidgetItem, QLabel, QToolBar, QAction, QWidget, QSizePolicy,
    QCompleter, QFontComboBox, QSpinBox, QColorDialog, QToolButton, QFrame,
    QSplitter, QScrollArea,
    QStyle,
)
from PyQt5.QtCore import Qt, QThread, pyqtSignal, QTimer
from PyQt5.QtGui import (
    QIcon, QFont, QTextCharFormat, QTextListFormat, QColor,
    QTextTableFormat, QTextLength, QTextCursor, QTextFrameFormat,
)
from core import database, smtp_client, contacts
from .recipient_completer import attach_to as attach_completer
from .ribbon_toolbar import RibbonToolbar, RibbonGroup


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

    def __init__(self, parent=None, account_id=None, prefill: dict=None,
                 draft_id: int | None=None):
        super().__init__(parent)
        self.setWindowTitle("Compose")
        self.resize(820, 620)
        self.attachments = []
        self.worker = None
        self.draft_id = draft_id
        self._outbox_id = None
        self._dirty = False
        self._sent = False  # if True, dialog closing should NOT save as draft
        # Pristine signature HTML stashed at insert-time. Used at send time
        # to splice the signature back in unmodified, bypassing Qt's lossy
        # toHtml() serialisation (which destroys complex table layouts).
        self._raw_signature_html = None
        # Pristine quoted HTML for replies/forwards (set in _apply_prefill).
        self._reply_quoted_html = None
        # Enable fullscreen/maximize for compose dialog
        self.setWindowFlags(self.windowFlags() | Qt.WindowMaximizeButtonHint)
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

        # Header form area (themed background, padded)
        from . import theme as _theme
        header = QWidget()
        header.setStyleSheet(
            f"QWidget {{ background:{_theme.color('bg')}; "
            f"border-bottom:1px solid {_theme.color('border')}; }}"
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
            lbl.setStyleSheet(
                f"color:{_theme.color('text_muted')}; font-weight:600;"
            )
            form.addRow(lbl, widget)

        _row("From", self.account_combo)
        _row("To", self.to_edit)
        _row("Cc", self.cc_edit)
        _row("Bcc", self.bcc_edit)
        _row("Subject", self.subject_edit)
        # Note: header akan di-add ke splitter, bukan langsung ke layout

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

        # Body FIRST (needed by formatting toolbar)
        from .paste_aware_edit import PasteAwareTextEdit
        self.body_edit = PasteAwareTextEdit()
        self.body_edit.setPlaceholderText("Write your message...")
        
        # 1. Formatting toolbar (Sticky di atas, di LUAR scroll area)
        fmt_bar = self._build_formatting_toolbar()
        layout.addWidget(fmt_bar)
        
        # 2. Scroll area containing Header + Body
        from PyQt5.QtWidgets import QScrollArea
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setStyleSheet(
            f"QScrollArea {{ border: none; background: {_theme.color('bg')}; }}"
        )
        
        # Container widget
        container = QWidget()
        vlayout = QVBoxLayout(container)
        vlayout.setContentsMargins(0, 0, 0, 0)
        vlayout.setSpacing(0)
        vlayout.addWidget(header)
        vlayout.addWidget(self.att_widget)
        
        # 3. Body styling - expand to fit content, NO internal scrollbar
        self.body_edit.setStyleSheet(
            "QTextEdit { background:#ffffff; border:none; padding:16px 20px; "
            "font-size:10pt; }"
        )
        self.body_edit.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.body_edit.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        
        # Auto-resize body height to fit content so container scrollbar is used
        doc = self.body_edit.document()
        doc.documentLayout().documentSizeChanged.connect(self._update_body_height)
        self.body_edit.setMinimumHeight(300)
        vlayout.addWidget(self.body_edit)
        
        scroll.setWidget(container)
        layout.addWidget(scroll, 1)
        self._update_body_height()

        # Status (bottom)
        self.status_label = QLabel("")
        self.status_label.setStyleSheet(
            f"color:{_theme.color('text_muted')}; padding:6px 20px; "
            f"border-top:1px solid {_theme.color('border')}; "
            f"background:{_theme.color('bg_sidebar')};"
        )
        layout.addWidget(self.status_label)

    def _build_formatting_toolbar(self) -> RibbonToolbar:
        """Build an Outlook-style ribbon toolbar with tabs and groups."""
        ribbon = RibbonToolbar(self)
        ribbon.setMaximumHeight(120)
        st = self.style()
        
        # === HOME TAB ===
        home_tab = ribbon.add_tab("Home")
        
        # Message Group
        msg_group = home_tab.add_group("Message")
        msg_group.add_button(
            "Send", st.standardIcon(QStyle.SP_ArrowForward), self._send, large=True,
            tooltip="Send email (Ctrl+Enter)"
        )
        
        # Clipboard Group
        clip_group = home_tab.add_group("Clipboard")
        clip_group.add_button("Cut", st.standardIcon(QStyle.SP_MessageBoxCritical), self._cut, tooltip="Cut")
        clip_group.add_button("Copy", st.standardIcon(QStyle.SP_FileDialogInfoView), self._copy, tooltip="Copy")
        clip_group.add_button("Paste", st.standardIcon(QStyle.SP_FileDialogNewFolder), self._paste, tooltip="Paste")
        
        # Basic Text Group
        text_group = home_tab.add_group("Basic Text")
        
        # Font controls in a row
        self.font_combo = QFontComboBox()
        self.font_combo.setMaximumWidth(140)
        self.font_combo.setCurrentFont(QFont("Segoe UI"))
        self.font_combo.currentFontChanged.connect(self._on_font_family)
        text_group.btn_layout.addWidget(self.font_combo)
        
        self.size_combo = QComboBox()
        self.size_combo.setEditable(True)
        self.size_combo.setMaximumWidth(50)
        for s in (8, 9, 10, 11, 12, 14, 16, 18, 20, 24, 28, 32, 36, 48, 72):
            self.size_combo.addItem(str(s))
        self.size_combo.setCurrentText("10")
        self.size_combo.activated.connect(self._on_font_size)
        self.size_combo.lineEdit().editingFinished.connect(self._on_font_size)
        text_group.btn_layout.addWidget(self.size_combo)
        
        text_group.btn_layout.addSpacing(6)
        
        # Bold/Italic/Underline/Strike
        self.bold_btn = self._fmt_btn("B", "Bold (Ctrl+B)", self._toggle_bold, bold=True)
        self.italic_btn = self._fmt_btn("I", "Italic (Ctrl+I)", self._toggle_italic, italic=True)
        self.under_btn = self._fmt_btn("U", "Underline (Ctrl+U)", self._toggle_underline, underline=True)
        self.strike_btn = self._fmt_btn("S", "Strikethrough", self._toggle_strike)
        for b in (self.bold_btn, self.italic_btn, self.under_btn, self.strike_btn):
            text_group.btn_layout.addWidget(b)
        
        text_group.btn_layout.addSpacing(6)
        
        # Color button
        self._color_btn = self._fmt_btn("A", "Text color", self._pick_color, bold=True)
        text_group.btn_layout.addWidget(self._color_btn)

        # Highlight (text background) button
        self._highlight_btn = self._fmt_btn(
            "🖍", "Highlight (text background color)", self._pick_highlight
        )
        text_group.btn_layout.addWidget(self._highlight_btn)
        
        # Paragraph Group
        para_group = home_tab.add_group("Paragraph")
        
        # List buttons
        bullet_btn = self._fmt_btn("•", "Bulleted list",
                                   lambda: self._toggle_list(QTextListFormat.ListDisc))
        num_btn = self._fmt_btn("1.", "Numbered list",
                                lambda: self._toggle_list(QTextListFormat.ListDecimal))
        para_group.btn_layout.addWidget(bullet_btn)
        para_group.btn_layout.addWidget(num_btn)
        para_group.btn_layout.addSpacing(6)
        
        # Alignment buttons
        for label, tip, align in [
            ("⇤", "Align left", Qt.AlignLeft),
            ("⇔", "Align center", Qt.AlignCenter),
            ("⇥", "Align right", Qt.AlignRight),
        ]:
            b = self._fmt_btn(label, tip, lambda _=False, a=align: self.body_edit.setAlignment(a))
            para_group.btn_layout.addWidget(b)
        
        # === INSERT TAB ===
        insert_tab = ribbon.add_tab("Insert")
        
        # Tables Group
        tables_group = insert_tab.add_group("Tables")
        tables_group.add_button(
            "Table", st.standardIcon(QStyle.SP_FileDialogListView),
            self._insert_table, large=True, tooltip="Insert a table"
        )
        # Row / column / cell editing (operate on the table under the caret)
        tables_group.add_button(
            "Insert Row", st.standardIcon(QStyle.SP_ArrowDown),
            self._table_insert_row, tooltip="Insert a row below the current row"
        )
        tables_group.add_button(
            "Insert Col", st.standardIcon(QStyle.SP_ArrowRight),
            self._table_insert_col, tooltip="Insert a column to the right"
        )
        tables_group.add_button(
            "Del Row", st.standardIcon(QStyle.SP_ArrowUp),
            self._table_delete_row, tooltip="Delete the current row"
        )
        tables_group.add_button(
            "Del Col", st.standardIcon(QStyle.SP_ArrowLeft),
            self._table_delete_col, tooltip="Delete the current column"
        )
        tables_group.add_button(
            "Merge", st.standardIcon(QStyle.SP_DialogApplyButton),
            self._table_merge_cells, tooltip="Merge the selected cells"
        )
        tables_group.add_button(
            "Split", st.standardIcon(QStyle.SP_DialogResetButton),
            self._table_split_cell, tooltip="Split the merged cell back into cells"
        )
        tables_group.add_button(
            "Border", st.standardIcon(QStyle.SP_FileDialogDetailedView),
            self._table_border, tooltip="Set the table border width & color"
        )
        
        # Attachments Group
        attach_group = insert_tab.add_group("Include")
        attach_group.add_button(
            "Attach File", st.standardIcon(QStyle.SP_DialogOpenButton),
            self._add_attachment, large=True, tooltip="Attach a file to this email"
        )
        
        home_tab.add_spacer()
        
        # Sync UI buttons when the cursor moves into a different format
        self.body_edit.cursorPositionChanged.connect(self._sync_format_buttons)
        self.body_edit.currentCharFormatChanged.connect(self._sync_format_buttons_from_fmt)
        
        return ribbon
    
    def _cut(self):
        self.body_edit.cut()
    
    def _copy(self):
        self.body_edit.copy()
    
    def _paste(self):
        self.body_edit.paste()

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

    def _pick_highlight(self):
        """Apply a highlight (text background) color to the selection. A
        second pick of the same/transparent color clears it."""
        from PyQt5.QtWidgets import QColorDialog as _QCD
        cur_fmt = self.body_edit.currentCharFormat()
        start = cur_fmt.background().color() if cur_fmt.background().style() != 0 else QColor("#ffff00")
        color = _QCD.getColor(start, self, "Pick highlight color")
        if not color.isValid():
            return
        fmt = QTextCharFormat()
        # White / near-white means "remove highlight" for convenience.
        if color.name().lower() in ("#ffffff",):
            fmt.clearBackground()
        else:
            fmt.setBackground(color)
        self._merge_format(fmt)
        self._highlight_btn.setStyleSheet(
            f"QToolButton {{ background:{color.name()}; }}"
        )

    def _table_under_cursor(self):
        """Return the QTextTable the caret is currently inside, or None."""
        return self.body_edit.textCursor().currentTable()

    def _require_table(self):
        t = self._table_under_cursor()
        if t is None:
            self.status_label.setText(
                "Letakkan kursor di dalam tabel dulu untuk operasi ini."
            )
        return t

    def _table_insert_row(self):
        t = self._require_table()
        if t is None:
            return
        cell = t.cellAt(self.body_edit.textCursor())
        t.insertRows(cell.row() + 1, 1)

    def _table_insert_col(self):
        t = self._require_table()
        if t is None:
            return
        cell = t.cellAt(self.body_edit.textCursor())
        t.insertColumns(cell.column() + 1, 1)

    def _table_delete_row(self):
        t = self._require_table()
        if t is None:
            return
        cell = t.cellAt(self.body_edit.textCursor())
        t.removeRows(cell.row(), 1)

    def _table_delete_col(self):
        t = self._require_table()
        if t is None:
            return
        cell = t.cellAt(self.body_edit.textCursor())
        t.removeColumns(cell.column(), 1)

    def _table_merge_cells(self):
        t = self._require_table()
        if t is None:
            return
        cursor = self.body_edit.textCursor()
        if cursor.hasSelection():
            t.mergeCells(cursor)
        else:
            self.status_label.setText(
                "Pilih (drag) beberapa sel dulu untuk merge."
            )

    def _table_split_cell(self):
        t = self._require_table()
        if t is None:
            return
        cursor = self.body_edit.textCursor()
        cell = t.cellAt(cursor)
        # Split back to single cells (1x1 spans).
        t.splitCell(cell.row(), cell.column(), 1, 1)

    def _table_border(self):
        """Set the border width and color of the table under the caret."""
        t = self._require_table()
        if t is None:
            return
        from PyQt5.QtWidgets import QDialog, QGridLayout, QLabel, QSpinBox, \
            QPushButton, QHBoxLayout
        dlg = QDialog(self)
        dlg.setWindowTitle("Table Border")
        grid = QGridLayout(dlg)
        grid.addWidget(QLabel("Border width (px):"), 0, 0)
        width_spin = QSpinBox()
        width_spin.setRange(0, 10)
        fmt = t.format()
        width_spin.setValue(int(fmt.border()) if fmt.border() else 1)
        grid.addWidget(width_spin, 0, 1)
        self._border_color = fmt.borderBrush().color() if fmt.borderBrush().style() != 0 else QColor("#000000")
        color_btn = QPushButton("Pick color...")
        def _pick():
            c = QColorDialog.getColor(self._border_color, dlg, "Border color")
            if c.isValid():
                self._border_color = c
                color_btn.setStyleSheet(f"color:{c.name()};")
        color_btn.clicked.connect(_pick)
        grid.addWidget(QLabel("Border color:"), 1, 0)
        grid.addWidget(color_btn, 1, 1)
        row = QHBoxLayout(); row.addStretch(1)
        ok = QPushButton("Apply"); ok.setDefault(True); ok.clicked.connect(dlg.accept)
        cancel = QPushButton("Cancel"); cancel.clicked.connect(dlg.reject)
        row.addWidget(ok); row.addWidget(cancel)
        grid.addLayout(row, 2, 0, 1, 2)
        if dlg.exec_() != QDialog.Accepted:
            return
        new_fmt = t.format()
        new_fmt.setBorder(float(width_spin.value()))
        new_fmt.setBorderBrush(self._border_color)
        new_fmt.setBorderStyle(QTextFrameFormat.BorderStyle_Solid)
        t.setFormat(new_fmt)

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

    def _insert_table(self):
        """Insert a table at the caret as a native QTextTable so it can be
        edited afterwards (insert/delete rows & columns, merge/split cells,
        change border). Includes a border by default."""
        from PyQt5.QtWidgets import (
            QDialog, QGridLayout, QLabel, QSpinBox, QPushButton, QHBoxLayout,
            QCheckBox, QComboBox,
        )
        dlg = QDialog(self)
        dlg.setWindowTitle("Insert Table")
        layout = QGridLayout(dlg)
        layout.addWidget(QLabel("Rows:"), 0, 0)
        rows_spin = QSpinBox(); rows_spin.setRange(1, 50); rows_spin.setValue(2)
        layout.addWidget(rows_spin, 0, 1)
        layout.addWidget(QLabel("Columns:"), 1, 0)
        cols_spin = QSpinBox(); cols_spin.setRange(1, 20); cols_spin.setValue(2)
        layout.addWidget(cols_spin, 1, 1)
        layout.addWidget(QLabel("Border (px):"), 2, 0)
        border_spin = QSpinBox(); border_spin.setRange(0, 10); border_spin.setValue(1)
        layout.addWidget(border_spin, 2, 1)
        layout.addWidget(QLabel("Table width:"), 3, 0)
        width_combo = QComboBox()
        width_combo.setEditable(True)
        width_combo.addItems(["100%", "90%", "80%", "75%", "70%", "60%", "50%", "40%", "30%", "Auto"])
        width_combo.setCurrentText("100%")
        layout.addWidget(width_combo, 3, 1)
        header_chk = QCheckBox("First row is a header")
        layout.addWidget(header_chk, 4, 0, 1, 2)
        btns = QHBoxLayout(); btns.addStretch(1)
        ok_btn = QPushButton("Insert"); ok_btn.setDefault(True)
        ok_btn.clicked.connect(dlg.accept); btns.addWidget(ok_btn)
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(dlg.reject); btns.addWidget(cancel_btn)
        layout.addLayout(btns, 5, 0, 1, 2)
        if dlg.exec_() != QDialog.Accepted:
            return
        rows = rows_spin.value()
        cols = cols_spin.value()
        border = float(border_spin.value())

        # Parse table width
        width_text = width_combo.currentText().strip().lower()
        if width_text == "auto" or width_text == "":
            table_width_pct = None  # Auto/variable width
        else:
            # Remove % if present and parse
            width_text = width_text.replace("%", "")
            try:
                table_width_pct = float(width_text)
                if table_width_pct <= 0 or table_width_pct > 100:
                    table_width_pct = 100.0
            except ValueError:
                table_width_pct = 100.0

        cursor = self.body_edit.textCursor()
        fmt = QTextTableFormat()
        fmt.setBorder(border)
        fmt.setBorderBrush(QColor("#000000"))
        fmt.setBorderStyle(QTextFrameFormat.BorderStyle_Solid)
        fmt.setCellPadding(4)
        fmt.setCellSpacing(0)

        # Calculate column widths based on table width
        if table_width_pct is not None:
            # Fixed percentage width for table, even column distribution
            col_width_pct = table_width_pct / cols
            fmt.setColumnWidthConstraints(
                [QTextLength(QTextLength.PercentageLength, col_width_pct)] * cols
            )
        # If table_width_pct is None, columns will use variable/available width

        table = cursor.insertTable(rows, cols, fmt)

        if header_chk.isChecked():
            # Bold + light shading for the header row.
            for c in range(cols):
                cell = table.cellAt(0, c)
                cfmt = cell.format()
                cfmt.setBackground(QColor("#f2f2f2"))
                cell.setFormat(cfmt)
                ccur = cell.firstCursorPosition()
                bold = QTextCharFormat(); bold.setFontWeight(QFont.Bold)
                ccur.mergeBlockCharFormat(bold)
        # Put the caret in the first cell, ready to type.
        self.body_edit.setTextCursor(table.cellAt(0, 0).firstCursorPosition())

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

    @staticmethod
    def _sanitize_signature_html(sig: str) -> str:
        """Strip baked-in background colors from a signature.

        Outlook/webmail signatures often carry `background-color: white`
        (and `!important`) on every paragraph/div/table. Inside our editor
        — whose base is a faint gray — those opaque white runs paint as
        visible white "cards"/boxes behind lines like "Best Regards,".
        We remove the background-color declarations so the signature sits
        flush on whatever surface renders it (editor or recipient client).
        The pristine copy used for sending is sanitized the same way so the
        boxes never reach the recipient either.
        """
        import re
        if not sig:
            return sig
        # Remove `background-color: <value>;` (incl. !important) up to the
        # next style delimiter. Greedy stop at ; } or quote so the whole
        # declaration (e.g. "white !important") is removed, not just the key.
        sig = re.sub(
            r'background-color\s*:\s*[^;}"\']*;?',
            '', sig, flags=re.IGNORECASE,
        )
        # Remove `background: <color>` shorthand only when it's purely a
        # color (avoid nuking gradients/images, which signatures rarely use).
        sig = re.sub(
            r'background\s*:\s*(?:#[0-9a-fA-F]{3,8}|rgb[a]?\([^)]*\)|white|transparent)'
            r'[^;}"\']*;?',
            '', sig, flags=re.IGNORECASE,
        )
        return sig

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

        We also plant invisible plain-text markers around the signature
        so that at send time we can swap Qt's lossy roundtrip back to the
        original pristine signature HTML — preserving table layout, fonts,
        colors that Qt's QTextDocument otherwise mangles when serialised.
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

        # Remember the original signature so we can restore it at send time.
        # Sanitize it (strip baked-in background colors) so neither the editor
        # nor the recipient sees white "card" boxes behind the text.
        self._raw_signature_html = (
            self._sanitize_signature_html(sig) if self._is_html(sig) else None
        )
        sig_html = self._raw_signature_html if self._is_html(sig) else sig

        cursor = self.body_edit.textCursor()
        cursor.movePosition(cursor.End)

        # Insert exactly one paragraph break before the signature so there
        # is one empty line for the user to start typing in. (Was 2; that's
        # why the user had to press Enter twice to "escape" the formatting.)
        if self._is_html(sig):
            cursor.insertBlock()
            # Markers are embedded INLINE at the signature's edges and the
            # whole thing is inserted in ONE call, so they never get their
            # own blank line above the signature.
            cursor.insertHtml(self._wrap_signature_with_markers(sig_html))
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

        # Hide the markers visually so the user doesn't see the literal text.
        # We do this with a 1pt transparent style applied to the runs that
        # contain the marker text. Plain text still survives toHtml().
        self._hide_marker_runs()

    def _prepend_signature_for_reply(self):
        """Insert signature at the TOP of the body, before the quoted
        block. This is what Outlook does for replies/forwards — user
        types above the signature, signature sits above the quoted
        original, original is at the bottom.

        We rebuild the whole body as ONE HTML string and setHtml() it once.
        Inserting the signature into the already-present quoted content made
        the signature's first block inherit the quoted separator's block
        format (gray background + left padding) — which showed up as a gray
        box behind "Best Regards" and pushed it to the right. Building the
        body in one pass keeps each section's formatting isolated.
        """
        sig = self._signature_for_current()
        if not sig:
            return
        body_plain = self.body_edit.toPlainText()
        sig_marker = self._sig_plain_excerpt(sig)
        if sig_marker and sig_marker in body_plain:
            return  # already there

        from PyQt5.QtGui import QTextCharFormat, QTextBlockFormat, QFont

        self._raw_signature_html = (
            self._sanitize_signature_html(sig) if self._is_html(sig) else None
        )
        sig_html = self._raw_signature_html if self._is_html(sig) else sig

        if self._is_html(sig):
            # Use the PRISTINE quoted HTML (stashed at prefill) rather than
            # re-serializing the editor — that round-trip bakes the quoted
            # separator's gray box/padding into block formats which then leak
            # into the signature.
            quoted_html = getattr(self, "_reply_quoted_html", None)
            if quoted_html is None:
                quoted_html = self.body_edit.toHtml()
            wrapped_sig = self._wrap_signature_with_markers(sig_html)
            # One empty typing line, then the signature on its own clean
            # blocks, then the quoted original. The quoted block already
            # begins with its own <hr> + sender box (built in
            # _quote_body_html), which is the visible divider — so we don't
            # add another rule here.
            combined = (
                '<div style="font-family:\'Segoe UI\',sans-serif;'
                'font-size:10pt;color:#201f1e;">'
                '<p style="margin:0;"><br></p>'
                f'<div style="margin:0;background:transparent;">{wrapped_sig}</div>'
                '</div>'
                f'{quoted_html}'
            )
            self.body_edit.setHtml(combined)
            cursor = self.body_edit.textCursor()
        else:
            cursor = self.body_edit.textCursor()
            cursor.movePosition(cursor.Start)
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

        self._hide_marker_runs()

    # Invisible plain-text tokens we plant around the signature so we can
    # splice the original (pristine) signature HTML back in at send time.
    # NOTE: these are bare tokens (no angle brackets). Qt's toHtml() escapes
    # "<!--...-->" into "&lt;!--...--&gt;", which broke the old comment-style
    # markers — the splice could never find them, so they leaked into the
    # sent mail as visible text. Bare tokens survive the roundtrip verbatim.
    SIG_MARKER_OPEN = "PMSIG_OPEN_5f3a9c"
    SIG_MARKER_CLOSE = "PMSIG_CLOSE_5f3a9c"
    # Invisible inline style for the marker runs. The negative letter-spacing
    # cancels out the ~1px the (1px, transparent) marker glyph would otherwise
    # occupy, so placing a marker at the START of a line does NOT shift the
    # first visible word to the right. This lets us keep OPEN before all the
    # signature text (so the send-time splice captures the WHOLE signature)
    # while still keeping "Best Regards," flush-left, aligned with the rest.
    _MARKER_SPAN_OPEN = (
        '<span style="font-size:1px;color:transparent;'
        'letter-spacing:-1px;mso-hide:all;">'
    )
    _MARKER_SPAN_CLOSE = "</span>"

    def _insert_marker(self, cursor, token: str):
        """Insert a marker token as an invisible inline run.

        We insert it via HTML so it carries a transparent/hidden char format
        in the document (user never sees it) yet survives toHtml() as plain
        token text we can search for when splicing the pristine signature.
        """
        cursor.insertHtml(self._MARKER_SPAN_OPEN + token + self._MARKER_SPAN_CLOSE)

    def _wrap_signature_with_markers(self, sig_html: str) -> str:
        """Return signature HTML with invisible marker tokens embedded INLINE
        inside the signature's first and last block elements.

        The OPEN token goes at the START of the first <p> (before any visible
        text) so the send-time splice replaces the ENTIRE signature — if OPEN
        sat AFTER "Best Regards,", the splice would leave that line in place
        and then insert the pristine signature (which also has "Best Regards,")
        right after it, producing a DUPLICATE. The marker span uses negative
        letter-spacing so being at the line start doesn't push the text right.
        CLOSE goes just before the LAST </p>. Falls back to a plain inline
        wrap for signatures without <p> blocks.
        """
        import re
        open_run = self._MARKER_SPAN_OPEN + self.SIG_MARKER_OPEN + self._MARKER_SPAN_CLOSE
        close_run = self._MARKER_SPAN_OPEN + self.SIG_MARKER_CLOSE + self._MARKER_SPAN_CLOSE

        m = re.search(r'<p\b[^>]*>', sig_html, re.IGNORECASE)
        last_close = sig_html.lower().rfind('</p>')
        if m and last_close >= 0 and last_close >= m.end():
            # OPEN right after the first <p ...> (before the first word),
            # CLOSE just before the last </p>. The whole signature, including
            # "Best Regards,", now sits BETWEEN the markers.
            html = sig_html[:m.end()] + open_run + sig_html[m.end():]
            last_close = html.lower().rfind('</p>')
            return html[:last_close] + close_run + html[last_close:]
        # Fallback: no <p> blocks — wrap inline (may create a line, but these
        # signatures are rare and usually single-line plain-ish HTML).
        return open_run + sig_html + close_run

    def _hide_marker_runs(self):
        """Belt-and-braces: force any run whose text contains a marker token
        to be transparent + 1pt, in case Qt normalized away the inline style
        we inserted. Markers should never be visible to the user."""
        from PyQt5.QtGui import QTextCursor, QTextCharFormat, QColor
        doc = self.body_edit.document()
        for token in (self.SIG_MARKER_OPEN, self.SIG_MARKER_CLOSE):
            block = doc.begin()
            while block.isValid():
                text = block.text()
                idx = text.find(token)
                if idx >= 0:
                    start = block.position() + idx
                    cur = QTextCursor(doc)
                    cur.setPosition(start)
                    cur.setPosition(start + len(token), QTextCursor.KeepAnchor)
                    fmt = QTextCharFormat()
                    fmt.setForeground(QColor(0, 0, 0, 0))  # transparent
                    fmt.setFontPointSize(1)
                    cur.mergeCharFormat(fmt)
                block = block.next()

    def _update_body_height(self):
        """Adjust body height to fit content so container scroll handles all scrolling."""
        doc = self.body_edit.document()
        height = int(doc.size().height()) + 40
        # Allow up to 100,000 pixels height for very long reply threads
        height = max(300, min(height, 100000))
        self.body_edit.setMinimumHeight(height)
        self.body_edit.setMaximumHeight(height)

    def _splice_pristine_signature(self, body_html: str) -> str:
        """Replace the signature region in Qt-serialized HTML with the
        ORIGINAL signature HTML we stashed at insert time. Returns the
        cleaned-up HTML ready for SMTP.

        The "signature region" is the text between SIG_MARKER_OPEN and
        SIG_MARKER_CLOSE, including any surrounding paragraph wrappers
        Qt may have inserted around the markers."""
        import re
        if not self._raw_signature_html:
            # No pristine sig stashed — just remove the markers in case they
            # leaked in via a re-opened draft.
            return self._strip_markers(body_html)

        open_tok = self.SIG_MARKER_OPEN
        close_tok = self.SIG_MARKER_CLOSE
        if open_tok not in body_html or close_tok not in body_html:
            return self._strip_markers(body_html)

        # Greedy: from the <p ...> wrapping OPEN to the closing </p> after
        # CLOSE — replace the whole block with the raw signature. The tokens
        # live inside invisible <span> wrappers, so allow optional span tags
        # immediately around each token.
        span_open = r'(?:<span[^>]*>\s*)?'
        span_close = r'(?:\s*</span>)?'
        pattern = re.compile(
            r'(<p[^>]*>\s*)?'  # optional opening <p>
            +span_open
            +re.escape(open_tok)
            +span_close
            +r'.*?'  # everything in between (the rendered signature)
            +span_open
            +re.escape(close_tok)
            +span_close
            +r'(\s*</p>)?',  # optional closing </p>
            re.DOTALL | re.IGNORECASE,
        )
        replacement = (
            '<div class="pymail-signature">'
            +self._raw_signature_html
            +'</div>'
        )
        new_html, n = pattern.subn(replacement, body_html, count=1)
        if n == 0:
            # Fallback: just strip markers, leave Qt's lossy render.
            return self._strip_markers(body_html)
        # Final cleanup — make sure no orphan markers remain
        return self._strip_markers(new_html)

    def _strip_markers(self, html: str) -> str:
        import re
        # Remove the invisible span-wrapped tokens (and any bare leftovers).
        for token in (self.SIG_MARKER_OPEN, self.SIG_MARKER_CLOSE):
            html = re.sub(
                r'(?:<span[^>]*>\s*)?'
                +re.escape(token)
                +r'(?:\s*</span>)?',
                '', html, flags=re.IGNORECASE,
            )
        return html

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
        html = (self.body_edit.toHtml() or "").lower()
        if any(tag in html for tag in ("<table", "<tr", "<td", "<th", "<ul", "<ol", "<li")):
            return True
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

    def _sync_completer_caches(self):
        """Sync each recipient field's completer state_cache to the field's
        current text.

        The completer caches `user_text` only on the textEdited signal, which
        is NOT emitted by setText(). Without this sync, after a reply/forward
        prefill (which uses setText) the cache stays empty — so when the user
        later picks an autocomplete suggestion, pathFromIndex reads the stale
        empty cache and WIPES the already-prefilled recipient instead of
        appending. Calling this after any programmatic setText keeps the
        completer in step with the field.
        """
        for fld in (self.to_edit, self.cc_edit, self.bcc_edit):
            comp = fld.completer()
            if comp is not None and hasattr(comp, "state_cache"):
                comp.state_cache["user_text"] = fld.text()

    def _apply_prefill(self, p):
        if "to" in p:
            self.to_edit.setText(p["to"])
        if "cc" in p:
            self.cc_edit.setText(p["cc"])
        if "bcc" in p:
            self.bcc_edit.setText(p["bcc"])
        if "subject" in p:
            self.subject_edit.setText(p["subject"])
        # Keep the autocomplete caches in step with the prefilled recipients
        # so picking a suggestion appends rather than overwrites.
        self._sync_completer_caches()

        # Body: prefer HTML when provided so quoted reply/forward keeps
        # original formatting, signatures, embedded images, and tables.
        body_html = p.get("body_html")
        body_plain = p.get("body")  # legacy plain-text path
        if body_html:
            # Stash the PRISTINE quoted HTML. _prepend_signature_for_reply
            # uses this directly instead of re-serializing the editor — a
            # round-trip through toHtml() bakes the quoted separator's gray
            # box/padding into block formats that then bleed into the
            # signature (gray box + rightward shift behind "Best Regards").
            self._reply_quoted_html = body_html
            self.body_edit.setHtml(body_html)
        elif body_plain is not None:
            self._reply_quoted_html = None
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
            # Splice the pristine signature HTML back in. Qt's toHtml()
            # roundtrips QTextDocument's internal model — for table-based
            # signatures this loses the table structure entirely. We
            # planted plain-text markers around the signature when it was
            # first inserted; here we replace everything between the
            # markers with the original signature HTML.
            body_html = self._splice_pristine_signature(body_html)
            # Also strip the markers from the plain-text alternative.
            body_plain = (
                body_plain
                .replace(self.SIG_MARKER_OPEN, "")
                .replace(self.SIG_MARKER_CLOSE, "")
            )
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

        # If account is set to queue-first (default), stop here.
        # OutboxFlushWorker will send it during the next Send/Receive.
        if not account.get("send_immediately"):
            self._sent = True
            self.sent.emit(account_id)
            # No blocking popup — clicking Send should feel instant. The
            # dialog just closes and the message is safely in the Outbox.
            self.accept()
            return

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
        # Strip signature splice markers from the stored body so a re-opened
        # draft never shows the raw tokens. Covers both the current bare
        # tokens and legacy comment-style markers from older drafts.
        body_plain = self.body_edit.toPlainText()
        for tok in (self.SIG_MARKER_OPEN, self.SIG_MARKER_CLOSE,
                    "<!--PMSIG_OPEN-->", "<!--PMSIG_CLOSE-->"):
            body_plain = body_plain.replace(tok, "")

        # Collect HTML body if has rich content (tables, formatting, etc.)
        body_html = None
        if self._body_has_rich_content():
            body_html = self.body_edit.toHtml()
            # Also strip markers from HTML
            if body_html:
                for tok in (self.SIG_MARKER_OPEN, self.SIG_MARKER_CLOSE,
                            "<!--PMSIG_OPEN-->", "<!--PMSIG_CLOSE-->"):
                    body_html = body_html.replace(tok, "")

        return {
            "from": from_addr,
            "to": self.to_edit.text().strip(),
            "cc": self.cc_edit.text().strip(),
            "bcc": self.bcc_edit.text().strip(),
            "subject": self.subject_edit.text().strip(),
            "body": body_plain,
            "body_html": body_html,
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

    def showEvent(self, event):
        super().showEvent(event)
        # Open ready to type — like Outlook. Put keyboard focus in the body
        # with the caret on the top empty line (above the signature, and
        # above the quoted block for replies/forwards). Only do this once,
        # and never for a re-opened draft (respect the saved caret).
        if getattr(self, "_focused_once", False):
            return
        self._focused_once = True
        from PyQt5.QtCore import QTimer
        # Defer to after the dialog is fully shown so focus/caret stick.
        QTimer.singleShot(0, self._focus_body_for_typing)

    def _focus_body_for_typing(self):
        """Place the caret on the first empty line and focus the editor."""
        # For a re-opened draft, just give focus without moving the caret —
        # the body is the user's own saved content.
        if not self.draft_id:
            cursor = self.body_edit.textCursor()
            cursor.movePosition(cursor.Start)
            self.body_edit.setTextCursor(cursor)
        self.body_edit.setFocus()
        self.body_edit.ensureCursorVisible()

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
