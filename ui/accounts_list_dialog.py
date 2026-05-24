"""
Accounts list dialog — shown from the toolbar "Accounts" button.

Lists every configured email account and lets the user Add new, Edit,
or Delete. Supports multiple accounts (each with its own POP3/SMTP/IMAP).
"""
from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QListWidget, QListWidgetItem,
    QPushButton, QLabel, QMessageBox, QDialogButtonBox,
)
from PyQt5.QtCore import Qt
from core import database
from .account_dialog import AccountDialog


class AccountsListDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Email Accounts")
        self.resize(560, 380)
        self._build_ui()
        self._reload()

    def _build_ui(self):
        layout = QVBoxLayout(self)

        intro = QLabel(
            "Manage email accounts in RunLab Mail. You can configure as "
            "many accounts as you need — each with its own POP3, SMTP, "
            "and (optional) IMAP Junk fetcher."
        )
        intro.setStyleSheet("color:#605e5c;")
        intro.setWordWrap(True)
        layout.addWidget(intro)

        body = QHBoxLayout()
        layout.addLayout(body, 1)

        self.list = QListWidget()
        self.list.setStyleSheet(
            "QListWidget::item { padding: 6px 8px; }"
            "QListWidget::item:selected { background: #cce4f7; color:#000; }"
        )
        self.list.itemDoubleClicked.connect(lambda *_: self._edit())
        body.addWidget(self.list, 1)

        btn_col = QVBoxLayout()
        btn_col.setSpacing(6)
        body.addLayout(btn_col)

        self.btn_add = QPushButton("Add account...")
        self.btn_add.clicked.connect(self._add)
        btn_col.addWidget(self.btn_add)

        self.btn_edit = QPushButton("Edit...")
        self.btn_edit.clicked.connect(self._edit)
        btn_col.addWidget(self.btn_edit)

        self.btn_delete = QPushButton("Delete")
        self.btn_delete.clicked.connect(self._delete)
        btn_col.addWidget(self.btn_delete)

        btn_col.addStretch(1)

        # Close
        bbox = QDialogButtonBox(QDialogButtonBox.Close)
        bbox.rejected.connect(self.reject)
        bbox.accepted.connect(self.accept)
        layout.addWidget(bbox)

    def _reload(self):
        self.list.clear()
        accounts = database.list_accounts()
        if not accounts:
            empty = QListWidgetItem("No accounts configured. Click 'Add account…' to start.")
            empty.setFlags(Qt.NoItemFlags)
            empty.setForeground(Qt.gray)
            self.list.addItem(empty)
            self.btn_edit.setEnabled(False)
            self.btn_delete.setEnabled(False)
            return
        for a in accounts:
            label_lines = [
                f"{a.get('name') or ''}  <{a.get('email') or ''}>",
                f"  POP3: {a.get('pop3_host') or '-'}:{a.get('pop3_port') or '-'}"
                + ("  ·  Leave-on-server" if a.get("leave_on_server") else "  ·  DELETE-on-fetch"),
                f"  SMTP: {a.get('smtp_host') or '-'}:{a.get('smtp_port') or '-'}",
            ]
            if a.get("imap_enabled"):
                label_lines.append(
                    f"  IMAP Junk: {a.get('imap_host') or '-'}"
                    + (f"  ·  folder='{a.get('junk_folder_name')}'"
                       if a.get("junk_folder_name") else "  ·  auto-detect")
                )
            it = QListWidgetItem("\n".join(label_lines))
            it.setData(Qt.UserRole, a["id"])
            self.list.addItem(it)
        self.btn_edit.setEnabled(True)
        self.btn_delete.setEnabled(True)
        # Select first by default
        self.list.setCurrentRow(0)

    def _selected_id(self):
        it = self.list.currentItem()
        if not it:
            return None
        data = it.data(Qt.UserRole)
        return data if isinstance(data, int) else None

    # ---- actions ----
    def _add(self):
        dlg = AccountDialog(self)
        if dlg.exec_():
            self._reload()

    def _edit(self):
        acc_id = self._selected_id()
        if acc_id is None:
            QMessageBox.information(self, "No selection", "Select an account first.")
            return
        acc = database.get_account(acc_id)
        if not acc:
            return
        dlg = AccountDialog(self, account=acc)
        if dlg.exec_():
            self._reload()

    def _delete(self):
        acc_id = self._selected_id()
        if acc_id is None:
            QMessageBox.information(self, "No selection", "Select an account first.")
            return
        acc = database.get_account(acc_id)
        if not acc:
            return
        if QMessageBox.question(
            self, "Delete account",
            f"Delete '{acc.get('email')}' and ALL its locally-stored emails?\n\n"
            "Messages still on the mail server are NOT touched.\n\n"
            "This cannot be undone.",
            QMessageBox.Yes | QMessageBox.Cancel, QMessageBox.Cancel,
        ) != QMessageBox.Yes:
            return
        database.delete_account(acc_id)
        self._reload()
