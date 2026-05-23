"""
Admin License Manager dialog — talks to the Cloudflare Worker.

Lists every registered user (the Worker is the source of truth — auto-
registration when users first run PyMail), shows status, and lets the
admin extend / revoke / restore with a click.

The admin token (set as Worker secret) is stored in core/config.py and
prompted for once on first use.
"""
from datetime import datetime, timezone
from PyQt5.QtCore import Qt, QThread, pyqtSignal
from PyQt5.QtGui import QColor, QFont
from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QMessageBox,
    QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView,
    QInputDialog, QMenu, QLineEdit,
)

from core import license_client, config


# ---------- Background workers ----------

class _ListWorker(QThread):
    loaded = pyqtSignal(list)
    failed = pyqtSignal(str)

    def __init__(self, token: str):
        super().__init__()
        self.token = token

    def run(self):
        try:
            users = license_client.admin_list_users(self.token)
            self.loaded.emit(users)
        except Exception as e:
            self.failed.emit(str(e))


class _ExtendWorker(QThread):
    done = pyqtSignal(dict)
    failed = pyqtSignal(str)

    def __init__(self, token: str, license_id: str, days: int):
        super().__init__()
        self.token = token
        self.license_id = license_id
        self.days = days

    def run(self):
        try:
            self.done.emit(license_client.admin_extend(
                self.token, self.license_id, self.days
            ))
        except Exception as e:
            self.failed.emit(str(e))


class _SimpleWorker(QThread):
    done = pyqtSignal()
    failed = pyqtSignal(str)

    def __init__(self, fn):
        super().__init__()
        self._fn = fn

    def run(self):
        try:
            self._fn()
            self.done.emit()
        except Exception as e:
            self.failed.emit(str(e))


# ---------- Main dialog ----------

class LicenseManagerDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("PyMail License Manager (Admin)")
        self.resize(1080, 580)
        self._users: list[dict] = []
        self._token: str = config.get("admin_token", "") or ""
        self._build_ui()
        if not self._token:
            # Prompt for the admin token on first use, then save it
            self._prompt_for_token()
        else:
            self._load()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(10)

        header = QLabel("<b>License Manager</b>")
        header.setStyleSheet("font-size:12pt;")
        header.setTextFormat(Qt.RichText)
        layout.addWidget(header)

        info = QLabel(
            "All PyMail installations that have auto-registered with your "
            "Cloudflare Worker. Right-click a row to extend, revoke, or restore."
        )
        info.setStyleSheet("color:#605e5c;")
        info.setWordWrap(True)
        layout.addWidget(info)

        # Action buttons row
        btn_row = QHBoxLayout()
        self.refresh_btn = QPushButton("⟳  Refresh")
        self.refresh_btn.clicked.connect(self._load)
        btn_row.addWidget(self.refresh_btn)

        btn_row.addStretch(1)
        self.token_btn = QPushButton("🔑  Change admin token")
        self.token_btn.clicked.connect(self._prompt_for_token)
        btn_row.addWidget(self.token_btn)
        layout.addLayout(btn_row)

        # Table
        self.table = QTableWidget(0, 8)
        self.table.setHorizontalHeaderLabels([
            "License ID", "Status", "Email", "Name",
            "Hostname", "Issued", "Expires", "Last seen",
        ])
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._show_menu)
        self.table.itemDoubleClicked.connect(
            lambda _: self._copy_selected_id()
        )
        h = self.table.horizontalHeader()
        h.setSectionResizeMode(0, QHeaderView.Interactive)
        h.setSectionResizeMode(1, QHeaderView.Interactive)
        h.setSectionResizeMode(2, QHeaderView.Stretch)
        h.setSectionResizeMode(3, QHeaderView.Stretch)
        h.setSectionResizeMode(4, QHeaderView.Interactive)
        h.setSectionResizeMode(5, QHeaderView.Interactive)
        h.setSectionResizeMode(6, QHeaderView.Interactive)
        h.setSectionResizeMode(7, QHeaderView.Interactive)
        self.table.setColumnWidth(0, 110)
        self.table.setColumnWidth(1, 80)
        self.table.setColumnWidth(4, 130)
        self.table.setColumnWidth(5, 90)
        self.table.setColumnWidth(6, 90)
        self.table.setColumnWidth(7, 100)
        layout.addWidget(self.table, 1)

        # Status bar
        self.status_label = QLabel("Ready")
        self.status_label.setStyleSheet("color:#605e5c;")
        layout.addWidget(self.status_label)

        bottom = QHBoxLayout()
        bottom.addStretch(1)
        close = QPushButton("Close")
        close.clicked.connect(self.accept)
        bottom.addWidget(close)
        layout.addLayout(bottom)

    # ----- Token handling -----
    def _prompt_for_token(self):
        token, ok = QInputDialog.getText(
            self, "Admin token",
            "Paste the admin token (set as Worker secret).\n"
            "It's stored locally so you only need to enter it once.",
            QLineEdit.Password,
            self._token,
        )
        if ok and token.strip():
            self._token = token.strip()
            config.set_value("admin_token", self._token)
            self.status_label.setText("Token saved. Loading users...")
            self._load()
        elif not self._token:
            QMessageBox.warning(
                self, "Token required",
                "Without an admin token this dialog cannot reach the Worker.",
            )
            self.reject()

    # ----- Data loading -----
    def _load(self):
        if not self._token:
            return
        self.status_label.setText("Fetching users from Worker...")
        self.refresh_btn.setEnabled(False)
        self._loader = _ListWorker(self._token)
        self._loader.loaded.connect(self._on_users_loaded)
        self._loader.failed.connect(self._on_load_failed)
        self._loader.start()

    def _on_users_loaded(self, users: list):
        self._users = users
        self._render_table()
        revoked = sum(1 for u in users if u.get("status") == "revoked")
        expired = sum(1 for u in users if u.get("status") == "expired")
        active = sum(1 for u in users if u.get("status") == "active")
        self.status_label.setText(
            f"{len(users)} total · {active} active · "
            f"{revoked} revoked · {expired} expired"
        )
        self.refresh_btn.setEnabled(True)

        # Auto-sync: if local licenses_issued.json has entries the Worker
        # doesn't know about, offer to push them up. This is how the
        # admin's pre-Worker / offline-issued licenses get into the
        # registry.
        self._maybe_offer_sync(users)

    def _maybe_offer_sync(self, server_users: list):
        """Detect locally-issued licenses missing from the Worker and
        offer to import them in bulk."""
        import json
        from pathlib import Path
        log_path = (Path(__file__).parent.parent / "admin"
                    / "licenses_issued.json")
        if not log_path.is_file():
            return
        try:
            local_payloads = json.loads(log_path.read_text(encoding="utf-8"))
        except Exception:
            return

        server_ids = {u.get("license_id") for u in server_users}
        missing = [
            p for p in local_payloads
            if p.get("license_id") and p["license_id"] not in server_ids
        ]
        if not missing:
            return

        ret = QMessageBox.question(
            self,
            "Sync local licenses?",
            f"Found {len(missing)} license(s) issued locally that aren't "
            f"in the Worker registry:\n\n"
            + "\n".join(
                f"  • {p.get('name') or '(no name)'} <{p.get('email') or ''}>  "
                f"({p['license_id']})"
                for p in missing[:10]
            )
            + ("\n  ..." if len(missing) > 10 else "")
            + "\n\nImport them now so they appear in the License Manager?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.Yes,
        )
        if ret != QMessageBox.Yes:
            return
        self._do_bulk_import(missing)

    def _do_bulk_import(self, payloads: list):
        from core import license_client
        added = 0
        failed = 0
        for p in payloads:
            try:
                license_client.admin_import(
                    self._token,
                    {"payload": p, "signature": ""},
                    machine_id=p.get("machine_id_hash") or "",
                )
                added += 1
            except Exception:
                failed += 1
        msg = f"Imported {added} license(s)."
        if failed:
            msg += f" {failed} failed."
        self.status_label.setText(msg)
        self._load()  # refresh table

    def _on_load_failed(self, err: str):
        self.status_label.setText(f"Failed: {err}")
        self.refresh_btn.setEnabled(True)
        if "403" in err or "Forbidden" in err:
            ret = QMessageBox.warning(
                self, "Authorization failed",
                "The admin token was rejected by the Worker. "
                "Update the token?",
                QMessageBox.Yes | QMessageBox.Cancel,
                QMessageBox.Yes,
            )
            if ret == QMessageBox.Yes:
                self._prompt_for_token()

    def _render_table(self):
        self.table.setRowCount(0)
        # Sort: revoked/expired at the bottom, active at top by last_seen
        def _sort_key(u):
            order = {"active": 0, "expired": 1, "revoked": 2}.get(
                u.get("status", "active"), 3
            )
            return (order, u.get("last_seen") or "")
        sorted_users = sorted(self._users, key=_sort_key, reverse=False)
        # Within each group, latest last_seen first
        sorted_users.sort(
            key=lambda u: (
                {"active": 0, "expired": 1, "revoked": 2}.get(
                    u.get("status", "active"), 3
                ),
                -1 * (
                    int(_iso_to_ts(u.get("last_seen") or ""))
                ),
            )
        )

        for u in sorted_users:
            row = self.table.rowCount()
            self.table.insertRow(row)
            status = u.get("status", "active")
            color = {
                "active": "#107c10",
                "expired": "#a4262c",
                "revoked": "#a4262c",
            }.get(status, "#605e5c")

            cells = [
                u.get("license_id") or "",
                status.upper() if status != "active" else "active",
                u.get("email") or "",
                u.get("name") or "",
                u.get("hostname") or "",
                (u.get("issued_at") or "")[:10],
                (u.get("expires_at") or "(perpetual)")[:10],
                (u.get("last_seen") or "")[:16].replace("T", " "),
            ]
            for c, value in enumerate(cells):
                item = QTableWidgetItem(value)
                if c == 0:
                    item.setFont(QFont("Consolas"))
                if c == 1:
                    item.setForeground(QColor(color))
                    f = item.font()
                    f.setBold(True)
                    item.setFont(f)
                self.table.setItem(row, c, item)

    # ----- Actions -----
    def _show_menu(self, pos):
        idx = self.table.indexAt(pos)
        if not idx.isValid():
            return
        row = idx.row()
        license_id = self.table.item(row, 0).text()
        status = self.table.item(row, 1).text().lower()
        name = self.table.item(row, 3).text() or self.table.item(row, 2).text()
        is_revoked = (status == "revoked")

        menu = QMenu(self)
        menu.addAction(
            "📋  Copy license ID", lambda: self._copy_text(license_id)
        )
        menu.addSeparator()

        ext_menu = menu.addMenu("⏱  Extend trial")
        for label, days in [
            ("+30 days", 30),
            ("+90 days", 90),
            ("+1 year", 365),
            ("Make perpetual (no expiry)", 0),
            ("Custom...", -1),
        ]:
            ext_menu.addAction(
                label,
                lambda d=days, lid=license_id, n=name:
                    self._extend(lid, n, d),
            )

        if is_revoked:
            menu.addAction(
                "✓  Restore license",
                lambda: self._restore(license_id, name),
            )
        else:
            menu.addAction(
                "🚫  Revoke license...",
                lambda: self._revoke(license_id, name),
            )
        menu.addSeparator()
        menu.addAction(
            "🗑  Delete (remove from registry permanently)",
            lambda: self._delete_user(license_id, name),
        )
        menu.exec_(self.table.viewport().mapToGlobal(pos))

    def _copy_selected_id(self):
        rows = self.table.selectionModel().selectedRows()
        if rows:
            self._copy_text(self.table.item(rows[0].row(), 0).text())

    def _copy_text(self, text: str):
        from PyQt5.QtWidgets import QApplication
        QApplication.clipboard().setText(text)
        self.status_label.setText(f"Copied: {text}")

    def _extend(self, license_id: str, name: str, days: int):
        if days == -1:
            days, ok = QInputDialog.getInt(
                self, "Custom duration",
                f"Days from today (0 = perpetual):",
                value=180, min=0, max=3650,
            )
            if not ok:
                return
        confirm_msg = (
            f"Make {license_id} ({name}) PERPETUAL (never expires)?"
            if days == 0 else
            f"Extend {license_id} ({name}) by {days} days from today?"
        )
        if QMessageBox.question(
            self, "Confirm extend", confirm_msg,
            QMessageBox.Yes | QMessageBox.Cancel, QMessageBox.Yes,
        ) != QMessageBox.Yes:
            return
        self.status_label.setText("Updating...")
        worker = _ExtendWorker(self._token, license_id, days)
        worker.done.connect(lambda _: self._on_action_done(
            f"Updated {license_id}"
        ))
        worker.failed.connect(self._on_action_failed)
        worker.start()
        self._action_worker = worker  # keep ref alive

    def _revoke(self, license_id: str, name: str):
        reason, ok = QInputDialog.getText(
            self, "Revoke license",
            f"Reason for revoking {license_id} ({name})?",
            text="No longer authorized",
        )
        if not ok:
            return
        self.status_label.setText("Revoking...")
        worker = _SimpleWorker(
            lambda: license_client.admin_revoke(
                self._token, license_id, reason
            )
        )
        worker.done.connect(lambda: self._on_action_done(
            f"Revoked {license_id}"
        ))
        worker.failed.connect(self._on_action_failed)
        worker.start()
        self._action_worker = worker

    def _restore(self, license_id: str, name: str):
        if QMessageBox.question(
            self, "Restore license?",
            f"Restore {license_id} ({name})?",
            QMessageBox.Yes | QMessageBox.Cancel, QMessageBox.Yes,
        ) != QMessageBox.Yes:
            return
        self.status_label.setText("Restoring...")
        worker = _SimpleWorker(
            lambda: license_client.admin_restore(
                self._token, license_id
            )
        )
        worker.done.connect(lambda: self._on_action_done(
            f"Restored {license_id}"
        ))
        worker.failed.connect(self._on_action_failed)
        worker.start()
        self._action_worker = worker

    def _delete_user(self, license_id: str, name: str):
        if QMessageBox.warning(
            self, "Permanently delete?",
            f"Permanently REMOVE {license_id} ({name}) from the registry?\n\n"
            f"This is different from Revoke — the row will be gone entirely. "
            f"Use this only for test accounts or confirmed-departed users "
            f"you don't want to track anymore.",
            QMessageBox.Yes | QMessageBox.Cancel, QMessageBox.Cancel,
        ) != QMessageBox.Yes:
            return
        self.status_label.setText("Deleting...")
        worker = _SimpleWorker(
            lambda: license_client.admin_delete(
                self._token, license_id
            )
        )
        worker.done.connect(lambda: self._on_action_done(
            f"Deleted {license_id}"
        ))
        worker.failed.connect(self._on_action_failed)
        worker.start()
        self._action_worker = worker

    def _on_action_done(self, msg: str):
        self.status_label.setText(msg + ". Refreshing...")
        self._load()

    def _on_action_failed(self, err: str):
        self.status_label.setText(f"Action failed: {err}")
        QMessageBox.critical(self, "Action failed", err)


def _iso_to_ts(iso: str) -> float:
    if not iso:
        return 0
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        return dt.timestamp()
    except Exception:
        return 0
