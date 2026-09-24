import base64
import sys
from email.message import EmailMessage
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from core import attachment_store, database
from core.mail_parser import (
    _extract_tnef_attachments,
    _is_tnef_attachment,
    parse_message,
)


# Upstream tnefparse LGPL test fixture (tests/examples/minimal_attachment.tnef).
# It is a genuine TNEF container with one 16-byte embedded attachment.
_MINIMAL_TNEF = base64.b64decode(
    "eJ8+IgAAAgKQAAAAAAAAAAACD4AAABAAAAB0aGlzIGlzIGEgdGVzdAoAHwUAAA=="
)


def _message_with_tnef(payload: bytes, mime_type="application/ms-tnef"):
    msg = EmailMessage()
    msg["From"] = "sender@example.com"
    msg["To"] = "receiver@example.com"
    msg["Subject"] = "TNEF attachment"
    msg.set_content("See attachment")
    maintype, subtype = mime_type.split("/", 1)
    msg.add_attachment(
        payload,
        maintype=maintype,
        subtype=subtype,
        filename="winmail.dat",
    )
    return msg.as_bytes()


class TNEFAttachmentTests(unittest.TestCase):
    def test_real_tnef_container_is_replaced_by_its_attachment(self):
        _, attachments = parse_message(_message_with_tnef(_MINIMAL_TNEF))

        self.assertEqual(len(attachments), 1)
        self.assertEqual(attachments[0]["size"], 16)
        self.assertEqual(attachments[0]["data"], b"this is a test\n\x00")
        self.assertNotEqual(attachments[0]["filename"].lower(), "winmail.dat")

    def test_original_excel_name_and_mime_are_restored(self):
        excel_bytes = b"PK\x03\x04fake-xlsx"
        fake_attachment = SimpleNamespace(
            data=excel_bytes,
            name="short.xls",
            long_filename=lambda: r"C:\mail\Copy of Template Delivery.xlsx",
        )
        fake_tnef = SimpleNamespace(attachments=[fake_attachment])
        fake_module = SimpleNamespace(TNEF=lambda _: fake_tnef)

        with patch.dict(sys.modules, {"tnefparse": fake_module}):
            extracted = _extract_tnef_attachments(b"valid-enough-for-mock")

        self.assertEqual(extracted[0]["filename"], "Copy of Template Delivery.xlsx")
        self.assertEqual(
            extracted[0]["mime_type"],
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        self.assertEqual(extracted[0]["data"], excel_bytes)

    def test_invalid_tnef_is_kept_as_winmail_dat(self):
        _, attachments = parse_message(_message_with_tnef(b"not a TNEF stream"))

        self.assertEqual(len(attachments), 1)
        self.assertEqual(attachments[0]["filename"], "winmail.dat")
        self.assertEqual(attachments[0]["data"], b"not a TNEF stream")

    def test_tnef_is_detected_by_mime_or_common_filename(self):
        self.assertTrue(_is_tnef_attachment("anything.bin", "application/vnd.ms-tnef"))
        self.assertTrue(_is_tnef_attachment("WINMAIL.DAT", "application/octet-stream"))
        self.assertFalse(_is_tnef_attachment("report.xlsx", "application/octet-stream"))

    def test_opening_legacy_email_migrates_stored_winmail_dat(self):
        old_db_path = database.DB_PATH
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            database.DB_PATH = str(root / "mail.db")
            store_dir = root / "attachments"
            with patch("core.attachment_store._store_dir", return_value=store_dir):
                try:
                    database.init_db()
                    account_id = database.add_account({
                        "name": "Test", "email": "test@example.com",
                    })
                    email_id = database.insert_email(
                        account_id,
                        "inbox",
                        {
                            "uidl": "legacy-tnef",
                            "from": "sender@example.com",
                            "to": "test@example.com",
                            "subject": "Legacy TNEF",
                        },
                        [{
                            "filename": "winmail.dat",
                            "mime_type": "application/ms-tnef",
                            "data": _MINIMAL_TNEF,
                        }],
                    )
                    wrapper_hash = attachment_store.hash_bytes(_MINIMAL_TNEF)

                    opened = database.get_email(email_id)
                    full = database.get_attachments_for_email(email_id)

                    self.assertEqual(len(opened["attachments"]), 1)
                    self.assertNotEqual(
                        opened["attachments"][0]["filename"].lower(),
                        "winmail.dat",
                    )
                    self.assertEqual(full[0]["data"], b"this is a test\n\x00")
                    self.assertFalse(attachment_store.exists(wrapper_hash))
                finally:
                    database.DB_PATH = old_db_path


if __name__ == "__main__":
    unittest.main()
