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

from PyQt5.QtCore import Qt, pyqtSignal, QThread, QMimeData, QUrl
from PyQt5.QtGui import QImage
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
                          max_image_width: int=_DEFAULT_MAX_IMG_WIDTH,
                          constrain_images: bool=True
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
                +re.escape(url)
                +r'([\'"])',
                re.IGNORECASE,
            )
            html = url_pat.sub(
                lambda m, du=data_uri: m.group(1) + du + m.group(2), html
            )

    # Optionally constrain image widths so giant pasted logos don't dominate.
    # For non-Excel table signatures we keep original dimensions to preserve
    # layout fidelity (closer to Outlook paste behavior).
    if constrain_images:
        html = _constrain_image_widths(html, max_image_width)
    return html, downloaded, failed


def _register_data_images_on_document(document, html: str) -> str:
    img_pattern = re.compile(r'<img\b([^>]*)>', re.IGNORECASE)

    def _process(match: re.Match) -> str:
        attrs = match.group(1)
        src_m = re.search(r'\bsrc\s*=\s*([\'"])(data:image/[^\'"]+)\1', attrs, re.IGNORECASE)
        if not src_m:
            return match.group(0)
        url = src_m.group(2)
        comma = url.find(",")
        if comma < 0:
            return match.group(0)
        meta = url[:comma].lower()
        payload = url[comma + 1:]
        if ";base64" not in meta:
            return match.group(0)
        try:
            data = base64.b64decode(payload)
        except Exception:
            return match.group(0)
        image = QImage.fromData(data)
        if image.isNull():
            return match.group(0)
        name = f"runlabmail-paste-image-{abs(hash(url))}"
        document.addResource(document.ImageResource, QUrl(name), image)
        attrs = attrs[:src_m.start(2)] + name + attrs[src_m.end(2):]
        return f'<img{attrs}>'

    return img_pattern.sub(_process, html)


def _constrain_image_widths(html: str, max_width: int) -> str:
    """Ensure every <img> has a width attribute capped at max_width.

    If the tag already has a width attr smaller than max_width, leave it
    alone. Otherwise force width=<max_width> and remove conflicting style
    width / height. Height is removed so aspect ratio is preserved.
    """
    img_pattern = re.compile(r'<img\b([^>]*)>', re.IGNORECASE)

    def _to_px(value: str, unit: str | None) -> int | None:
        try:
            n = float(value)
        except Exception:
            return None
        u = (unit or "px").lower()
        if u == "pt":
            return int(round(n * 96.0 / 72.0))
        return int(round(n))

    def _process(match: re.Match) -> str:
        attrs = match.group(1)
        # Existing width attr (number form)
        m_w = re.search(r'\bwidth\s*=\s*[\'"]?(\d+)', attrs, re.IGNORECASE)
        existing_w = int(m_w.group(1)) if m_w else None

        # Existing style width / max-width in px/pt
        m_style = re.search(r'\bstyle\s*=\s*[\'"]([^\'"]*)[\'"]', attrs,
                            re.IGNORECASE)
        style_value = m_style.group(1) if m_style else ""
        m_style_w = re.search(r'\bwidth\s*:\s*(\d+(?:\.\d+)?)\s*(px|pt)?', style_value,
                              re.IGNORECASE)
        m_style_max_w = re.search(r'\bmax-width\s*:\s*(\d+(?:\.\d+)?)\s*(px|pt)?', style_value,
                                  re.IGNORECASE)
        m_style_h = re.search(r'\bheight\s*:\s*(\d+(?:\.\d+)?)\s*(px|pt)?', style_value,
                              re.IGNORECASE)
        style_w = _to_px(m_style_w.group(1), m_style_w.group(2)) if m_style_w else None
        style_max_w = _to_px(m_style_max_w.group(1), m_style_max_w.group(2)) if m_style_max_w else None
        style_h = _to_px(m_style_h.group(1), m_style_h.group(2)) if m_style_h else None

        # Decide based on hard widths first (width attr / style width).
        # We cannot trust style max-width alone because Qt may still render
        # oversized images when width attr is large.
        hard_widths = [w for w in (existing_w, style_w) if w is not None]
        if hard_widths and max(hard_widths) <= max_width:
            # Qt rich text ignores CSS width/height — mirror them into
            # explicit HTML attributes so Qt renders at the correct size.
            needs_w = existing_w is None and style_w is not None
            m_eh = re.search(r'\bheight\s*=\s*[\'"]?(\d+)', attrs, re.IGNORECASE)
            existing_h = int(m_eh.group(1)) if m_eh else None
            needs_h = existing_h is None and style_h is not None
            if needs_w or needs_h:
                attrs2 = attrs.rstrip().rstrip('/').rstrip()
                if needs_w:
                    attrs2 = re.sub(
                        r'\s*\bwidth\s*=\s*[\'"]?[^\s\'">]+[\'"]?',
                        '', attrs2, flags=re.IGNORECASE,
                    )
                    attrs2 += f' width="{style_w}"'
                if needs_h:
                    attrs2 = re.sub(
                        r'\s*\bheight\s*=\s*[\'"]?[^\s\'">]+[\'"]?',
                        '', attrs2, flags=re.IGNORECASE,
                    )
                    attrs2 += f' height="{style_h}"'
                return f'<img{attrs2} />'
            return match.group(0)  # already small enough

        # No hard width set; only max-width hint present and already safe.
        if not hard_widths and style_max_w is not None and style_max_w <= max_width:
            return match.group(0)

        # Helper: merge a max-width cap into style without forcing width=.
        def _add_max_width_only(a: str) -> str:
            style_m = re.search(r'\bstyle\s*=\s*([\'"])(.*?)\1', a,
                                re.IGNORECASE | re.DOTALL)
            if style_m:
                q = style_m.group(1)
                cur = style_m.group(2)
                cur = re.sub(r'(?:^|;)\s*(?:max-)?width\s*:[^;]*;?', ';', cur,
                             flags=re.IGNORECASE)
                cur = re.sub(r'(?:^|;)\s*(?:max-)?height\s*:[^;]*;?', ';', cur,
                             flags=re.IGNORECASE)
                cur = cur.strip(' ;')
                merged = _append_style(cur, f"max-width:{max_width}px;height:auto;")
                return a[:style_m.start()] + f' style={q}{merged}{q}' + a[style_m.end():]
            return a + f' style="max-width:{max_width}px;height:auto;"'

        # No explicit width info: cap with max-width only (no forced width)
        # so small icons stay small while large logos are constrained.
        if existing_w is None and style_w is None and style_max_w is None:
            attrs2 = _add_max_width_only(attrs)
            return f'<img{attrs2}>'

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

        # Insert max-width cap without forcing explicit width= (prevents upscaling)
        attrs = _add_max_width_only(attrs)
        return f'<img{attrs}>'

    return img_pattern.sub(_process, html)


def _append_style(existing: str, extra: str) -> str:
    existing = (existing or "").strip()
    extra = (extra or "").strip()
    if not existing:
        return extra
    if not extra:
        return existing
    if existing.endswith(";"):
        return existing + extra
    return existing + ";" + extra


def _normalize_background_shorthand(style: str) -> str:
    """Make Excel cell fills survive Qt's rich-text table renderer.

    Excel emits cell fills as the `background:` shorthand (e.g.
    `background:#FFFF00`). Qt's QTextDocument honors `background-color`
    on table cells but frequently ignores the `background` shorthand, so
    pasted fills came through colorless. When we see a `background:` whose
    value is a plain color, we also emit an explicit `background-color:` so
    the fill renders. The original shorthand is left in place for clients
    that prefer it.
    """
    if not style or "background" not in style.lower():
        return style

    color_pat = re.compile(
        r'(?<![-\w])background\s*:\s*'
        r'(#[0-9a-fA-F]{3,8}|rgb[a]?\([^)]*\)|[a-zA-Z]+)\s*;?',
        re.IGNORECASE,
    )

    def _repl(m: re.Match) -> str:
        color = m.group(1).strip()
        # Skip non-color shorthands (gradients/images/"none"/"transparent").
        if color.lower() in ("none", "transparent", "inherit", "initial"):
            return m.group(0)
        decl = m.group(0)
        if not decl.rstrip().endswith(";"):
            decl = decl.rstrip() + ";"
        return f"{decl}background-color:{color};"

    return color_pat.sub(_repl, style)


def _inline_table_class_styles(html: str) -> str:
    """Inline class-based CSS for pasted tables (e.g. Excel clipboard HTML).


    Excel usually emits styles in <style> blocks and references them via
    class="xlNN" on <table>/<tr>/<td>. Qt rich text often drops class styles,
    so we lift those rules into inline style attributes before insertion.
    """
    style_blocks = re.findall(
        r'<style\b[^>]*>(.*?)</style>',
        html,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if not style_blocks:
        return html

    class_styles: dict[str, str] = {}
    for block in style_blocks:
        for m in re.finditer(r'\.([A-Za-z0-9_-]+)\s*\{([^}]*)\}', block, re.DOTALL):
            cls = m.group(1).strip()
            style = _normalize_background_shorthand(m.group(2).strip())
            if cls and style:
                class_styles[cls] = _append_style(class_styles.get(cls, ""), style)


    if not class_styles:
        return html

    # Match class attributes in all three quoting styles Excel/webmail emit:
    #   class="xl65"  class='xl65'  class=xl65   (Excel uses UNQUOTED a lot)
    # The old pattern only matched double-quoted values, so Excel's
    # class-based colors/fonts/backgrounds were never inlined — pasted
    # tables lost all their formatting and kept only fallback borders.
    tag_pat = re.compile(
        r'<([a-z0-9]+)([^>]*?\sclass\s*=\s*'
        r'(?:"([^"]*)"|\'([^\']*)\'|([^\s>]+)))([^>]*)>',
        re.IGNORECASE,
    )

    def repl(m: re.Match) -> str:
        tag = m.group(1)
        before = m.group(2)
        # class value is in whichever of groups 3/4/5 matched
        class_value = m.group(3) or m.group(4) or m.group(5) or ""
        after = m.group(6)

        merged = ""
        for cls in class_value.split():
            merged = _append_style(merged, class_styles.get(cls, ""))


        attrs = before + after
        style_m = re.search(r'\bstyle\s*=\s*(["\"])(.*?)\1', attrs, re.IGNORECASE | re.DOTALL)
        if style_m:
            q = style_m.group(1)
            existing = style_m.group(2)
            combined = _append_style(existing, merged)
            attrs = (
                attrs[:style_m.start()]
                +f' style={q}{combined}{q}'
                +attrs[style_m.end():]
            )
        elif merged:
            attrs += f' style="{merged}"'

        return f"<{tag}{attrs}>"

    return tag_pat.sub(repl, html)


def _normalize_excel_table_html(html: str) -> str:
    """Best-effort normalization so pasted Excel tables keep layout in Qt."""
    if not re.search(r'<table\b', html, re.IGNORECASE):
        return html

    html = _inline_table_class_styles(html)

    # Fallback table borders if clipboard CSS was lost/partial.
    def _table_repl(m: re.Match) -> str:
        attrs = m.group(1)
        style_m = re.search(r'\bstyle\s*=\s*(["\"])(.*?)\1', attrs, re.IGNORECASE | re.DOTALL)
        base = "border-collapse:collapse;border:1px solid #8a8a8a;"
        if style_m:
            q = style_m.group(1)
            existing = style_m.group(2)
            combined = _append_style(existing, base)
            attrs = attrs[:style_m.start()] + f' style={q}{combined}{q}' + attrs[style_m.end():]
        else:
            attrs += f' style="{base}"'
        return f"<table{attrs}>"

    html = re.sub(r'<table\b([^>]*)>', _table_repl, html, flags=re.IGNORECASE)

    def _cell_repl(m: re.Match) -> str:
        tag = m.group(1)
        attrs = m.group(2)
        style_m = re.search(r'\bstyle\s*=\s*(["\"])(.*?)\1', attrs, re.IGNORECASE | re.DOTALL)
        base = "border:1px solid #8a8a8a;padding:2px 4px;"
        if style_m:
            q = style_m.group(1)
            existing = style_m.group(2)
            combined = _append_style(existing, base)
            attrs = attrs[:style_m.start()] + f' style={q}{combined}{q}' + attrs[style_m.end():]
        else:
            attrs += f' style="{base}"'
        return f"<{tag}{attrs}>"

    html = re.sub(r'<(td|th)\b([^>]*)>', _cell_repl, html, flags=re.IGNORECASE)
    return html


def _looks_like_excel_html(html: str) -> bool:
    """Heuristic: detect Excel clipboard HTML vs generic table HTML."""
    h = (html or "").lower()
    return (
        "xmlns:x=\"urn:schemas-microsoft-com:office:excel\"" in h
        or "name=\"progid\" content=\"excel.sheet\"" in h
        or "mso-" in h
        # Match xlNN class refs in any quoting style Excel emits:
        #   class="xl65"  class='xl65'  class=xl65 (unquoted is common)
        or bool(re.search(
            r'class\s*=\s*(?:"[^"]*\bxl\d+|\'[^\']*\bxl\d+|xl\d+)', h
        ))
    )



class _PasteWorker(QThread):
    finished = pyqtSignal(str, int, int)  # html, downloaded, failed
    progress = pyqtSignal(int, int)  # current, total

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

    def shrink_oversized_images(self, max_width: int=_DEFAULT_MAX_IMG_WIDTH):
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

        html = _constrain_image_widths(source.html(), _DEFAULT_MAX_IMG_WIDTH)
        has_table = bool(re.search(r'<table\b', html, re.IGNORECASE))
        # Preserve Excel table look by inlining class CSS before Qt ingests it.
        # Important: do NOT apply this to non-Excel signatures/documents,
        # otherwise they get unwanted borders and broken layout.
        if has_table and _looks_like_excel_html(html):
            normalized = _normalize_excel_table_html(html)
            normalized = _register_data_images_on_document(self.document(), normalized)
            self.textCursor().insertHtml(normalized)
            return

        # Generic HTML signatures (customer templates, webmail copy, etc.)
        # often rely on class-based styles inside <style> blocks. Qt may drop
        # those classes, which breaks table widths/logo sizing. For non-Excel
        # table HTML we inline the class rules, but we do NOT add Excel-style
        # fallback borders.
        if has_table:
            html = _inline_table_class_styles(html)

        # If the HTML has no external image URLs (http/https), nothing for
        # us to embed — let Qt handle the paste natively. This preserves
        # complex content like Excel tables, Word documents, etc. that
        # our regex-based pipeline would otherwise mangle.
        external_imgs = re.search(
            r'<img\b[^>]*\bsrc\s*=\s*[\'"]https?://',
            html, re.IGNORECASE,
        )
        if not external_imgs:
            if has_table:
                # Manual HTML signature pastes (already-inline images) can
                # still carry very large explicit image widths. Clamp oversized
                # images before insertion while keeping small images untouched.
                html = _constrain_image_widths(html, _DEFAULT_MAX_IMG_WIDTH)
                html = _register_data_images_on_document(self.document(), html)
                self.textCursor().insertHtml(html)
            else:
                super().insertFromMimeData(source)
            return

        # Otherwise we run the full embed/constrain pipeline so signatures
        # with remote images become self-contained.
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            new_html, _, _ = _embed_images_in_html(
                html,
                constrain_images=True,
            )
        finally:
            QApplication.restoreOverrideCursor()
        new_html = _register_data_images_on_document(self.document(), new_html)
        self.textCursor().insertHtml(new_html)
