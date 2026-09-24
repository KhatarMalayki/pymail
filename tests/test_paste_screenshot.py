import os
from types import MethodType, SimpleNamespace
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtCore import QMimeData
from PyQt5.QtGui import QColor, QImage
from PyQt5.QtWidgets import QApplication, QLineEdit

from core.smtp_client import build_message
from ui.compose_dialog import ComposeDialog
from ui.paste_aware_edit import PasteAwareTextEdit, _SCREENSHOT_MAX_WIDTH


class PasteScreenshotTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def _paste_screenshot(self, width=1200, height=600):
        image = QImage(width, height, QImage.Format.Format_ARGB32)
        image.fill(QColor("#2b579a"))
        clipboard = QMimeData()
        clipboard.setImageData(image)
        editor = PasteAwareTextEdit()
        editor.insertFromMimeData(clipboard)
        return editor

    def test_clipboard_screenshot_survives_html_serialization(self):
        editor = self._paste_screenshot()
        html = editor.toHtml()

        self.assertIn("data:image/png;base64,", html)
        self.assertIn(f'width="{_SCREENSHOT_MAX_WIDTH}"', html)

        reopened = PasteAwareTextEdit()
        reopened.setHtml(html)
        self.assertIn("data:image/png;base64,", reopened.toHtml())

    def test_serialized_screenshot_becomes_cid_inline_mime_part(self):
        editor = self._paste_screenshot(320, 180)
        msg = build_message(
            from_addr="sender@example.com",
            to_list=["recipient@example.com"],
            cc_list=[],
            bcc_list=[],
            subject="Screenshot",
            body_text="",
            body_html=editor.toHtml(),
        )

        rendered = msg.as_string()
        html_content = msg.get_body(preferencelist=("html",)).get_content()
        self.assertIn("multipart/related", rendered)
        self.assertIn("Content-ID:", rendered)
        self.assertIn('src="cid:', html_content)
        self.assertNotIn("data:image/png;base64,", rendered)
        self.assertTrue(any(
            part.get_content_maintype() == "image"
            and part.get_content_disposition() != "attachment"
            for part in msg.walk()
        ))

    def test_image_only_body_counts_as_draft_content(self):
        editor = self._paste_screenshot(320, 180)
        compose = SimpleNamespace(
            to_edit=QLineEdit(),
            cc_edit=QLineEdit(),
            bcc_edit=QLineEdit(),
            subject_edit=QLineEdit(),
            body_edit=editor,
        )
        compose._body_has_rich_content = MethodType(
            ComposeDialog._body_has_rich_content, compose
        )

        self.assertTrue(ComposeDialog._has_content(compose))

    def test_wps_table_html_wins_over_bitmap_preview(self):
        clipboard = QMimeData()
        clipboard.setHtml(
            '<table><tr><th>No</th><th>Type Unit</th></tr>'
            '<tr><td>1</td><td>TOYOTA AVANZA</td></tr></table>'
        )
        clipboard.setText("No\tType Unit\n1\tTOYOTA AVANZA")
        preview = QImage(800, 300, QImage.Format.Format_ARGB32)
        preview.fill(QColor("#ffffff"))
        clipboard.setImageData(preview)

        editor = PasteAwareTextEdit()
        editor.insertFromMimeData(clipboard)
        html = editor.toHtml()

        self.assertIn("<table", html.lower())
        self.assertIn("TOYOTA AVANZA", editor.toPlainText())
        self.assertNotIn("data:image/png;base64,", html)

        msg = build_message(
            from_addr="sender@example.com",
            to_list=["recipient@example.com"],
            cc_list=[],
            bcc_list=[],
            subject="Editable WPS table",
            body_text=editor.toPlainText(),
            body_html=html,
        )
        sent_html = msg.get_body(preferencelist=("html",)).get_content()
        self.assertIn("<table", sent_html.lower())
        self.assertFalse(any(
            part.get_content_maintype() == "image" for part in msg.walk()
        ))

    def test_wps_table_without_plain_text_still_wins_over_preview(self):
        clipboard = QMimeData()
        clipboard.setHtml("<table><tr><td>Editable cell</td></tr></table>")
        preview = QImage(400, 100, QImage.Format.Format_ARGB32)
        preview.fill(QColor("#ffffff"))
        clipboard.setImageData(preview)

        editor = PasteAwareTextEdit()
        editor.insertFromMimeData(clipboard)

        self.assertIn("Editable cell", editor.toPlainText())
        self.assertNotIn("data:image/png;base64,", editor.toHtml())


if __name__ == "__main__":
    unittest.main()
