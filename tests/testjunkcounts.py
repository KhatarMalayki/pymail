import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QApplication, QTreeWidget, QTreeWidgetItem
from core import database
from ui.main_window import MainWindow, _folder_label


class JunkUnreadCountTests(unittest.TestCase):
    def setUp(self):
        self._old_path = database.DB_PATH
        self._tmp = tempfile.TemporaryDirectory()
        database.DB_PATH = str(Path(self._tmp.name) / "mail.db")
        database.init_db()
        self.account_id = database.add_account({
            "name": "Junk Test", "email": "junk@example.com",
        })

    def tearDown(self):
        database.DB_PATH = self._old_path
        self._tmp.cleanup()

    def _insert_junk(self, uidl: str) -> int:
        return database.insert_email(
            self.account_id,
            "spam",
            {
                "uidl": uidl,
                "from": "sender@example.com",
                "to": "junk@example.com",
                "subject": uidl,
            },
            [],
        )

    def test_new_junk_is_counted_as_unread(self):
        self._insert_junk("junk-1")
        self._insert_junk("junk-2")
        self.assertEqual(
            database.folder_counts(self.account_id)["spam"],
            {"total": 2, "unread": 2},
        )

    def test_read_junk_is_removed_from_badge_count(self):
        first = self._insert_junk("junk-1")
        self._insert_junk("junk-2")
        database.mark_read(first, True)
        counts = database.folder_counts(self.account_id)["spam"]
        self.assertEqual(counts, {"total": 2, "unread": 1})
        self.assertEqual(_folder_label("spam", "Junk", "X", counts), "X  Junk (1)")

    def test_junk_badge_disappears_when_everything_is_read(self):
        email_id = self._insert_junk("junk-1")
        database.mark_read(email_id, True)
        counts = database.folder_counts(self.account_id)["spam"]
        self.assertEqual(_folder_label("spam", "Junk", "X", counts), "X  Junk")

    def test_outbox_and_inbox_badge_rules_are_unchanged(self):
        self.assertEqual(
            _folder_label("inbox", "Inbox", "I", {"total": 5, "unread": 2}),
            "I  Inbox (2)",
        )
        self.assertEqual(
            _folder_label("outbox", "Outbox", "O", {"total": 3, "unread": 0}),
            "O  Outbox (3)",
        )

    def test_incremental_refresh_updates_requested_account_junk_badge(self):
        # Model a worker completing after the user switched to another
        # account. The explicit account_id must still update the right row.
        app = QApplication.instance() or QApplication([])
        tree = QTreeWidget()
        top = QTreeWidgetItem(["Junk Test"])
        top.setData(0, Qt.UserRole, ("account", self.account_id))
        junk = QTreeWidgetItem(["X  Junk"])
        junk.setData(0, Qt.UserRole, ("folder", self.account_id, "spam"))
        top.addChild(junk)
        tree.addTopLevelItem(top)

        class WindowStub:
            FOLDERS = MainWindow.FOLDERS
            current_account_id = self.account_id + 999

        stub = WindowStub()
        stub.tree = tree
        with patch.object(
            database,
            "folder_counts",
            return_value={"spam": {"total": 2, "unread": 2}},
        ):
            MainWindow._update_folder_counts(stub, self.account_id)

        self.assertTrue(junk.text(0).endswith("Junk (2)"))
        self.assertEqual(junk.foreground(0).color(), Qt.red)
        tree.deleteLater()
        # Keep a reference alive until after Qt objects have been cleaned up.
        self.assertIsNotNone(app)


if __name__ == "__main__":
    unittest.main()
