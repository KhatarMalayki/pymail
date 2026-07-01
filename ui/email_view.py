"""
Right-side email viewer widget: header info + body + attachments.
"""
import os
import tempfile
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QTextBrowser, QFrame,
    QPushButton, QListWidget, QListWidgetItem, QFileDialog, QMessageBox,
    QMenu, QScrollArea, QSizePolicy, QDialog, QComboBox,
)
from PyQt5.QtCore import Qt, QUrl, pyqtSignal, QSize, QRect, QTimer, QThread
from PyQt5.QtGui import (
    QDesktopServices, QFont, QPainter, QColor, QPixmap, QBrush, QPen,
)
from core import database
from .theme import avatar_color_for, initials_of, color as theme_color


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


class _ImageFetchWorker(QThread):
    """Downloads remote images for an email body off the UI thread.

    Emits `fetched(url, QPixmap)` for each image that loads successfully so
    the viewer can drop it into the document and re-render. Running this in a
    background thread keeps the window responsive — the synchronous
    urllib download used to block the GUI thread and trigger Windows'
    "Not Responding" state whenever a message contained remote signature
    logos (or pointed at a slow/unreachable host).
    """
    fetched = pyqtSignal(str, QPixmap)

    def __init__(self, urls: list[str], parent=None):
        super().__init__(parent)
        self._urls = list(urls)
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    def run(self):
        import urllib.request
        for url_str in self._urls:
            if self._cancelled:
                return
            try:
                req = urllib.request.Request(
                    url_str, headers={"User-Agent": "RunLabMail/1.0"}
                )
                with urllib.request.urlopen(req, timeout=5) as resp:
                    data = resp.read(512 * 1024)  # cap at 512 KB
            except Exception:
                continue
            if self._cancelled:
                return
            pm = QPixmap()
            pm.loadFromData(data)
            if not pm.isNull():
                self.fetched.emit(url_str, pm)


class NetworkAwareTextBrowser(QTextBrowser):
    """QTextBrowser that renders external images (http/https) for sender
    signatures with logos.

    Downloads happen on a background thread so the UI never blocks. On first
    render `loadResource` returns whatever is already cached (or an empty
    pixmap), then a worker fetches the missing images and the body re-renders
    once they arrive. Downloads are cached per-session so repeated views
    don't re-fetch.

    Remote images can also be blocked entirely (Outlook-style privacy
    default). When blocking is on, remote URLs are neither fetched nor
    rendered; instead `images_blocked` is emitted so the viewer can offer a
    "Show images" button."""

    images_blocked = pyqtSignal(int)  # number of remote images blocked

    def __init__(self, parent=None):
        super().__init__(parent)
        self._image_cache: dict[str, QPixmap] = {}
        self._pending: set[str] = set()
        self._worker: _ImageFetchWorker | None = None
        self._current_html: str = ""
        self._block_images: bool = False
        self._blocked_count: int = 0
        # The browser does NOT scroll on its own: it grows to fit its content
        # and the outer scroll area (see EmailView) scrolls header + body as
        # one unit, like Outlook's reading pane.
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.document().documentLayout().documentSizeChanged.connect(
            self._adjust_height
        )

    def _adjust_height(self, *args) -> None:
        """Resize the widget to fit the full document so the outer scroll
        area can scroll everything together."""
        doc = self.document()
        doc.setTextWidth(self.viewport().width())
        h = int(doc.size().height())
        # Account for the content margins/frame so the last line isn't clipped.
        self.setFixedHeight(h + 12)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        # Re-wrap to the new width, then recompute height.
        self._adjust_height()

    def set_block_images(self, block: bool) -> None:
        """Set whether remote images are blocked for the NEXT render."""
        self._block_images = bool(block)

    def reload_with_images(self) -> None:
        """Re-render the current message WITH remote images (the user clicked
        'Show images')."""
        self._block_images = False
        self.setHtml(self._current_html)

    @staticmethod
    def _scan_remote_images(html: str) -> set:
        """Return the set of http/https image URLs referenced in the HTML.

        Done with a regex up front (instead of relying on Qt calling
        loadResource at a predictable time) so blocking and background
        fetching are deterministic."""
        import re
        urls = set()
        for m in re.finditer(
            r'<img\b[^>]*?\bsrc\s*=\s*["\']([^"\']+)["\']', html or "",
            flags=re.IGNORECASE,
        ):
            u = m.group(1).strip()
            if u.startswith(("http://", "https://")):
                urls.add(u)
        return urls

    def setHtml(self, html: str) -> None:  # type: ignore[override]
        # Stop any in-flight fetch from a previously viewed email.
        self._stop_worker()
        self._current_html = html or ""
        self._pending.clear()
        self._blocked_count = 0

        remote = self._scan_remote_images(self._current_html)

        if self._block_images and remote:
            # Render without fetching; loadResource returns empty for remotes.
            super().setHtml(self._current_html)
            self._blocked_count = len(remote)
            self.images_blocked.emit(self._blocked_count)
            return

        super().setHtml(self._current_html)
        # Fetch any remote images not already cached, on a background thread.
        to_fetch = [u for u in remote if u not in self._image_cache]
        if to_fetch:
            self._pending = set(to_fetch)
            self._start_worker(to_fetch)

    def loadResource(self, rtype: int, url: QUrl) -> object:
        from PyQt5.QtGui import QTextDocument
        if rtype == QTextDocument.ImageResource:
            url_str = url.toString()
            if url_str.startswith(("http://", "https://")):
                # Serve from cache when available (used by the re-render after
                # a background fetch); otherwise return an empty pixmap so
                # rendering never blocks on the network.
                if not self._block_images and url_str in self._image_cache:
                    return self._image_cache[url_str]
                return QPixmap()
        return super().loadResource(rtype, url)

    def _start_worker(self, urls: list[str]) -> None:
        self._worker = _ImageFetchWorker(urls, self)
        self._worker.fetched.connect(self._on_image_fetched)
        self._worker.finished.connect(self._on_worker_finished)
        self._worker.start()

    def _stop_worker(self) -> None:
        if self._worker is not None:
            w = self._worker
            w.cancel()
            try:
                w.fetched.disconnect(self._on_image_fetched)
                w.finished.disconnect(self._on_worker_finished)
            except (TypeError, RuntimeError):
                pass
            # Let the cancelled thread delete itself once it unwinds (it
            # checks the cancel flag between downloads) so workers don't pile
            # up when the user clicks through messages quickly.
            w.finished.connect(w.deleteLater)
            self._worker = None

    def _on_image_fetched(self, url_str: str, pm: QPixmap) -> None:
        from PyQt5.QtGui import QTextDocument
        # Cache the image and register it with the document.
        self._image_cache[url_str] = pm
        self._pending.discard(url_str)
        self.document().addResource(
            QTextDocument.ImageResource, QUrl(url_str), pm,
        )

    def _on_worker_finished(self) -> None:
        # Re-render once downloads are done so the layout picks up the real
        # image sizes. loadResource now serves images straight from cache, so
        # this render is synchronous and non-blocking. Images that failed to
        # download are simply skipped. Preserve scroll position so the view
        # doesn't jump.
        if self.sender() is not self._worker:
            return
        self._worker = None
        if not self._image_cache:
            return
        vbar = self.verticalScrollBar()
        pos = vbar.value()
        super().setHtml(self._current_html)
        vbar.setValue(pos)


class _PrintPreviewDialog(QDialog):
    """Self-contained print dialog with a Qt-rendered preview.

    We render the preview ourselves with QPrintPreviewWidget instead of
    relying on the OS print dialog's preview pane — the modern Windows
    dialog shows "This app doesn't support print preview" because Qt
    doesn't implement the callback it expects. This dialog always shows a
    correct preview and gives clear Print / Save as PDF / Close buttons.
    """

    def __init__(self, header_html, body_html, size_map, printer,
                 resource_doc=None, default_name: str = "email", parent=None):
        super().__init__(parent)
        from PyQt5.QtPrintSupport import QPrintPreviewWidget
        self._header_html = header_html
        self._body_html = body_html
        self._size_map = size_map or {}
        self._printer = printer
        self._resource_doc = resource_doc
        self._default_name = default_name
        self._doc = None  # rebuilt for the current page size

        self.setWindowTitle("Print")
        # Size to fit the available screen so the action buttons at the
        # bottom are never pushed off-screen (a fixed 760px height overflowed
        # smaller/scaled displays). Cap to ~90% of the available work area
        # and center the dialog.
        try:
            from PyQt5.QtWidgets import QApplication
            avail = QApplication.primaryScreen().availableGeometry()
            w = min(820, int(avail.width() * 0.9))
            h = min(760, int(avail.height() * 0.9))
            self.resize(w, h)
            self.move(
                avail.left() + (avail.width() - w) // 2,
                avail.top() + (avail.height() - h) // 2,
            )
        except Exception:
            self.resize(820, 700)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Toolbar: printer picker + zoom
        bar = QFrame()
        bar.setStyleSheet("QFrame { background:#f3f2f1; border-bottom:1px solid #d0d0d0; }")
        bar_l = QHBoxLayout(bar)
        bar_l.setContentsMargins(12, 8, 12, 8)
        bar_l.setSpacing(8)

        bar_l.addWidget(QLabel("Printer:"))
        self.printer_combo = QComboBox()
        self._populate_printers()
        self.printer_combo.currentIndexChanged.connect(self._on_printer_changed)
        bar_l.addWidget(self.printer_combo, 1)

        bar_l.addWidget(QLabel("Layout:"))
        self.orient_combo = QComboBox()
        self.orient_combo.addItems(["Portrait", "Landscape"])
        self.orient_combo.currentIndexChanged.connect(self._on_orient_changed)
        bar_l.addWidget(self.orient_combo)

        # Fit-image-to-page toggle (on by default): scales big posters so the
        # whole image lands on one page instead of splitting across pages.
        from PyQt5.QtWidgets import QCheckBox
        self.fit_chk = QCheckBox("Fit images to page")
        self.fit_chk.setChecked(True)
        self.fit_chk.toggled.connect(lambda _=False: self._rebuild_and_refresh())
        bar_l.addWidget(self.fit_chk)

        zoom_out = QPushButton("－")
        zoom_in = QPushButton("＋")
        for b in (zoom_out, zoom_in):
            b.setFixedWidth(34)
            b.setCursor(Qt.PointingHandCursor)
        zoom_out.clicked.connect(lambda: self.preview.zoomOut(1.15))
        zoom_in.clicked.connect(lambda: self.preview.zoomIn(1.15))
        bar_l.addWidget(zoom_out)
        bar_l.addWidget(zoom_in)
        layout.addWidget(bar)

        # The actual preview
        self.preview = QPrintPreviewWidget(self._printer)
        self.preview.paintRequested.connect(self._render)
        self.preview.fitToWidth()
        layout.addWidget(self.preview, 1)

        # Action buttons
        btns = QFrame()
        btns.setStyleSheet("QFrame { background:#faf9f8; border-top:1px solid #d0d0d0; }")
        btns_l = QHBoxLayout(btns)
        btns_l.setContentsMargins(12, 10, 12, 10)
        btns_l.setSpacing(8)
        btns_l.addStretch(1)

        self.pdf_btn = QPushButton("Save as PDF...")
        self.print_btn = QPushButton("🖨  Print")
        self.print_btn.setDefault(True)
        cancel_btn = QPushButton("Close")
        for b in (self.pdf_btn, self.print_btn, cancel_btn):
            b.setCursor(Qt.PointingHandCursor)
            b.setMinimumWidth(110)
        self.pdf_btn.clicked.connect(self._save_pdf)
        self.print_btn.clicked.connect(self._do_print)
        cancel_btn.clicked.connect(self.reject)
        btns_l.addWidget(self.pdf_btn)
        btns_l.addWidget(self.print_btn)
        btns_l.addWidget(cancel_btn)
        layout.addWidget(btns)

        # Build the initial document for the current (portrait) page size.
        self._rebuild_document()

    # ---- document building ----
    def _printable_size_px(self):
        """Printable area (width, height) in logical px (96 dpi) for the
        current printer page + margins. QTextDocument lays out in logical
        px, so we convert from the printer's physical points."""
        from PyQt5.QtPrintSupport import QPrinter
        try:
            rect = self._printer.pageRect(QPrinter.Point)  # 1/72 inch units
            w_in = rect.width() / 72.0
            h_in = rect.height() / 72.0
        except Exception:
            # A4 portrait fallback minus ~0.5in margins each side.
            w_in, h_in = 7.27, 10.69
        return w_in * 96.0, h_in * 96.0

    def _build_body_html(self):
        """Return body HTML with each <img> sized to fit the page when the
        'Fit images to page' option is on; otherwise capped to page width."""
        import re
        page_w, page_h = self._printable_size_px()
        fit = self.fit_chk.isChecked() if hasattr(self, "fit_chk") else True
        size_map = self._size_map

        def _fix(m):
            tag = m.group(0)
            sm = re.search(r'src\s*=\s*["\']([^"\']+)["\']', tag, re.IGNORECASE)
            src = sm.group(1) if sm else ""
            nat = size_map.get(src)
            # Strip existing width/height attrs and any width/height in style.
            tag = re.sub(r'\s(width|height)\s*=\s*"[^"]*"', "", tag, flags=re.IGNORECASE)
            tag = re.sub(r"\s(width|height)\s*=\s*'[^']*'", "", tag, flags=re.IGNORECASE)
            style_extra = ""
            if nat and nat[0] > 0 and nat[1] > 0:
                nw, nh = nat
                # Scale so width fits the page; if fit-to-page, also cap height.
                scale = min(1.0, page_w / nw)
                if fit:
                    scale = min(scale, page_h / nh)
                tw = max(1, int(nw * scale))
                th = max(1, int(nh * scale))
                style_extra = f"width:{tw}px;height:{th}px;"
            else:
                style_extra = "max-width:100%;height:auto;"
            sstyle = re.search(r'style\s*=\s*"([^"]*)"', tag, re.IGNORECASE)
            if sstyle:
                existing = re.sub(
                    r'(?:max-)?(?:width|height)\s*:[^;]*;?', "", sstyle.group(1),
                    flags=re.IGNORECASE,
                )
                tag = tag[:sstyle.start(1)] + existing + style_extra + tag[sstyle.end(1):]
            else:
                tag = tag[:-1].rstrip() + f' style="{style_extra}">'
            return tag

        return re.sub(r'<img\b[^>]*>', _fix, self._body_html, flags=re.IGNORECASE)

    def _rebuild_document(self):
        from PyQt5.QtCore import QSizeF
        from PyQt5.QtGui import QTextDocument
        doc = QTextDocument()
        page_w, page_h = self._printable_size_px()
        doc.setPageSize(QSizeF(page_w, page_h))
        # Copy image resources from the source body view so data:/cached
        # remote images render in the print document too.
        if self._resource_doc is not None:
            try:
                import re
                from PyQt5.QtCore import QUrl
                for m in re.finditer(
                    r'<img\b[^>]*\bsrc\s*=\s*["\']([^"\']+)["\']',
                    self._body_html, flags=re.IGNORECASE,
                ):
                    src = m.group(1)
                    res = self._resource_doc.resource(
                        QTextDocument.ImageResource, QUrl(src)
                    )
                    if res is not None:
                        doc.addResource(QTextDocument.ImageResource, QUrl(src), res)
            except Exception:
                pass
        doc.setHtml(self._header_html + self._build_body_html())
        self._doc = doc

    def _rebuild_and_refresh(self):
        self._rebuild_document()
        self.preview.updatePreview()

    def _populate_printers(self):
        from PyQt5.QtPrintSupport import QPrinterInfo
        self.printer_combo.clear()
        names = [p.printerName() for p in QPrinterInfo.availablePrinters()]
        # Ensure the currently-selected printer is present/selected.
        current = self._printer.printerName()
        if current and current not in names:
            names.insert(0, current)
        if not names:
            names = [current] if current else ["(no printers)"]
        self.printer_combo.addItems(names)
        if current in names:
            self.printer_combo.setCurrentIndex(names.index(current))

    def _on_printer_changed(self, _idx):
        name = self.printer_combo.currentText()
        if name and name != "(no printers)":
            self._printer.setPrinterName(name)
            self._rebuild_and_refresh()

    def _on_orient_changed(self, _idx):
        from PyQt5.QtPrintSupport import QPrinter
        landscape = self.orient_combo.currentText() == "Landscape"
        self._printer.setOrientation(
            QPrinter.Landscape if landscape else QPrinter.Portrait
        )
        # Page size changed → rebuild so images re-scale to the new page.
        self._rebuild_and_refresh()

    def _render(self, printer):
        if self._doc is not None:
            self._doc.print_(printer)

    def _do_print(self):
        # Render to the selected printer and close.
        if self._doc is not None:
            self._doc.print_(self._printer)
        self.accept()

    def _save_pdf(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "Save as PDF", f"{self._default_name}.pdf", "PDF files (*.pdf)"
        )
        if not path:
            return
        if not path.lower().endswith(".pdf"):
            path += ".pdf"
        from PyQt5.QtPrintSupport import QPrinter as _QPrinter
        pdf = _QPrinter(_QPrinter.HighResolution)
        pdf.setOutputFormat(_QPrinter.PdfFormat)
        if self.orient_combo.currentText() == "Landscape":
            pdf.setOrientation(_QPrinter.Landscape)
        pdf.setOutputFileName(path)
        if self._doc is not None:
            self._doc.print_(pdf)
        QMessageBox.information(self, "Saved", f"Saved PDF to:\n{path}")
        self.accept()


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

        # Header panel wrapped in scroll area for scrolling like compose
        self.header_frame = QFrame()
        self.header_frame.setStyleSheet(
            f"QFrame {{ background: {theme_color('bg')}; "
            f"border-bottom: 1px solid {theme_color('border')}; }}"
        )
        h_layout = QVBoxLayout(self.header_frame)
        h_layout.setContentsMargins(24, 18, 24, 16)
        h_layout.setSpacing(8)

        # Header text fields are plain QLabels, which by default swallow mouse
        # selection — so users couldn't highlight/copy the To/Cc addresses
        # without opening Reply/Forward. Enable mouse + keyboard text selection
        # on every header label so any of them can be selected and copied
        # (Ctrl+C) straight from the reading pane, like Outlook.
        _selectable = Qt.TextSelectableByMouse | Qt.TextSelectableByKeyboard

        self.subject_label = QLabel()
        f = QFont(); f.setPointSize(15); f.setWeight(QFont.DemiBold)
        self.subject_label.setFont(f)
        self.subject_label.setWordWrap(True)
        self.subject_label.setTextInteractionFlags(_selectable)
        self.subject_label.setCursor(Qt.IBeamCursor)
        self.subject_label.setStyleSheet(f"color:{theme_color('text')};")
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
        self.sender_name_label.setTextInteractionFlags(_selectable)
        self.sender_name_label.setCursor(Qt.IBeamCursor)
        self.sender_name_label.setStyleSheet(f"color:{theme_color('text')};")
        self.sender_email_label = QLabel()
        self.sender_email_label.setTextInteractionFlags(_selectable)
        self.sender_email_label.setCursor(Qt.IBeamCursor)
        self.sender_email_label.setStyleSheet(
            f"color:{theme_color('text_muted')}; font-size:9pt;"
        )
        self.recipients_label = QLabel()
        self.recipients_label.setTextInteractionFlags(_selectable)
        self.recipients_label.setCursor(Qt.IBeamCursor)
        self.recipients_label.setStyleSheet(
            f"color:{theme_color('text_muted')}; font-size:9pt;"
        )
        self.recipients_label.setWordWrap(True)
        sender_text_col.addWidget(self.sender_name_label)
        sender_text_col.addWidget(self.sender_email_label)
        sender_text_col.addWidget(self.recipients_label)
        sender_row.addLayout(sender_text_col, 1)

        self.date_label = QLabel()
        self.date_label.setTextInteractionFlags(_selectable)
        self.date_label.setCursor(Qt.IBeamCursor)
        self.date_label.setStyleSheet(
            f"color:{theme_color('text_muted')}; font-size:9pt;"
        )
        self.date_label.setAlignment(Qt.AlignRight | Qt.AlignTop)
        sender_row.addWidget(self.date_label, 0, Qt.AlignTop)

        h_layout.addLayout(sender_row)

        # Cc (only shown if present)
        self.cc_label = QLabel()
        self.cc_label.setTextInteractionFlags(_selectable)
        self.cc_label.setCursor(Qt.IBeamCursor)
        self.cc_label.setStyleSheet(
            f"color:{theme_color('text_muted')}; font-size:9pt; padding-left:56px;"
        )
        self.cc_label.setWordWrap(True)
        h_layout.addWidget(self.cc_label)

        # Action buttons row
        btn_row = QHBoxLayout()
        btn_row.setContentsMargins(0, 10, 0, 0)
        btn_row.setSpacing(6)
        self.reply_btn = QPushButton("↩  Reply")
        self.reply_all_btn = QPushButton("↩↩  Reply All")
        self.forward_btn = QPushButton("➡  Forward")
        self.print_btn = QPushButton("🖨  Print")
        for b in (self.reply_btn, self.reply_all_btn, self.forward_btn,
                  self.print_btn):
            b.setCursor(Qt.PointingHandCursor)
            btn_row.addWidget(b)
        btn_row.addStretch(1)
        self.reply_btn.clicked.connect(lambda: self.reply_requested.emit(self.email, "reply"))
        self.reply_all_btn.clicked.connect(lambda: self.reply_requested.emit(self.email, "reply_all"))
        self.forward_btn.clicked.connect(lambda: self.reply_requested.emit(self.email, "forward"))
        self.print_btn.clicked.connect(self._print_email)
        h_layout.addLayout(btn_row)

        # Attachments (below header, outside scroll)
        self.att_frame = QFrame()
        self.att_frame.setStyleSheet(
            "QFrame { background:#fff8e1; border-bottom: 1px solid #e0d090; }"
        )
        a_layout = QVBoxLayout(self.att_frame)
        a_layout.setContentsMargins(14, 6, 14, 6)
        att_label = QLabel("📎 Attachments (double-click to open, right-click to save):")
        att_label.setStyleSheet("color:#604000;")
        a_layout.addWidget(att_label)
        self.att_list = QListWidget()
        self.att_list.setMaximumHeight(120)
        self.att_list.itemDoubleClicked.connect(self._open_attachment)
        self.att_list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.att_list.customContextMenuRequested.connect(self._att_context_menu)
        a_layout.addWidget(self.att_list)
        self.att_frame.setVisible(False)

        # "Remote images blocked" banner (Outlook-style). Hidden unless the
        # current message has remote images that we chose not to load.
        self.img_banner = QFrame()
        self.img_banner.setStyleSheet(
            "QFrame { background:#fff4ce; border-bottom:1px solid #e6d27a; }"
        )
        ib_layout = QHBoxLayout(self.img_banner)
        ib_layout.setContentsMargins(16, 8, 16, 8)
        ib_layout.setSpacing(10)
        self.img_banner_label = QLabel("Remote images in this message were not downloaded.")
        self.img_banner_label.setStyleSheet("color:#5c4a00; background:transparent;")
        self.img_banner_label.setWordWrap(True)
        ib_layout.addWidget(self.img_banner_label, 1)
        self.show_images_btn = QPushButton("Show images")
        self.show_images_btn.setCursor(Qt.PointingHandCursor)
        self.show_images_btn.clicked.connect(self._show_remote_images)
        ib_layout.addWidget(self.show_images_btn, 0)
        self.img_banner.setVisible(False)

        # Body viewer — grows to fit content; the outer scroll area below
        # scrolls header + attachments + body together as one unit (Outlook
        # reading-pane style), instead of two separate scroll regions.
        self.body_view = NetworkAwareTextBrowser()
        self.body_view.setOpenExternalLinks(True)
        self.body_view.images_blocked.connect(self._on_images_blocked)
        self.body_view.setStyleSheet(
            "QTextBrowser { background:#ffffff; color:#201f1e; "
            "border:none; padding:16px 20px; }"
        )

        # Single content container: everything scrolls together.
        content = QWidget()
        content.setStyleSheet(f"background:{theme_color('bg')};")
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(0)
        content_layout.addWidget(self.header_frame)
        content_layout.addWidget(self.att_frame)
        content_layout.addWidget(self.img_banner)
        content_layout.addWidget(self.body_view)
        content_layout.addStretch(0)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll.setStyleSheet(
            f"QScrollArea {{ border: none; background: {theme_color('bg')}; }}"
        )
        self.scroll.setWidget(content)
        layout.addWidget(self.scroll, 1)

        # The body grows to fit its content and has no scrollbar of its own,
        # so wheel events over it must drive the outer scroll area. Intercept
        # them here and relay to the unified pane's vertical scrollbar.
        self.body_view.viewport().installEventFilter(self)

    def eventFilter(self, obj, event):
        from PyQt5.QtCore import QEvent
        if obj is self.body_view.viewport() and event.type() == QEvent.Wheel:
            sb = self.scroll.verticalScrollBar()
            sb.setValue(sb.value() - event.angleDelta().y())
            return True
        return super().eventFilter(obj, event)

    def apply_theme(self):
        """Re-apply theme-dependent styling after a light/dark switch."""
        self.header_frame.setStyleSheet(
            f"QFrame {{ background: {theme_color('bg')}; "
            f"border-bottom: 1px solid {theme_color('border')}; }}"
        )
        self.scroll.setStyleSheet(
            f"QScrollArea {{ border: none; background: {theme_color('bg')}; }}"
        )
        self.subject_label.setStyleSheet(f"color:{theme_color('text')};")
        self.sender_name_label.setStyleSheet(f"color:{theme_color('text')};")
        muted = f"color:{theme_color('text_muted')}; font-size:9pt;"
        self.sender_email_label.setStyleSheet(muted)
        self.recipients_label.setStyleSheet(muted)
        self.date_label.setStyleSheet(muted)
        self.cc_label.setStyleSheet(muted + " padding-left:56px;")
        # If nothing is open, re-theme the empty body surface too.
        if self.email is None:
            self.body_view.setStyleSheet(
                f"QTextBrowser {{ background:{theme_color('bg')}; "
                f"color:{theme_color('text_muted')}; border:none; padding:16px 20px; }}"
            )

    def show_empty(self):
        self.email = None
        # Empty state: blend the body into the themed surface (in dark mode a
        # bright white page with no content looks broken). A real message
        # switches back to a white page in show_email().
        self.body_view.setStyleSheet(
            f"QTextBrowser {{ background:{theme_color('bg')}; "
            f"color:{theme_color('text_muted')}; border:none; padding:16px 20px; }}"
        )
        self.subject_label.setText(
            f"<span style='color:{theme_color('text_disabled')};'>"
            "Select an email to read</span>"
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
        self.img_banner.setVisible(False)
        self.body_view.setHtml("")
        for b in (self.reply_btn, self.reply_all_btn, self.forward_btn,
                  self.print_btn):
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

        for b in (self.reply_btn, self.reply_all_btn, self.forward_btn,
                  self.print_btn):
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

        # Remote images: honour the user's "block remote images" preference.
        # Fresh per message — reset the banner and apply the current setting
        # before rendering. The browser emits images_blocked() if it hides
        # any, which raises the banner.
        from core import config
        self.img_banner.setVisible(False)
        self.body_view.set_block_images(bool(config.get("block_remote_images", False)))

        # Body: prefer HTML when available. Switch back to a WHITE page for
        # the actual message (sender HTML assumes a light background).
        self.body_view.setStyleSheet(
            "QTextBrowser { background:#ffffff; color:#201f1e; "
            "border:none; padding:16px 20px; }"
        )
        html = email.get("body_html")
        plain = email.get("body_plain") or ""
        folder = (email.get("folder") or "").lower()
        # Outbox rows re-purpose body_html to stash the raw MIME bytes
        # (base64) so the sender worker can re-transmit the exact wire bytes.
        # That base64 blob is NOT meant for display. Parse the raw MIME back
        # into a proper HTML/plain body so a queued email looks exactly like
        # it will when sent (formatted, with the signature) — not gibberish.
        if folder == "outbox":
            html = None
            try:
                from core import database as _db
                from core.mail_parser import parse_message
                raw = _db.get_outbox_raw(email["id"])
                if raw:
                    parsed, _atts = parse_message(raw)
                    html = (parsed.get("body_html") or "").strip() or None
                    if not html:
                        plain = (parsed.get("body_plain") or "").strip() or plain
            except Exception:
                html = None  # fall back to stored plain text
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
        # New message → scroll the unified reading pane back to the top.
        self.scroll.verticalScrollBar().setValue(0)

    def _on_images_blocked(self, count: int):
        """The body had remote images we didn't load → offer to show them."""
        plural = "image" if count == 1 else "images"
        self.img_banner_label.setText(
            f"This message has {count} remote {plural} that "
            f"weren't downloaded to protect your privacy."
        )
        self.img_banner.setVisible(True)

    def _show_remote_images(self):
        """User clicked 'Show images' — re-render this message with images."""
        self.img_banner.setVisible(False)
        self.body_view.reload_with_images()

    def _collect_image_sizes(self, html: str) -> dict:
        """Return {img_src: (width_px, height_px)} for every <img> in the
        body, using the body view's loaded resources (so we get the real
        decoded pixel size, including inlined data: URIs and downloaded
        remote images). Used by the print dialog to scale each image so it
        fits on a single page."""
        import re
        from PyQt5.QtCore import QUrl
        from PyQt5.QtGui import QTextDocument, QImage, QPixmap
        out = {}
        if not html:
            return out
        doc = self.body_view.document()
        for m in re.finditer(r'<img\b[^>]*\bsrc\s*=\s*["\']([^"\']+)["\']',
                             html, flags=re.IGNORECASE):
            src = m.group(1)
            if src in out:
                continue
            try:
                res = doc.resource(QTextDocument.ImageResource, QUrl(src))
                img = None
                if isinstance(res, QImage):
                    img = res
                elif isinstance(res, QPixmap):
                    img = res.toImage()
                if img is not None and not img.isNull():
                    out[src] = (img.width(), img.height())
            except Exception:
                pass
        return out

    def _print_email(self):
        """Open the system print dialog (with preview) for the current email.

        We build a fresh QTextDocument containing a small header block
        (subject, from, to, cc, date) followed by the message body, so the
        printout looks like a proper email rather than just the raw body.
        The preview dialog lets the user pick a physical printer OR
        "Microsoft Print to PDF" / "Save as PDF" to keep a digital copy.
        """
        if not self.email:
            return
        try:
            from PyQt5.QtPrintSupport import QPrinter
            from PyQt5.QtGui import QTextDocument
        except Exception as e:
            QMessageBox.warning(
                self, "Print unavailable",
                f"Printing support could not be loaded: {e}",
            )
            return

        import html as _html
        email = self.email
        subject = email.get("subject") or "(no subject)"
        sender = email.get("sender") or ""
        to = email.get("recipients") or ""
        cc = email.get("cc") or ""
        date = email.get("date_sent") or email.get("date_received") or ""
        date_pretty = self._format_date(date)

        # Body: reuse what the viewer resolved. Prefer the rendered HTML in
        # the body view so the printout matches what the user sees (signature,
        # formatting, inlined images included).
        body_html = self.body_view.toHtml()
        # Measure every image's natural size so the print dialog can scale
        # each one to fit a single page (both width AND height). Width-only
        # constraints made tall/large posters spill onto a second page.
        size_map = self._collect_image_sizes(body_html)

        def _row(label, value):
            if not value:
                return ""
            return (
                f'<tr><td style="padding:1px 8px 1px 0;color:#555;'
                f'white-space:nowrap;vertical-align:top;"><b>{label}</b></td>'
                f'<td style="padding:1px 0;color:#111;">'
                f'{_html.escape(value)}</td></tr>'
            )

        header_html = (
            '<div style="font-family:Segoe UI,Arial,sans-serif;'
            'font-size:11pt;color:#111;">'
            f'<div style="font-size:15pt;font-weight:bold;margin-bottom:8px;">'
            f'{_html.escape(subject)}</div>'
            '<table style="font-size:10pt;border-collapse:collapse;'
            'margin-bottom:10px;">'
            + _row("From:", sender)
            + _row("To:", to)
            + _row("Cc:", cc)
            + _row("Date:", date_pretty)
            + '</table>'
            '<hr style="border:none;border-top:1px solid #bbb;margin:0 0 12px 0;">'
            '</div>'
        )

        printer = QPrinter(QPrinter.HighResolution)
        printer.setDocName(subject)

        # Use our own preview dialog (Qt-rendered preview + clear Print /
        # Save as PDF buttons). It rebuilds the document for the current page
        # size so images are scaled to fit one page in either orientation.
        safe_name = "".join(
            c for c in subject if c.isalnum() or c in " -_"
        ).strip()[:60] or "email"
        dlg = _PrintPreviewDialog(
            header_html, body_html, size_map, printer,
            resource_doc=self.body_view.document(),
            default_name=safe_name, parent=self,
        )
        dlg.exec_()

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
            # Parse the ISO timestamp, interpreting 'Z' as UTC
            dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
            # Convert to local time so the UI shows the user's timezone
            dt = dt.astimezone()
            return dt.strftime("%a, %d %b %Y, %H:%M")
        except Exception:
            return iso[:16]

    def _att_context_menu(self, pos):
        item = self.att_list.itemAt(pos)
        if item is None:
            return
        menu = QMenu(self)
        open_act = menu.addAction("Open")
        save_act = menu.addAction("Save As…")
        chosen = menu.exec_(self.att_list.mapToGlobal(pos))
        if chosen == open_act:
            self._open_attachment(item)
        elif chosen == save_act:
            self._save_attachment(item)

    def _open_attachment(self, item: QListWidgetItem):
        """Write the attachment to a temp file and open it with the OS default
        application (e.g. .xlsx → Excel, .pdf → PDF viewer)."""
        att_id = item.data(Qt.UserRole)
        att = database.get_attachment(att_id)
        if not att:
            return
        name = os.path.basename((att.get("filename") or "").strip()) or "attachment"
        tmp_dir = os.path.join(tempfile.gettempdir(), "RunLabMail", "attachments")
        try:
            os.makedirs(tmp_dir, exist_ok=True)
            path = os.path.join(tmp_dir, name)
            with open(path, "wb") as f:
                f.write(att["data"])
        except Exception as e:
            QMessageBox.warning(self, "Open failed", f"Could not open attachment:\n{e}")
            return
        if not QDesktopServices.openUrl(QUrl.fromLocalFile(path)):
            QMessageBox.warning(
                self, "No application",
                f"Windows could not find an application to open\n{name}.\n\n"
                f"Right-click the attachment and choose \"Save As…\" instead.",
            )

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
