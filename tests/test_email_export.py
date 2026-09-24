import tempfile
import unittest
from unittest.mock import patch

from core.email_export import export_emails, get_last_export_dir


class EmailExportTests(unittest.TestCase):
    def test_exports_original_mime_without_overwriting(self):
        raw = b"From: Abdul Wahab <abdul@example.com>\r\nSubject: Invoice\r\n\r\nEvidence"
        row = {
            "id": 7,
            "sender": "Abdul Wahab <abdul@example.com>",
            "date_received": "2026-09-23T10:00:00+07:00",
            "raw_message": raw,
        }
        with tempfile.TemporaryDirectory() as folder, patch(
            "core.email_export.database.get_email", return_value=row
        ):
            first = export_emails([7], folder)[0]
            second = export_emails([7], folder)[0]
            self.assertEqual(first.name, "09-2026-Abdul Wahab.eml")
            self.assertEqual(second.name, "09-2026-Abdul Wahab-2.eml")
            self.assertEqual(first.read_bytes(), raw)
            self.assertEqual(get_last_export_dir(), folder)

    def test_exports_single_email_with_date_sent_fallback(self):
        raw = b"From: Ade Tri Wahyudi <ade@example.com>\r\nSubject: Invoice\r\n\r\nEvidence"
        row = {
            "id": 8,
            "sender": "Ade Tri Wahyudi <ade@example.com>",
            "date_sent": "2026-09-24T08:00:00+07:00",
            "raw_message": raw,
        }
        with tempfile.TemporaryDirectory() as folder, patch(
            "core.email_export.database.get_email", return_value=row
        ):
            saved = export_emails([8], folder)[0]
            self.assertEqual(saved.name, "09-2026-Ade Tri Wahyudi.eml")
            self.assertEqual(saved.read_bytes(), raw)


if __name__ == "__main__":
    unittest.main()
