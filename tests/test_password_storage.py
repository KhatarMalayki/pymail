import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PyQt5.QtWidgets import QApplication, QDialog, QMessageBox

from core import database, secure_storage
from ui.account_dialog import AccountDialog


class PasswordStorageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.old_db_path = database.DB_PATH
        self.old_secrets_file = secure_storage.SECRETS_FILE
        self.old_recovery_secrets_file = secure_storage.RECOVERY_SECRETS_FILE
        database.DB_PATH = str(root / "mail.db")
        secure_storage.SECRETS_FILE = root / "secrets.dat"
        secure_storage.RECOVERY_SECRETS_FILE = root / "recovery" / "secrets.dat"
        self.platform_patch = patch.object(
            secure_storage, "_is_windows", return_value=False
        )
        self.platform_patch.start()
        database.init_db()

    def tearDown(self):
        self.platform_patch.stop()
        database.DB_PATH = self.old_db_path
        secure_storage.SECRETS_FILE = self.old_secrets_file
        secure_storage.RECOVERY_SECRETS_FILE = self.old_recovery_secrets_file
        self.tmp.cleanup()

    @staticmethod
    def account_data(pop_password="pop-secret", smtp_password="smtp-secret"):
        return {
            "name": "Password Test",
            "email": "password@example.com",
            "pop3_host": "pop.example.com",
            "pop3_port": 995,
            "pop3_ssl": 1,
            "pop3_user": "password@example.com",
            "pop3_password": pop_password,
            "leave_on_server": 1,
            "smtp_host": "smtp.example.com",
            "smtp_port": 465,
            "smtp_security": "SSL",
            "smtp_user": "password@example.com",
            "smtp_password": smtp_password,
        }

    def test_add_and_reopen_returns_both_saved_passwords(self):
        account_id = database.add_account(self.account_data())

        reopened = database.get_account(account_id)
        self.assertEqual("pop-secret", reopened["pop3_password"])
        self.assertEqual("smtp-secret", reopened["smtp_password"])

    def test_empty_passwords_on_settings_edit_preserve_saved_values(self):
        account_id = database.add_account(self.account_data())
        edited = database.get_account(account_id)
        edited["name"] = "Renamed Account"
        edited["pop3_password"] = ""
        edited["smtp_password"] = ""

        database.update_account(account_id, edited)

        reopened = database.get_account(account_id)
        self.assertEqual("Renamed Account", reopened["name"])
        self.assertEqual("pop-secret", reopened["pop3_password"])
        self.assertEqual("smtp-secret", reopened["smtp_password"])

    def test_failed_atomic_replace_raises_and_keeps_old_passwords(self):
        account_id = database.add_account(self.account_data())
        edited = database.get_account(account_id)
        edited["pop3_password"] = "new-pop"
        edited["smtp_password"] = "new-smtp"

        with patch.object(
            secure_storage.os, "replace", side_effect=PermissionError("denied")
        ):
            with self.assertRaises(secure_storage.SecretStorageError):
                database.update_account(account_id, edited)

        reopened = database.get_account(account_id)
        self.assertEqual("pop-secret", reopened["pop3_password"])
        self.assertEqual("smtp-secret", reopened["smtp_password"])

    def test_failed_dpapi_preverification_never_replaces_old_store(self):
        account_id = database.add_account(self.account_data())
        original_bytes = secure_storage.SECRETS_FILE.read_bytes()
        edited = database.get_account(account_id)
        edited["pop3_password"] = "new-pop"

        with patch.object(secure_storage, "_is_windows", return_value=True), \
             patch.object(secure_storage, "_dpapi_encrypt", return_value=b"bad"), \
             patch.object(secure_storage, "_dpapi_decrypt", return_value=None):
            with self.assertRaises(secure_storage.SecretStorageError):
                database.update_account(account_id, edited)

        self.assertEqual(original_bytes, secure_storage.SECRETS_FILE.read_bytes())
        reopened = database.get_account(account_id)
        self.assertEqual("pop-secret", reopened["pop3_password"])
        self.assertEqual("smtp-secret", reopened["smtp_password"])

    def test_corrupt_store_is_not_overwritten(self):
        secure_storage.SECRETS_FILE.write_text("{broken", encoding="utf-8")

        with self.assertRaises(secure_storage.SecretStorageError):
            secure_storage.set_secret("pop3_pass_1", "replacement")

        self.assertEqual(
            "{broken",
            secure_storage.SECRETS_FILE.read_text(encoding="utf-8"),
        )

    def test_corrupt_store_can_be_backed_up_then_reset_explicitly(self):
        secure_storage.SECRETS_FILE.write_text("{broken", encoding="utf-8")

        recovery = secure_storage.backup_and_reset_store()

        self.assertIsNotNone(recovery.preserved_path)
        self.assertTrue(recovery.moved_to_backup)
        self.assertEqual(
            "{broken", recovery.preserved_path.read_text(encoding="utf-8")
        )
        self.assertEqual({}, secure_storage._read_store(strict=True))

    def test_locked_store_falls_back_without_touching_old_file(self):
        locked_store = secure_storage.SECRETS_FILE
        locked_store.write_text("{broken", encoding="utf-8")
        real_replace = secure_storage.os.replace

        def deny_only_old_store_rename(source, target):
            if (Path(source) == locked_store
                    and str(target).endswith(".bak")):
                raise PermissionError("locked by ACL")
            return real_replace(source, target)

        with patch.object(
            secure_storage.os,
            "replace",
            side_effect=deny_only_old_store_rename,
        ):
            recovery = secure_storage.backup_and_reset_store()

        self.assertFalse(recovery.moved_to_backup)
        self.assertEqual(locked_store, recovery.preserved_path)
        self.assertEqual("{broken", locked_store.read_text(encoding="utf-8"))
        self.assertEqual(
            secure_storage.RECOVERY_SECRETS_FILE,
            recovery.active_path,
        )
        self.assertEqual(recovery.active_path, secure_storage.SECRETS_FILE)
        self.assertEqual({}, secure_storage._read_store(strict=True))

    def test_failed_legacy_migration_keeps_plaintext_database_copy(self):
        with database.get_conn() as conn:
            cur = conn.execute(
                "INSERT INTO accounts(name, email, pop3_password, smtp_password) "
                "VALUES (?, ?, ?, ?)",
                ("Legacy", "legacy@example.com", "legacy-pop", "legacy-smtp"),
            )
            account_id = cur.lastrowid

        with patch.object(
            secure_storage,
            "set_secrets",
            side_effect=secure_storage.SecretStorageError("cannot write"),
        ):
            with database.get_conn() as conn:
                database._migrate_passwords(conn)

        with database.get_conn() as conn:
            row = conn.execute(
                "SELECT pop3_password, smtp_password FROM accounts WHERE id=?",
                (account_id,),
            ).fetchone()
        self.assertEqual("legacy-pop", row["pop3_password"])
        self.assertEqual("legacy-smtp", row["smtp_password"])

    def test_dialog_stays_open_and_reports_secure_save_failure(self):
        dialog = AccountDialog()
        dialog.email_edit.setText("password@example.com")
        dialog.pop_host.setText("pop.example.com")
        dialog.pop_pass.setText("pop-secret")
        dialog.smtp_host.setText("smtp.example.com")

        error = secure_storage.SecretStorageError("simulated write failure")
        with patch.object(database, "add_account", side_effect=error), \
             patch.object(QMessageBox, "critical") as critical:
            dialog._save()

        self.assertEqual(QDialog.Rejected, dialog.result())
        critical.assert_called_once()
        self.assertIn("Password was not saved", critical.call_args.args[1])
        dialog.close()

    def test_dialog_can_confirm_store_reset_and_retry_save(self):
        dialog = AccountDialog()
        dialog.email_edit.setText("password@example.com")
        dialog.pop_host.setText("pop.example.com")
        dialog.pop_pass.setText("pop-secret")
        dialog.smtp_host.setText("smtp.example.com")
        secure_storage.SECRETS_FILE.write_text("{broken", encoding="utf-8")

        recoverable = secure_storage.SecretStorageError(
            "damaged", can_reset_store=True
        )
        with patch.object(
            database, "add_account", side_effect=[recoverable, 1]
        ) as add_account, patch.object(
            QMessageBox, "question", return_value=QMessageBox.Yes
        ), patch.object(QMessageBox, "information"):
            dialog._save()

        self.assertEqual(2, add_account.call_count)
        self.assertEqual(QDialog.Accepted, dialog.result())
        backups = list(
            secure_storage.SECRETS_FILE.parent.glob(
                "secrets.dat.unreadable-*.bak"
            )
        )
        self.assertEqual(1, len(backups))
        self.assertEqual("{broken", backups[0].read_text(encoding="utf-8"))
        dialog.close()


if __name__ == "__main__":
    unittest.main()
