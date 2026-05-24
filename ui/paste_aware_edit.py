"""
QTextEdit subclass that, on paste, downloads any external <img src="http..."/>
references and inlines them as data: URIs. This makes pasted signatures
self-contained — they survive copy/paste, work offline, and can be sent
to other recipients without broken images.

Usage:
    editor = PasteAwareTextEdit()
    editor.set_image_download_enabled(True)  # default
"""
import base64
import re
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

from PyQt5.QtCore import Qt, QObject, pyqtSignal, QThread, QMimeData
from PyQt5.QtWidgets import QTextEdit, QApplication


_DEFAULT_TIMEOUT = 6  # seconds per image
_MAX_IMAGE_BYTES = 5 * 1024 * 1024  # 5 MB cap per image
_MAX_TOTAL_IMAGES = 20  # don't download more than this in one paste
_USER_AGENT = "Mozilla/5.0 (RunLabMail; signature paste)"
_DEFAULT_MAX_IMG_WIDTH = 400  # corporate logos ~300px, banners ~200px


def _fetch_image(url: str) -> tuple[str, bytes | None, str]:
    """Download a single image. Returns (url, bytes_or_None, mime_type)."""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
        with urllib.request.urlopen(req, timeout=_DEFAULT_TIMEOUT) as resp:
            ctype = resp.headers.get("Content-Type", "image/png").split(";")[0].strip()
            data = resp.read(_MAX_IMAGE_BYTES + 1)
            if len(data) > _MAX_IMAGE_BYTES:
                return url, None, ctype  # too large
        return url, data, ctype
    except Exception:
        return url, None, "image/png"


def _embed_images_in_html(html: str, on_progress=None,
                          max_image_width: int = _DEFAULT_MAX_IMG_WIDTH
                          ) -> tuple[str, int, int]:
    """Replace external <img src="http..."> with data: URIs.

    Returns (new_html, downloaded_count, failed_count).
    Skips data:, cid:, and file: URIs (data: is already inline; cid: refers
    to attachments we don't have; file: is local-only and unsafe to embed
    silently).

    Also constrains every image to `max_image_width` (preserving aspect
    ratio) so giant company logos don't dominate the editor.
    """
    # Find all unique http(s) image URLs
    pattern = re.compile(
        r'<img\b[^>]*\bsrc\s*=\s*[\'"]([^\'"]+)[\'"]', re.IGNORECASE
    )
    matches = pattern.findall(html)
    urls = []
    seen = set()
    for u in matches:
        u_lower = u.lower()
        if u_lower.startswith(("data:", "cid:", "file:")):
            continue
        if not u_lower.startswith(("http://", "https://")):
            continue
        if u in seen:
            continue
        seen.add(u)
        urls.append(u)

    downloaded = 0
    failed = 0
    if urls:
        urls = urls[:_MAX_TOTAL_IMAGES]
        results: dict[str, tuple[bytes | None, str]] = {}

        with ThreadPoolExecutor(max_workers=4) as ex:
            futures = [ex.submit(_fetch_image, u) for u in urls]
            for i, fut in enumerate(as_completed(futures), start=1):
                url, data, ctype = fut.result()
                results[url] = (data, ctype)
                if on_progress:
                    on_progress(i, len(urls))

        downloaded = sum(1 for d, _ in results.values() if d is not None)
        failed = len(results) - downloaded

        for url, (data, ctype) in results.items():
            if data is None:
                continue
            b64 = base64.b64encode(data).decode("ascii")
            data_uri = f"data:{ctype};base64,{b64}"
            url_pat = re.compile(
                r'(<img\b[^>]*\bsrc\s*=\s*[\'"])'
                + re.escape(url)
                + r'([\'"])',
                re.IGNORECASE,
            )
            html = url_pat.sub(
                lambda m, du=data_uri: m.group(1) + du + m.group(2), html
            )

    # Constrain image widths regardless of whether they were external or inline
    html = _constrain_image_widths(html, max_image_width)
    return html, downloaded, failed


def _constrain_image_widths(html: str, max_width: int) -> str:
    """Ensure every <img> has a width attribute capped at max_width.

    If the tag already has a width attr smaller than max_width, leave it
    alone. Otherwise force width=<max_width> and remove conflicting style
    width / height. Height is removed so aspect ratio is preserved.
    """
    img_pattern = re.compile(r'<img\b([^>]*)>', re.IGNORECASE)

    def _process(match: re.Match) -> str:
        attrs = match.group(1)
        # Existing width attr (number form)
        m_w = re.search(r'\bwidth\s*=\s*[\'"]?(\d+)', attrs, re.IGNORECASE)
        existing_w = int(m_w.group(1)) if m_w else None

        if existing_w is not None and existing_w <= max_width:
            return match.group(0)  # already small enough

        # Strip width=, height=, and width:/height: from style
        attrs = re.sub(r'\s*\bwidth\s*=\s*[\'"]?[^\s\'">]+[\'"]?', '', attrs,
                       flags=re.IGNORECASE)
        attrs = re.sub(r'\s*\bheight\s*=\s*[\'"]?[^\s\'">]+[\'"]?', '', attrs,
                       flags=re.IGNORECASE)
        # Remove style width: and height: too
        def _strip_style_dims(m_style: re.Match) -> str:
            style = m_style.group(1)
            style = re.sub(r'(?:^|;)\s*(?:max-)?width\s*:[^;]*;?', ';', style,
                           flags=re.IGNORECASE)
            style = re.sub(r'(?:^|;)\s*(?:max-)?height\s*:[^;]*;?', ';', style,
                           flags=re.IGNORECASE)
            style = style.strip(" ;")
            return f' style="{style}"' if style else ''
        attrs = re.sub(r'\s*\bstyle\s*=\s*[\'"]([^\'"]*)[\'"]',
                       _strip_style_dims, attrs, flags=re.IGNORECASE)

        # Insert constrained dimensions
        return f'<img{attrs} width="{max_width}" style="max-width:{max_width}px;height:auto;">'

    return img_pattern.sub(_process, html)


class _PasteWorker(QThread):
    finished = pyqtSignal(str, int, int)  # html, downloaded, failed
    progress = pyqtSignal(int, int)       # current, total

    def __init__(self, html: str, parent=None):
        super().__init__(parent)
        self.html = html

    def run(self):
        new_html, downloaded, failed = _embed_images_in_html(
            self.html,
            on_progress=lambda c, t: self.progress.emit(c, t),
        )
        self.finished.emit(new_html, downloaded, failed)


class PasteAwareTextEdit(QTextEdit):
    """QTextEdit that downloads external images on paste."""

    paste_progress = pyqtSignal(int, int)  # downloaded, total
    paste_finished = pyqtSignal(int, int)  # downloaded, failed

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptRichText(True)
        self._image_download_enabled = True
        self._paste_worker: _PasteWorker | None = None

    def shrink_oversized_images(self, max_width: int = _DEFAULT_MAX_IMG_WIDTH):
        """Resize any image in the current document that's wider than
        max_width. Useful when reopening a previously-pasted signature
        whose images were stored at full original size."""
        html = self.toHtml()
        new_html = _constrain_image_widths(html, max_width)
        if new_html != html:
            self.setHtml(new_html)

    def insertFromMimeData(self, source: QMimeData):
        if not self._image_download_enabled or not source.hasHtml():
            super().insertFromMimeData(source)
            return

        html = source.html()
        # Always pass through the embed/constrain pipeline. Even if there
        # are no external image URLs, _embed_images_in_html will at least
        # cap the size of any inline <img> tags.
        QApplication.setOverrideCursor(Qt.BusyCursor)
        try:
            new_html, _, _ = _embed_images_in_html(html)
        finally:
            QApplication.restoreOverrideCursor()
        self.textCursor().insertHtml(new_html)
