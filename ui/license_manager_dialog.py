"""
Admin License Manager dialog — talks to the Cloudflare Worker.

Lists every registered user (the Worker is the source of truth — auto-
registration when users first run RunLab Mail), shows status, and lets the
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
    QInputDialog, QMenu, QLineEdit, QComboBox,
)

from core import license_client, config, secure_storage


def _iso_to_local(ts: str) -> str:
    """Convert ISO timestamp (UTC) to local time string."""
    if not ts:
        return ""
    try:
        # Parse ISO format (may have Z or +00:00)
        ts = ts.replace("Z", "+00:00")
        dt = datetime.fromisoformat(ts)
        # If no tzinfo, assume UTC
        if dt.tzinfo is None:
            from datetime import timezone
            dt = dt.replace(tzinfo=timezone.utc)
        # Convert to local
        local_dt = dt.astimezone()
        return local_dt.strftime("%Y-%m-%d %H:%M")
    except Exception:
        return ts[:16].replace("T", " ")  # fallback

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


class _ResultWorker(QThread):
    """Like _SimpleWorker but carries the function's return value (dict) back
    to the UI thread — used by generate/re-bind which return a license_key."""
    done = pyqtSignal(dict)
    failed = pyqtSignal(str)

    def __init__(self, fn):
        super().__init__()
        self._fn = fn

    def run(self):
        try:
            result = self._fn()
            self.done.emit(result if isinstance(result, dict) else {})
        except Exception as e:
            self.failed.emit(str(e))


class _BulkWorker(QThread):
    """Apply the same admin action to many license_ids sequentially.

    `fn(license_id)` is called once per id. We keep going even if some fail,
    then report how many succeeded/failed so one bad row doesn't abort the
    whole batch. progress is emitted as (done_count, total) so the UI can
    show "Processing 3/12...".
    """
    progress = pyqtSignal(int, int)        # done, total
    finished_bulk = pyqtSignal(int, list)  # ok_count, [(license_id, error), ...]

    def __init__(self, fn, license_ids: list):
        super().__init__()
        self._fn = fn
        self._ids = list(license_ids)

    def run(self):
        ok = 0
        errors = []
        total = len(self._ids)
        for i, lid in enumerate(self._ids, start=1):
            try:
                self._fn(lid)
                ok += 1
            except Exception as e:
                errors.append((lid, str(e)))
            self.progress.emit(i, total)
        self.finished_bulk.emit(ok, errors)



# ---------- Main dialog ----------


class LicenseManagerDialog(QDialog):

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("RunLab Mail License Manager (Admin)")
        self.resize(1080, 580)
        # Allow maximize / fullscreen so the admin can see all columns
        # without resizing the window manually.
        from PyQt5.QtCore import Qt as _Qt
        self.setWindowFlags(
            self.windowFlags()
            | _Qt.WindowMaximizeButtonHint
            | _Qt.WindowMinimizeButtonHint
        )
        self._users: list[dict] = []
        # Migrate any plain-text token from older versions (one-time)
        secure_storage.migrate_from_config("admin_token")
        self._token: str = secure_storage.get_secret("admin_token", "")
        self._build_ui()
        if not self._token:
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
            "All RunLab Mail installations that have auto-registered with your "
            "Cloudflare Worker. Right-click a row to extend, revoke, or restore. "
            "Ctrl-click or Shift-click to select several rows, then right-click "
            "for bulk actions."
        )
        info.setStyleSheet("color:#605e5c;")
        info.setWordWrap(True)
        layout.addWidget(info)

        # Action buttons row
        btn_row = QHBoxLayout()
        self.refresh_btn = QPushButton("⟳  Refresh")
        self.refresh_btn.clicked.connect(self._load)
        btn_row.addWidget(self.refresh_btn)

        self.push_ver_btn = QPushButton("🚀  Push version to all users")
        self.push_ver_btn.setToolTip(
            "Set the allowed update version for every registered user.\n"
            "Users will only receive the update once you push it here."
        )
        self.push_ver_btn.clicked.connect(self._push_version)
        btn_row.addWidget(self.push_ver_btn)

        self.generate_btn = QPushButton("➕  Generate license")
        self.generate_btn.setToolTip(
            "Create a brand-new signed license key for a user.\n"
            "Choose floating (any device) or bound to a specific device."
        )
        self.generate_btn.clicked.connect(self._generate_license)
        btn_row.addWidget(self.generate_btn)

        btn_row.addStretch(1)
        
        # Search box
        search_label = QLabel("🔍")
        search_label.setStyleSheet("font-size: 14px;")
        btn_row.addWidget(search_label)
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("Search email, name, hostname, license...")
        self.search_edit.setMinimumWidth(250)
        self.search_edit.setMaximumWidth(350)
        self.search_edit.textChanged.connect(self._on_search_changed)
        btn_row.addWidget(self.search_edit)
        
        self.token_btn = QPushButton("🔑  Change admin token")
        self.token_btn.clicked.connect(self._prompt_for_token)
        btn_row.addWidget(self.token_btn)
        layout.addLayout(btn_row)

        # Table
        self.table = QTableWidget(0, 13)
        self.table.setHorizontalHeaderLabels([
            "License ID", "Status", "Email", "Name",
            "Hostname", "Version", "Allowed Ver.", "Machine ID",
            "Public IP", "Local IP",
            "Issued", "Expires", "Last seen",
        ])

        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        # ExtendedSelection lets the admin Ctrl-click / Shift-click multiple
        # rows, then right-click for a bulk Extend/Revoke/Restore/Delete menu.
        self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._show_menu)
        self.table.itemDoubleClicked.connect(
            lambda _: self._copy_selected_id()
        )
        h = self.table.horizontalHeader()
        # Auto-size most columns to their content so the admin never has to
        # drag column borders. Email & Name stretch to fill leftover space.
        from PyQt5.QtWidgets import QHeaderView as _QHV
        h.setSectionResizeMode(0, _QHV.ResizeToContents)   # License ID
        h.setSectionResizeMode(1, _QHV.ResizeToContents)   # Status
        h.setSectionResizeMode(2, _QHV.Stretch)            # Email (fill)
        h.setSectionResizeMode(3, _QHV.Stretch)            # Name (fill)
        h.setSectionResizeMode(4, _QHV.ResizeToContents)   # Hostname
        h.setSectionResizeMode(5, _QHV.ResizeToContents)   # Version
        h.setSectionResizeMode(6, _QHV.ResizeToContents)   # Allowed Ver.
        h.setSectionResizeMode(7, _QHV.ResizeToContents)   # Machine ID
        h.setSectionResizeMode(8, _QHV.ResizeToContents)   # Public IP
        h.setSectionResizeMode(9, _QHV.ResizeToContents)   # Local IP
        h.setSectionResizeMode(10, _QHV.ResizeToContents)  # Issued
        h.setSectionResizeMode(11, _QHV.ResizeToContents)  # Expires
        h.setSectionResizeMode(12, _QHV.ResizeToContents)  # Last seen
        # Let the user still drag to override if they want.
        h.setStretchLastSection(False)
        # Click a column header to sort by that column (toggles asc/desc).
        # We sort the underlying data ourselves (typed: dates/versions/status)
        # and re-render, so sorting works correctly across pagination.
        h.setSectionsClickable(True)
        h.sectionClicked.connect(self._on_header_clicked)
        self._sort_column = None   # None = default status/last-seen ordering
        self._sort_desc = False
        layout.addWidget(self.table, 1)

        # Status bar
        self.status_label = QLabel("Ready")
        self.status_label.setStyleSheet("color:#605e5c;")
        layout.addWidget(self.status_label)

        # Pagination controls
        self._page_size = 50  # default items per page
        self._current_page = 0
        self._total_pages = 1
        self._all_users = []  # store all users for pagination
        self._search_filter = ""  # search filter text

        page_layout = QHBoxLayout()
        page_layout.addWidget(QLabel("Items per page:"))
        self.page_size_combo = QComboBox()
        self.page_size_combo.addItems(["25", "50", "100", "200", "All"])
        self.page_size_combo.setCurrentIndex(1)  # 50
        self.page_size_combo.currentIndexChanged.connect(self._on_page_size_changed)
        page_layout.addWidget(self.page_size_combo)
        page_layout.addSpacing(20)

        self.page_label = QLabel("Page 1 of 1")
        page_layout.addWidget(self.page_label)
        page_layout.addSpacing(10)

        self.prev_btn = QPushButton("← Previous")
        self.prev_btn.setEnabled(False)
        self.prev_btn.clicked.connect(self._prev_page)
        page_layout.addWidget(self.prev_btn)

        self.next_btn = QPushButton("Next →")
        self.next_btn.setEnabled(False)
        self.next_btn.clicked.connect(self._next_page)
        page_layout.addWidget(self.next_btn)

        page_layout.addStretch(1)
        layout.addLayout(page_layout)

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
            "It's stored encrypted with your Windows credentials so only "
            "your user account on this machine can read it.",
            QLineEdit.Password,
            self._token,
        )
        if ok and token.strip():
            self._token = token.strip()
            try:
                secure_storage.set_secret("admin_token", self._token)
            except secure_storage.SecretStorageError as exc:
                QMessageBox.critical(
                    self,
                    "Token was not saved",
                    f"The admin token could not be stored securely.\n\n{exc}",
                )
                return
            self.status_label.setText("Token saved (encrypted). Loading users...")
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

    def _on_search_changed(self, text: str):
        self._search_filter = text.lower().strip()
        self._current_page = 0
        self._update_pagination()
        self._render_table()
    
    def _on_users_loaded(self, users: list):
        self._all_users = users  # store all for pagination
        self._search_filter = ""  # reset search on refresh
        self._current_page = 0
        self._update_pagination()
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

    def _get_filtered_users(self):
        """Return users filtered by search text."""
        if not self._search_filter:
            return self._all_users
        filt = self._search_filter
        return [
            u for u in self._all_users
            if filt in (u.get("email") or "").lower()
            or filt in (u.get("name") or "").lower()
            or filt in (u.get("hostname") or "").lower()
            or filt in (u.get("license_id") or "").lower()
            or filt in (u.get("machine_id") or "").lower()
        ]
    
    def _update_pagination(self):
        """Recalculate total pages based on current page size and data."""
        total = len(self._get_filtered_users())
        if self._page_size == 0:  # All
            self._total_pages = 1
        else:
            self._total_pages = max(1, (total + self._page_size - 1) // self._page_size)
        # Clamp current page
        self._current_page = min(self._current_page, self._total_pages - 1)
        self.page_label.setText(f"Page {self._current_page + 1} of {self._total_pages}")
        self.prev_btn.setEnabled(self._current_page > 0)
        self.next_btn.setEnabled(self._current_page < self._total_pages - 1)

    def _prev_page(self):
        if self._current_page > 0:
            self._current_page -= 1
            self._update_pagination()
            self._render_table()

    def _next_page(self):
        if self._current_page < self._total_pages - 1:
            self._current_page += 1
            self._update_pagination()
            self._render_table()

    def _on_page_size_changed(self, index: int):
        text = self.page_size_combo.currentText()
        if text == "All":
            self._page_size = 0
        else:
            try:
                self._page_size = int(text)
            except ValueError:
                self._page_size = 50
        self._current_page = 0
        self._update_pagination()
        self._render_table()

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
            +"\n".join(
                f"  • {p.get('name') or '(no name)'} <{p.get('email') or ''}>  "
                f"({p['license_id']})"
                for p in missing[:10]
            )
            +("\n  ..." if len(missing) > 10 else "")
            +"\n\nImport them now so they appear in the License Manager?",
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

    def _push_version(self):
        from core.version import __version__
        ver, ok = QInputDialog.getText(
            self, "Push version to all users",
            "Enter the version to allow (e.g. 1.2.38).\n\n"
            "Users will only see and auto-install the update\n"
            "after you push it here.",
            QLineEdit.Normal,
            __version__,
        )
        if not ok or not ver.strip():
            return
        ver = ver.strip()
        self.push_ver_btn.setEnabled(False)
        self.status_label.setText(f"Pushing v{ver} to all users...")

        def _do():
            license_client.admin_push_version(self._token, ver)

        self._push_worker = _SimpleWorker(_do)
        self._push_worker.done.connect(lambda: self._on_push_done(ver))
        self._push_worker.failed.connect(self._on_push_failed)
        self._push_worker.start()

    def _on_push_done(self, ver: str):
        self.push_ver_btn.setEnabled(True)
        self.status_label.setText(f"✓ Version {ver} pushed to all users.")
        QMessageBox.information(
            self, "Version pushed",
            f"All registered users are now allowed to update to v{ver}.\n\n"
            f"They will receive the update on next launch.",
        )

    def _on_push_failed(self, err: str):
        self.push_ver_btn.setEnabled(True)
        self.status_label.setText(f"Push failed: {err}")
        QMessageBox.critical(self, "Push failed", err)

    def _push_version_single(self, license_id: str, name: str):
        from core.version import __version__
        ver, ok = QInputDialog.getText(
            self, f"Push version to {name}",
            f"Enter the version to allow for {name} ({license_id}):",
            QLineEdit.Normal,
            __version__,
        )
        if not ok or not ver.strip():
            return
        ver = ver.strip()
        self.status_label.setText(f"Pushing v{ver} to {name}...")

        def _do():
            license_client.admin_push_version_single(
                self._token, license_id, ver
            )

        w = _SimpleWorker(_do)
        w.done.connect(lambda: self._on_action_done(
            f"v{ver} pushed to {name}"
        ))
        w.failed.connect(self._on_action_failed)
        w.start()
        self._action_worker = w

    # ----- Generate license -----
    def _generate_license(self):
        dlg = _GenerateLicenseDialog(self)
        if dlg.exec_() != QDialog.Accepted:
            return
        params = dlg.values()
        self.generate_btn.setEnabled(False)
        self.status_label.setText(f"Generating license for {params['email']}...")

        def _do():
            return license_client.admin_generate(
                self._token,
                params["email"],
                params["name"],
                days=params["days"],
                machine_id=params["machine_id"],
                note=params["note"],
                hostname=params.get("hostname", ""),
            )

        w = _ResultWorker(_do)
        w.done.connect(self._on_generate_done)
        w.failed.connect(self._on_generate_failed)
        w.start()
        self._gen_worker = w

    def _on_generate_done(self, resp: dict):
        self.generate_btn.setEnabled(True)
        key = resp.get("license_key") or ""
        floating = resp.get("floating")
        if not key:
            self.status_label.setText("Generate failed: no key returned.")
            QMessageBox.critical(self, "Generate failed",
                                 "The Worker did not return a license key.")
            return
        self.status_label.setText(
            f"✓ License {resp.get('license_id')} generated."
        )
        bind_note = ("floating (any device)" if floating
                     else "bound to the specified device")
        _LicenseKeyDialog(
            self, key,
            title="License generated",
            intro=(f"New license created ({bind_note}).\n"
                   "Send this key to the user — they paste it via "
                   "\"Enter / replace license key\" in RunLab Mail."),
        ).exec_()
        self._load()

    def _on_generate_failed(self, err: str):
        self.generate_btn.setEnabled(True)
        self.status_label.setText(f"Generate failed: {err}")
        QMessageBox.critical(self, "Generate failed", err)

    # ----- Change device / re-bind -----
    def _rebind_device(self, license_id: str, name: str):
        mid, ok = QInputDialog.getText(
            self, "Change device — re-bind license",
            "Paste the NEW device's Machine ID for "
            f"{name} ({license_id}).\n\n"
            "The user can find it in RunLab Mail → License dialog, "
            "or you can leave it blank to make the license FLOATING "
            "(usable on any device).",
            QLineEdit.Normal,
            "",
        )
        if not ok:
            return
        mid = mid.strip()
        floating = (mid == "")
        confirm = (
            f"Make {license_id} ({name}) FLOATING (usable on any device)?"
            if floating else
            f"Re-bind {license_id} ({name}) to device:\n  {mid}\n\n"
            "The old device's key will stop working."
        )
        if QMessageBox.question(
            self, "Confirm change device", confirm,
            QMessageBox.Yes | QMessageBox.Cancel, QMessageBox.Yes,
        ) != QMessageBox.Yes:
            return
        self.status_label.setText(f"Re-binding {license_id}...")

        def _do():
            return license_client.admin_rebind(self._token, license_id, mid)

        w = _ResultWorker(_do)
        w.done.connect(lambda resp: self._on_rebind_done(resp, name))
        w.failed.connect(self._on_action_failed)
        w.start()
        self._rebind_worker = w

    def _on_rebind_done(self, resp: dict, name: str):
        key = resp.get("license_key") or ""
        if not key:
            self.status_label.setText("Re-bind failed: no key returned.")
            QMessageBox.critical(self, "Re-bind failed",
                                 "The Worker did not return a license key.")
            return
        floating = not (resp.get("machine_id") or "").strip()
        self.status_label.setText(
            f"✓ {resp.get('license_id')} re-bound. Send the new key to {name}."
        )
        bind_note = ("floating (any device)" if floating
                     else "bound to the new device")
        _LicenseKeyDialog(
            self, key,
            title="Device changed — new license key",
            intro=(f"License re-issued ({bind_note}).\n"
                   f"Send this key to {name} — they paste it via "
                   "\"Enter / replace license key\" on the NEW device."),
        ).exec_()
        self._load()

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

    def _on_header_clicked(self, col: int):
        """Sort by the clicked column. Clicking the same column again toggles
        ascending/descending. Re-renders from page 1."""
        from PyQt5.QtCore import Qt as _Qt
        if self._sort_column == col:
            self._sort_desc = not self._sort_desc
        else:
            self._sort_column = col
            self._sort_desc = False
        # Show the sort indicator arrow on the header.
        try:
            self.table.horizontalHeader().setSortIndicatorShown(True)
            self.table.horizontalHeader().setSortIndicator(
                col, _Qt.DescendingOrder if self._sort_desc else _Qt.AscendingOrder
            )
        except Exception:
            pass
        self._current_page = 0
        self._update_pagination()
        self._render_table()

    def _render_table(self):
        self.table.setRowCount(0)

        # Apply search filter
        filtered_users = self._get_filtered_users()

        if self._sort_column is None:
            # Default ordering: active first, then expired/revoked; within
            # each group, most-recently-seen first.
            def _grp(u):
                return {"active": 0, "expired": 1, "revoked": 2}.get(
                    u.get("status", "active"), 3
                )
            sorted_users = sorted(
                filtered_users,
                key=lambda u: (_grp(u), -1 * int(_iso_to_ts(u.get("last_seen") or ""))),
            )
        else:
            # Column-click sorting with type-aware keys.
            col = self._sort_column

            def _key(u):
                if col == 0:   # License ID
                    return (u.get("license_id") or "").lower()
                if col == 1:   # Status
                    return (u.get("status") or "").lower()
                if col == 2:   # Email
                    return (u.get("email") or "").lower()
                if col == 3:   # Name
                    return (u.get("name") or "").lower()
                if col == 4:   # Hostname
                    return (u.get("hostname") or "").lower()
                if col == 5:   # Version
                    return _version_key(u.get("version") or "")
                if col == 6:   # Allowed Ver.
                    return _version_key(u.get("allowed_version") or "")
                if col == 7:   # Machine ID
                    return (u.get("machine_id") or "").lower()
                if col == 8:   # Public IP
                    return (u.get("public_ip") or "").lower()
                if col == 9:   # Local IP
                    return (u.get("local_ip") or "").lower()
                if col == 10:  # Issued
                    return _iso_to_ts(u.get("issued_at") or "")
                if col == 11:  # Expires (perpetual sorts last when asc)
                    exp = u.get("expires_at") or ""
                    return _iso_to_ts(exp) if exp else float("inf")
                if col == 12:  # Last seen
                    return _iso_to_ts(u.get("last_seen") or "")
                return ""

            sorted_users = sorted(filtered_users, key=_key, reverse=self._sort_desc)

        # Pagination slice
        if self._page_size > 0:
            start = self._current_page * self._page_size
            end = start + self._page_size
            page_users = sorted_users[start:end]
        else:
            page_users = sorted_users

        for u in page_users:
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
                u.get("version") or "",
                u.get("allowed_version") or "(all)",
                u.get("machine_id") or "",
                u.get("public_ip") or "(belum ada)",
                u.get("local_ip") or "(belum ada)",
                (u.get("issued_at") or "")[:10],
                (u.get("expires_at") or "(perpetual)")[:10],
                _iso_to_local(u.get("last_seen") or ""),
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
    def _selected_license_ids(self) -> list:
        """Return license_ids for every selected row (de-duplicated, in the
        order they appear). Used to decide between single- and bulk-action
        menus."""
        rows = sorted({idx.row() for idx in self.table.selectionModel().selectedRows()})
        ids = []
        for r in rows:
            item = self.table.item(r, 0)
            if item and item.text():
                ids.append(item.text())
        return ids

    def _show_menu(self, pos):
        idx = self.table.indexAt(pos)
        if not idx.isValid():
            return

        # If the row under the cursor isn't part of the current selection,
        # treat this as a single-row action on that row (matches typical
        # table UX). Otherwise act on the whole selection.
        clicked_row = idx.row()
        selected_ids = self._selected_license_ids()
        clicked_id = self.table.item(clicked_row, 0).text()
        if clicked_id not in selected_ids:
            selected_ids = [clicked_id]
            self.table.selectRow(clicked_row)

        if len(selected_ids) > 1:
            self._show_bulk_menu(pos, selected_ids)
            return

        row = clicked_row
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
            "🔄  Change device (re-bind to new machine)...",
            lambda: self._rebind_device(license_id, name),
        )
        menu.addAction(
            "🚀  Push specific version to this user...",
            lambda: self._push_version_single(license_id, name),
        )
        menu.addSeparator()
        menu.addAction(
            "🗑  Delete (remove from registry permanently)",
            lambda: self._delete_user(license_id, name),
        )
        menu.exec_(self.table.viewport().mapToGlobal(pos))

    def _show_bulk_menu(self, pos, license_ids: list):
        """Context menu shown when multiple rows are selected. Actions apply
        to every selected license."""
        n = len(license_ids)
        menu = QMenu(self)
        header = menu.addAction(f"{n} licenses selected")
        header.setEnabled(False)
        menu.addSeparator()

        ext_menu = menu.addMenu(f"⏱  Extend {n} licenses")
        for label, days in [
            ("+30 days", 30),
            ("+90 days", 90),
            ("+1 year", 365),
            ("Make perpetual (no expiry)", 0),
            ("Custom...", -1),
        ]:
            ext_menu.addAction(
                label,
                lambda d=days, ids=license_ids: self._bulk_extend(ids, d),
            )

        menu.addSeparator()
        menu.addAction(
            f"🚫  Revoke {n} licenses...",
            lambda ids=license_ids: self._bulk_revoke(ids),
        )
        menu.addAction(
            f"✓  Restore {n} licenses",
            lambda ids=license_ids: self._bulk_restore(ids),
        )
        menu.addSeparator()
        menu.addAction(
            f"🚀  Push specific version to {n} users...",
            lambda ids=license_ids: self._bulk_push_version(ids),
        )
        menu.addSeparator()
        menu.addAction(
            f"🗑  Delete {n} licenses (remove permanently)...",
            lambda ids=license_ids: self._bulk_delete(ids),
        )
        menu.exec_(self.table.viewport().mapToGlobal(pos))

    def _run_bulk(self, fn, license_ids: list, verb: str):
        """Run `fn(license_id)` across all license_ids on a background thread
        and report a summary when done."""
        total = len(license_ids)
        self.status_label.setText(f"{verb} 0/{total}...")
        worker = _BulkWorker(fn, license_ids)
        worker.progress.connect(
            lambda done, tot: self.status_label.setText(f"{verb} {done}/{tot}...")
        )
        worker.finished_bulk.connect(
            lambda ok, errors: self._on_bulk_done(verb, ok, errors)
        )
        worker.start()
        self._action_worker = worker  # keep ref alive

    def _on_bulk_done(self, verb: str, ok: int, errors: list):
        if errors:
            detail = "\n".join(f"  • {lid}: {err}" for lid, err in errors[:10])
            more = f"\n…and {len(errors) - 10} more" if len(errors) > 10 else ""
            QMessageBox.warning(
                self, f"{verb} finished with errors",
                f"{ok} succeeded, {len(errors)} failed.\n\n{detail}{more}",
            )
        self.status_label.setText(
            f"{verb} done: {ok} ok"
            + (f", {len(errors)} failed" if errors else "")
            + ". Refreshing..."
        )
        self._load()

    def _bulk_extend(self, license_ids: list, days: int):
        if days == -1:
            days, ok = QInputDialog.getInt(
                self, "Custom duration",
                "Days from today (0 = perpetual):",
                value=180, min=0, max=3650,
            )
            if not ok:
                return
        n = len(license_ids)
        confirm_msg = (
            f"Make {n} licenses PERPETUAL (never expires)?"
            if days == 0 else
            f"Extend {n} licenses by {days} days from today?"
        )
        if QMessageBox.question(
            self, "Confirm bulk extend", confirm_msg,
            QMessageBox.Yes | QMessageBox.Cancel, QMessageBox.Yes,
        ) != QMessageBox.Yes:
            return
        self._run_bulk(
            lambda lid: license_client.admin_extend(self._token, lid, days),
            license_ids, "Extending",
        )

    def _bulk_revoke(self, license_ids: list):
        reason, ok = QInputDialog.getText(
            self, "Revoke licenses",
            f"Reason for revoking {len(license_ids)} licenses?",
            text="No longer authorized",
        )
        if not ok:
            return
        self._run_bulk(
            lambda lid: license_client.admin_revoke(self._token, lid, reason),
            license_ids, "Revoking",
        )

    def _bulk_restore(self, license_ids: list):
        if QMessageBox.question(
            self, "Restore licenses?",
            f"Restore {len(license_ids)} licenses?",
            QMessageBox.Yes | QMessageBox.Cancel, QMessageBox.Yes,
        ) != QMessageBox.Yes:
            return
        self._run_bulk(
            lambda lid: license_client.admin_restore(self._token, lid),
            license_ids, "Restoring",
        )

    def _bulk_push_version(self, license_ids: list):
        from core.version import __version__
        ver, ok = QInputDialog.getText(
            self, f"Push version to {len(license_ids)} users",
            "Enter the version to allow for the selected users:",
            QLineEdit.Normal, __version__,
        )
        if not ok or not ver.strip():
            return
        ver = ver.strip()
        self._run_bulk(
            lambda lid: license_client.admin_push_version_single(
                self._token, lid, ver
            ),
            license_ids, "Pushing version",
        )

    def _bulk_delete(self, license_ids: list):
        if QMessageBox.warning(
            self, "Permanently delete?",
            f"Permanently REMOVE {len(license_ids)} licenses from the "
            f"registry?\n\nThis is different from Revoke — the rows will be "
            f"gone entirely. Use this only for test accounts or "
            f"confirmed-departed users.",
            QMessageBox.Yes | QMessageBox.Cancel, QMessageBox.Cancel,
        ) != QMessageBox.Yes:
            return
        self._run_bulk(
            lambda lid: license_client.admin_delete(self._token, lid),
            license_ids, "Deleting",
        )

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


def _version_key(v: str) -> tuple:
    """Turn a version string like '1.2.59' into a tuple of ints for correct
    numeric sorting (so 1.2.10 sorts after 1.2.9, not before). Non-numeric
    or empty values (e.g. '(all)') sort first."""
    if not v:
        return (-1,)
    parts = []
    for chunk in str(v).strip().split("."):
        try:
            parts.append(int(chunk))
        except ValueError:
            # Non-numeric component (e.g. "(all)") — sort lowest.
            return (-1,)
    return tuple(parts) if parts else (-1,)



# ---------- Generate / re-bind helper dialogs ----------


class _GenerateLicenseDialog(QDialog):
    """Collects the fields needed to generate a new license."""

    DURATIONS = [
        ("Perpetual (never expires)", 0),
        ("30 days", 30),
        ("90 days", 90),
        ("1 year", 365),
        ("2 years", 730),
    ]

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Generate license")
        self.resize(460, 300)
        self._build_ui()

    def _build_ui(self):
        from PyQt5.QtWidgets import QFormLayout, QCheckBox
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(10)

        form = QFormLayout()
        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("Display name (e.g. Budi Santoso)")
        form.addRow("Name:", self.name_edit)

        self.email_edit = QLineEdit()
        self.email_edit.setPlaceholderText("user@example.com")
        form.addRow("Email:", self.email_edit)

        self.duration_combo = QComboBox()
        for label, _days in self.DURATIONS:
            self.duration_combo.addItem(label)
        form.addRow("Validity:", self.duration_combo)

        # Optional hostname so the admin-generated record isn't blank. The
        # field fills in automatically once the user runs the app, but pre-
        # seeding it helps identify the device in the License Manager table.
        self.hostname_edit = QLineEdit()
        self.hostname_edit.setPlaceholderText(
            "Device hostname, e.g. SS-BTR-DSP (optional)"
        )
        form.addRow("Hostname:", self.hostname_edit)

        # Default to a DEVICE-BOUND license: the Machine ID workflow is the
        # common case (user sends their Machine ID, admin binds the license).
        # Leave 'Floating' unchecked so the Machine ID field is visible and
        # active by default — ticking it makes the license usable anywhere.
        self.floating_cb = QCheckBox(
            "Floating license (usable on any device)"
        )
        self.floating_cb.setChecked(False)
        self.floating_cb.toggled.connect(self._on_floating_toggled)
        form.addRow("", self.floating_cb)

        self.machine_edit = QLineEdit()
        self.machine_edit.setPlaceholderText(
            "Paste the Machine ID the user sent you "
            "(leave blank only for a floating license)"
        )
        self.machine_edit.setEnabled(True)
        form.addRow("Machine ID:", self.machine_edit)

        hint = QLabel(
            "Tip: minta user buka RunLab Mail → dialog License → Copy Machine "
            "ID, lalu tempel di sini. Centang 'Floating' hanya kalau lisensi "
            "boleh dipakai di perangkat mana saja."
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color:#605e5c; font-size: 8pt;")
        form.addRow("", hint)

        self.note_edit = QLineEdit()
        self.note_edit.setPlaceholderText("Optional note")
        form.addRow("Note:", self.note_edit)

        layout.addLayout(form)
        layout.addStretch(1)

        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        ok = QPushButton("Generate")
        ok.setDefault(True)
        ok.clicked.connect(self._validate_accept)
        btn_row.addWidget(cancel)
        btn_row.addWidget(ok)
        layout.addLayout(btn_row)

    def _on_floating_toggled(self, floating: bool):
        # When floating, no machine binding is needed.
        self.machine_edit.setEnabled(not floating)
        if floating:
            self.machine_edit.clear()

    def _validate_accept(self):
        email = self.email_edit.text().strip()
        if "@" not in email:
            QMessageBox.warning(self, "Invalid email",
                                "Please enter a valid email address.")
            return
        if not self.floating_cb.isChecked() and not self.machine_edit.text().strip():
            QMessageBox.warning(
                self, "Machine ID required",
                "Either tick 'Floating license' or paste the target "
                "device's Machine ID.",
            )
            return
        self.accept()

    def values(self) -> dict:
        days = self.DURATIONS[self.duration_combo.currentIndex()][1]
        machine_id = ("" if self.floating_cb.isChecked()
                      else self.machine_edit.text().strip())
        return {
            "name": self.name_edit.text().strip(),
            "email": self.email_edit.text().strip().lower(),
            "days": days,
            "machine_id": machine_id,
            "note": self.note_edit.text().strip(),
            "hostname": self.hostname_edit.text().strip(),
        }



class _LicenseKeyDialog(QDialog):
    """Shows a generated/re-issued license key with a copy button."""

    def __init__(self, parent, license_key: str, *,
                 title: str = "License key", intro: str = ""):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(560, 360)
        self._key = license_key
        self._build_ui(intro)

    def _build_ui(self, intro: str):
        from PyQt5.QtWidgets import QPlainTextEdit
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(10)

        if intro:
            lbl = QLabel(intro)
            lbl.setWordWrap(True)
            lbl.setStyleSheet("color:#605e5c;")
            layout.addWidget(lbl)

        self.key_view = QPlainTextEdit()
        self.key_view.setReadOnly(True)
        self.key_view.setPlainText(self._key)
        self.key_view.setStyleSheet(
            "font-family: Consolas, monospace; font-size: 9pt;"
        )
        layout.addWidget(self.key_view, 1)

        btn_row = QHBoxLayout()
        copy_btn = QPushButton("📋  Copy license key")
        copy_btn.clicked.connect(self._copy)
        btn_row.addWidget(copy_btn)
        btn_row.addStretch(1)
        close = QPushButton("Close")
        close.clicked.connect(self.accept)
        btn_row.addWidget(close)
        layout.addLayout(btn_row)

    def _copy(self):
        from PyQt5.QtWidgets import QApplication
        QApplication.clipboard().setText(self._key)
