"""
Main window with 3-panel Outlook-style layout:
[ Folders / Accounts ] [ Email list ] [ Email viewer ]
"""
import email
from datetime import datetime
from PyQt5.QtWidgets import (
    QMainWindow, QWidget, QHBoxLayout, QVBoxLayout, QSplitter, QTreeWidget,
    QTreeWidgetItem, QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView,
    QLineEdit, QPushButton, QToolBar, QAction, QMessageBox, QStatusBar, QLabel,
    QMenu, QFrame, QStyle, QSizePolicy,
)
from PyQt5.QtCore import Qt, QThread, pyqtSignal, QTimer
from PyQt5.QtGui import QFont, QIcon

from core import database, pop3_client, smtp_client, updater
from core import imap_client
from core import license as licmod
from core.version import __version__
from .account_dialog import AccountDialog
from .accounts_list_dialog import AccountsListDialog
from .compose_dialog import ComposeDialog
from .email_view import EmailView
from .update_dialog import UpdateDialog
from .banner import NotificationBanner
from .license_dialog import LicenseInfoDialog
from .about_dialog import AboutDialog
from .email_list import EmailListWidget, ROLE_EMAIL_ID, ROLE_UNREAD
from .view_bar import ViewBar


# ---------- Outbox flusher (background) ----------
class OutboxFlushWorker(QThread):
    log = pyqtSignal(str)
    sent_one = pyqtSignal(int, int)   # account_id, outbox_id (now sent)
    failed_one = pyqtSignal(int, str) # outbox_id, error
    done = pyqtSignal(int, int)       # success_count, failure_count

    def __init__(self, account_id: int | None = None):
        super().__init__()
        self.account_id = account_id

    def run(self):
        accounts = database.list_accounts()
        if self.account_id is not None:
            accounts = [a for a in accounts if a["id"] == self.account_id]
        sent = 0
        failed = 0
        for acc in accounts:
            outbox = database.list_outbox(acc["id"])
            for entry in outbox:
                raw = database.get_outbox_raw(entry["id"])
                if raw is None:
                    failed += 1
                    self.failed_one.emit(entry["id"], "Outbox payload missing")
                    continue
                try:
                    msg = email.message_from_bytes(raw)
                    smtp_client.send_email(acc, msg)
                    database.move_outbox_to_sent(entry["id"], raw)
                    sent += 1
                    self.sent_one.emit(acc["id"], entry["id"])
                except Exception as e:
                    failed += 1
                    self.failed_one.emit(entry["id"], str(e))
        self.done.emit(sent, failed)


# ---------- Update checker (background) ----------
class UpdateCheckWorker(QThread):
    found = pyqtSignal(dict)
    none = pyqtSignal()

    def run(self):
        m = updater.check_for_updates()
        if m:
            self.found.emit(m)
        else:
            self.none.emit()


# ---------- License revalidation (background) ----------
class LicenseCheckWorker(QThread):
    revoked = pyqtSignal(str)  # error message

    def run(self):
        ok, payload, err = licmod.is_licensed()
        if not ok:
            self.revoked.emit(err)


# ---------- License auto-registration with the Worker ----------
class LicenseRegisterWorker(QThread):
    success = pyqtSignal(dict, str)  # payload, expires_at
    error = pyqtSignal(str)

    def __init__(self, email: str, name: str = ""):
        super().__init__()
        self.email = email
        self.name = name

    def run(self):
        try:
            from core import license_client
            resp = license_client.register_now(self.email, self.name)
            # Re-read the license we just saved so we have the verified
            # payload, not the raw server response
            ok, payload, err = licmod.is_licensed()
            if ok and payload:
                self.success.emit(payload, resp.get("expires_at") or "")
            else:
                self.error.emit(
                    f"Server license could not be verified: {err}"
                )
        except Exception as e:
            self.error.emit(str(e))


class WorkerVerifyWorker(QThread):
    """Calls /verify on the Worker to learn the canonical status of a
    license_id (admin may have extended / revoked since last check)."""
    result = pyqtSignal(str, str)  # status, expires_at

    def __init__(self, license_id: str):
        super().__init__()
        self.license_id = license_id

    def run(self):
        try:
            from core import license_client
            resp = license_client.verify_now(self.license_id)
            self.result.emit(
                str(resp.get("status") or "unknown"),
                str(resp.get("expires_at") or ""),
            )
        except Exception:
            # Network glitch — silent, will retry next tick
            pass


# ---------- Worker ----------
def _purge_old_junk(account: dict, log_cb=None) -> int:
    """Auto-delete junk older than `junk_purge_days` for the given account.

    If IMAP is enabled, the deletion is mirrored to the server too
    (standard IMAP semantics — "delete here means delete everywhere").
    POP3-origin junk is local-only no matter what (POP3 has no folder
    semantics, so we can't tell the server which message to delete)."""
    import datetime as _dt
    days = int(account.get("junk_purge_days") or 0)
    if days <= 0:
        return 0
    cutoff = (_dt.datetime.utcnow() - _dt.timedelta(days=days)).isoformat()
    rows = database.list_old_junk(account["id"], cutoff)
    if not rows:
        database.set_junk_purge_last_run(account["id"], _dt.datetime.utcnow().isoformat())
        return 0

    # Server-side delete first (so if we crash, local cleanup retries next time).
    # This is implicit when IMAP is on — same as Outlook, Thunderbird, etc.
    if account.get("imap_enabled"):
        try:
            uidls = [r["uidl"] for r in rows if r.get("uidl")]
            imap_client.purge_junk_on_server(account, uidls, log_cb=log_cb)
        except Exception as e:
            if log_cb:
                log_cb(f"[Junk purge] Server delete failed (will still purge local): {e}")

    # Local delete
    purged_local = 0
    for r in rows:
        try:
            database.delete_email(r["id"])
            purged_local += 1
        except Exception:
            continue

    database.set_junk_purge_last_run(account["id"], _dt.datetime.utcnow().isoformat())
    return purged_local


class FetchWorker(QThread):
    log = pyqtSignal(str)
    progress = pyqtSignal(int, int)
    done = pyqtSignal(int, int, str)  # account_id, new_count, error_or_empty

    def __init__(self, account):
        super().__init__()
        self.account = account

    def run(self):
        try:
            new_count = pop3_client.fetch_new(
                self.account,
                progress_cb=lambda i, t: self.progress.emit(i, t),
                log_cb=lambda m: self.log.emit(m),
            )
            # Hybrid: also fetch server-side Junk via IMAP (read-only).
            # Failures here don't break the POP3 sync — Junk is best-effort.
            if self.account.get("imap_enabled") and self.account.get("imap_host"):
                try:
                    junk_count = imap_client.fetch_junk(
                        self.account,
                        progress_cb=lambda i, t: self.progress.emit(i, t),
                        log_cb=lambda m: self.log.emit(m),
                    )
                    new_count += junk_count
                except Exception as e:
                    self.log.emit(f"[IMAP Junk] Skipped: {e}")
            # Auto-purge old junk (local + optionally server)
            try:
                purged = _purge_old_junk(self.account, log_cb=lambda m: self.log.emit(m))
                if purged:
                    self.log.emit(f"[Junk purge] Removed {purged} old junk message(s).")
            except Exception as e:
                self.log.emit(f"[Junk purge] Skipped: {e}")
            self.done.emit(self.account["id"], new_count, "")
        except Exception as e:
            self.done.emit(self.account["id"], 0, str(e))


class JunkFetchWorker(QThread):
    """Background-only IMAP Junk fetch, used when the user opens the
    Junk folder so they see fresh server-side Junk without a full sync."""
    done = pyqtSignal(int, int, str)  # account_id, new_count, error_or_empty

    def __init__(self, account):
        super().__init__()
        self.account = account

    def run(self):
        try:
            n = imap_client.fetch_junk(self.account)
            self.done.emit(self.account["id"], n, "")
        except Exception as e:
            self.done.emit(self.account["id"], 0, str(e))


# ---------- Main window ----------
class MainWindow(QMainWindow):
    FOLDERS = [
        ("inbox", "Inbox", "📥"),
        ("outbox", "Outbox", "📮"),
        ("sent", "Sent", "📤"),
        ("drafts", "Drafts", "📝"),
        ("spam", "Junk", "🚫"),
        ("trash", "Trash", "🗑"),
    ]

    def __init__(self, license_payload: dict | None = None):
        super().__init__()
        self.setWindowTitle(f"RunLab Mail {__version__}")
        self.resize(1280, 760)
        self.current_account_id = None
        self.current_folder = "inbox"
        self.current_search = ""
        self.workers = []
        self._update_worker = None
        self._update_silent = True
        self._license_worker = None
        self.license_payload = license_payload or {}
        self._compose_dialogs: list[ComposeDialog] = []
        # Pagination state
        self._loaded_count = 0
        self._total_count = 0
        # Background workers we want to track
        self._register_worker = None

        database.init_db()
        self._build_ui()
        self._refresh_accounts_tree()

        # Periodic auto-fetch every 5 minutes
        self.auto_timer = QTimer(self)
        self.auto_timer.timeout.connect(self._auto_fetch_all)
        self.auto_timer.start(5 * 60 * 1000)

        # Periodic outbox flush every 2 minutes (retries failed sends)
        self.outbox_timer = QTimer(self)
        self.outbox_timer.timeout.connect(self._flush_outbox_silent)
        self.outbox_timer.start(2 * 60 * 1000)
        self._outbox_worker = None
        # Initial flush a few seconds after launch (catch leftover from prior crash)
        QTimer.singleShot(5000, self._flush_outbox_silent)

        # One-time legacy attachment migration (BLOBs → filesystem store).
        # Runs in background so users with multi-GB DBs don't hang on launch.
        self._migration_timer = QTimer(self)
        self._migration_timer.timeout.connect(self._migration_tick)
        QTimer.singleShot(8000, self._kick_off_migration)

        # Show a trial-expiry banner if the license has a near-future
        # expiration date (or is already expired but somehow still active)
        QTimer.singleShot(1500, self._maybe_show_trial_banner)

        if not database.list_accounts():
            QTimer.singleShot(300, self._first_run_prompt)

        # Check for updates 2 seconds after launch
        QTimer.singleShot(2000, lambda: self._check_for_updates(silent=True))

        # Re-validate license against blacklist every 15 minutes.
        # The blacklist itself is cached for 5 minutes client-side, so most
        # checks within a window are cache hits. R2 has 10M reads/month
        # free tier — we use ~150K/month at this rate for 300 users, fine.
        self.license_timer = QTimer(self)
        self.license_timer.timeout.connect(self._recheck_license)
        self.license_timer.start(15 * 60 * 1000)

        # Also poll the Worker every 5 minutes so admin actions
        # (extend / revoke) take effect quickly. The Worker call is
        # very cheap (single GET, ~50ms). With 300 users this is
        # 300 * 96 = ~29K calls/day = 30% of the 100K daily free
        # tier. Combined with the blacklist cache TTL (30 sec) the
        # effective revoke latency is ~30 sec to 5 min.
        self.worker_poll_timer = QTimer(self)
        self.worker_poll_timer.timeout.connect(self._poll_worker_status)
        self.worker_poll_timer.start(5 * 60 * 1000)
        # First check 10 seconds after launch (after migration timer)
        QTimer.singleShot(10000, self._poll_worker_status)

    # ----- UI -----
    def _build_ui(self):
        # Toolbar
        tb = QToolBar("Main")
        tb.setMovable(False)
        from PyQt5.QtCore import QSize
        tb.setIconSize(QSize(20, 20))
        tb.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.addToolBar(tb)

        st = self.style()
        # ---- Primary actions (left) ----
        act_send_recv = QAction(
            st.standardIcon(QStyle.SP_BrowserReload), "Send / Receive", self
        )
        act_send_recv.setToolTip("Fetch new emails and flush the Outbox")
        act_send_recv.triggered.connect(self._fetch_current)
        tb.addAction(act_send_recv)

        act_compose = QAction(
            st.standardIcon(QStyle.SP_FileDialogNewFolder), "New Email", self
        )
        act_compose.setShortcut("Ctrl+N")
        act_compose.setToolTip("Compose a new email (Ctrl+N)")
        act_compose.triggered.connect(self._compose_new)
        tb.addAction(act_compose)

        tb.addSeparator()

        # ---- Search in the middle (Outlook-style) ----
        # Stretchable left spacer to push search to the visual center
        left_spacer = QWidget()
        left_spacer.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        tb.addWidget(left_spacer)

        search_label = QLabel("🔍 ")
        search_label.setStyleSheet("color:#605e5c;")
        tb.addWidget(search_label)
        self.search_edit = QLineEdit()
        self.search_edit.setObjectName("searchInput")
        self.search_edit.setPlaceholderText("Search subject, sender, body...")
        self.search_edit.setMinimumWidth(280)
        self.search_edit.setMaximumWidth(420)
        self.search_edit.textChanged.connect(self._on_search_changed)
        tb.addWidget(self.search_edit)

        # Stretchable right spacer to balance the search in the middle
        right_spacer = QWidget()
        right_spacer.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        tb.addWidget(right_spacer)

        tb.addSeparator()

        # ---- Account & data (right side) ----
        act_account = QAction(
            st.standardIcon(QStyle.SP_DialogOpenButton), "Accounts", self
        )
        act_account.setToolTip("Manage email accounts (add, edit, delete)")
        act_account.triggered.connect(self._open_accounts)
        tb.addAction(act_account)

        act_backup = QAction(
            st.standardIcon(QStyle.SP_DriveHDIcon), "Backup", self
        )
        act_backup.setToolTip("Export or import your RunLab Mail data file")
        act_backup.triggered.connect(self._open_backup_menu)
        tb.addAction(act_backup)

        act_settings = QAction(
            st.standardIcon(QStyle.SP_FileDialogDetailedView),
            "Settings", self
        )
        act_settings.setToolTip("Application settings (data folder, etc.)")
        act_settings.triggered.connect(self._open_settings)
        tb.addAction(act_settings)

        tb.addSeparator()

        act_update = QAction(
            st.standardIcon(QStyle.SP_MessageBoxInformation),
            "About", self
        )
        act_update.setToolTip("Version info and check for updates")
        act_update.triggered.connect(self._open_about)
        tb.addAction(act_update)

        act_license = QAction(
            st.standardIcon(QStyle.SP_FileDialogContentsView),
            "License", self
        )
        act_license.setToolTip("View your license info")
        act_license.triggered.connect(self._show_license_info)
        tb.addAction(act_license)

        # Admin-only: License Manager. Shown only when the running user
        # is recognized as the admin (their email matches ADMIN_EMAIL or
        # they have a "note" containing 'admin' on their license).
        if self._is_admin():
            act_admin = QAction(
                st.standardIcon(QStyle.SP_DialogResetButton),
                "License Manager", self,
            )
            act_admin.setToolTip(
                "Admin-only: issue, revoke, and manage all licenses"
            )
            act_admin.triggered.connect(self._open_license_manager)
            tb.addAction(act_admin)

        # Central widget: banner on top + 3-panel splitter below
        central = QWidget()
        central_layout = QVBoxLayout(central)
        central_layout.setContentsMargins(0, 0, 0, 0)
        central_layout.setSpacing(0)

        self.banner = NotificationBanner()
        central_layout.addWidget(self.banner)

        splitter = QSplitter(Qt.Horizontal)
        central_layout.addWidget(splitter, 1)
        self.setCentralWidget(central)

        # Left: accounts/folders tree
        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.setMinimumWidth(220)
        self.tree.itemClicked.connect(self._on_tree_clicked)
        self.tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._tree_menu)
        splitter.addWidget(self.tree)

        # Middle: email list (Outlook-style 2-line rows + view options bar)
        email_list_container = QWidget()
        elc_layout = QVBoxLayout(email_list_container)
        elc_layout.setContentsMargins(0, 0, 0, 0)
        elc_layout.setSpacing(0)

        self.view_bar = ViewBar()
        self.view_bar.view_changed.connect(self._refresh_email_list)
        elc_layout.addWidget(self.view_bar)

        self.email_list = EmailListWidget()
        self.email_list.itemSelectionChanged.connect(self._on_email_selected)
        self.email_list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.email_list.customContextMenuRequested.connect(self._list_menu)
        self.email_list.request_more.connect(self._load_more_emails)
        elc_layout.addWidget(self.email_list, 1)

        splitter.addWidget(email_list_container)

        # Right: viewer
        self.viewer = EmailView()
        self.viewer.reply_requested.connect(self._reply_or_forward)
        splitter.addWidget(self.viewer)

        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 2)
        splitter.setStretchFactor(2, 3)
        splitter.setSizes([240, 460, 580])

        # Status bar
        sb = QStatusBar()
        self.setStatusBar(sb)
        self.status_label = QLabel("Ready")
        sb.addWidget(self.status_label, 1)
        # Update status (right side, never overwritten by fetch progress)
        self.update_status_label = QLabel("")
        self.update_status_label.setStyleSheet("color:#1565c0; padding-right:12px;")
        sb.addPermanentWidget(self.update_status_label)
        # License indicator on the right
        licensee = self.license_payload.get("name") or self.license_payload.get("email") or ""
        if licensee:
            license_label = QLabel(f"Licensed to: <b>{licensee}</b>")
            license_label.setTextFormat(Qt.RichText)
            license_label.setStyleSheet("color:#555; padding-right:8px;")
            sb.addPermanentWidget(license_label)

    def _first_run_prompt(self):
        ret = QMessageBox.question(
            self, "Welcome to RunLab Mail",
            "No email account configured yet.\nWould you like to add one now?",
            QMessageBox.Yes | QMessageBox.No,
        )
        if ret == QMessageBox.Yes:
            self._add_account()

    # ----- Tree (accounts/folders) -----
    def _refresh_accounts_tree(self):
        self.tree.clear()
        for acc in database.list_accounts():
            counts = database.folder_counts(acc["id"])
            top = QTreeWidgetItem([f'{acc["name"]}  ({acc["email"]})'])
            top.setData(0, Qt.UserRole, ("account", acc["id"]))
            f = top.font(0); f.setBold(True); top.setFont(0, f)
            for fkey, fname, icon in self.FOLDERS:
                c = counts.get(fkey, {"total": 0, "unread": 0})
                if fkey == "inbox" and c["unread"]:
                    label = f'{icon}  {fname} ({c["unread"]})'
                elif fkey == "outbox" and c["total"]:
                    # Show pending count in red so user notices stuck mail
                    label = f'{icon}  {fname} ({c["total"]})'
                else:
                    label = f'{icon}  {fname}'
                child = QTreeWidgetItem([label])
                child.setData(0, Qt.UserRole, ("folder", acc["id"], fkey))
                if fkey == "outbox" and c["total"]:
                    child.setForeground(0, Qt.red)
                top.addChild(child)
            self.tree.addTopLevelItem(top)
            top.setExpanded(True)

        # Restore selection
        if self.current_account_id is not None:
            self._select_folder(self.current_account_id, self.current_folder)
        elif self.tree.topLevelItemCount() > 0:
            first = self.tree.topLevelItem(0).child(0)
            self.tree.setCurrentItem(first)
            self._on_tree_clicked(first, 0)

    def _select_folder(self, account_id, folder):
        for i in range(self.tree.topLevelItemCount()):
            top = self.tree.topLevelItem(i)
            data = top.data(0, Qt.UserRole)
            if data and data[1] == account_id:
                for j in range(top.childCount()):
                    child = top.child(j)
                    cdata = child.data(0, Qt.UserRole)
                    if cdata and cdata[2] == folder:
                        self.tree.setCurrentItem(child)
                        self._on_tree_clicked(child, 0)
                        return

    def _on_tree_clicked(self, item, col):
        data = item.data(0, Qt.UserRole)
        if not data:
            return
        if data[0] == "account":
            self.current_account_id = data[1]
            self.current_folder = "inbox"
            self._select_folder(data[1], "inbox")
            return
        if data[0] == "folder":
            _, acc_id, folder = data
            self.current_account_id = acc_id
            self.current_folder = folder
            self._refresh_email_list()
            # If user opened Junk and IMAP is enabled, refresh server-side
            # junk in the background so they see fresh entries.
            if folder == "spam":
                self._maybe_refresh_junk(acc_id)

    def _tree_menu(self, pos):
        item = self.tree.itemAt(pos)
        if not item:
            return
        data = item.data(0, Qt.UserRole)
        if not data or data[0] != "account":
            return
        acc_id = data[1]
        menu = QMenu(self)
        menu.addAction("Send / Receive", lambda: self._fetch_account(acc_id))
        menu.addAction("Edit account...", lambda: self._edit_account(acc_id))
        menu.addAction("Delete account", lambda: self._delete_account(acc_id))
        menu.exec_(self.tree.viewport().mapToGlobal(pos))

    # ----- Email list -----
    PAGE_SIZE = 200

    def _refresh_email_list(self):
        self.email_list.clear()
        self.viewer.show_empty()
        self._loaded_count = 0
        if self.current_account_id is None:
            self.view_bar.set_count(0)
            return
        # Compute total once for the count label / pagination decisions
        try:
            self._total_count = database.count_emails(
                self.current_account_id, self.current_folder, self.current_search,
            )
        except Exception:
            self._total_count = 0
        self._load_more_emails()
        self.view_bar.set_count(self._total_count)
        self.status_label.setText(
            f"{self._total_count} email(s) in {self.current_folder}"
        )

    def _load_more_emails(self):
        """Load the next page of emails into the visible list."""
        if self.current_account_id is None:
            return
        if self._loaded_count >= self._total_count:
            return
        emails = database.list_emails(
            self.current_account_id, self.current_folder, self.current_search,
            sort_field=self.view_bar.sort_field,
            sort_desc=self.view_bar.sort_desc,
            limit=self.PAGE_SIZE,
            offset=self._loaded_count,
        )
        if self.view_bar.group_by_conversation and self._loaded_count == 0:
            # Conversation grouping needs a full pass to collapse threads,
            # so when grouping is on we fetch up to a higher cap once and
            # treat that as "all loaded".
            emails = database.list_emails(
                self.current_account_id, self.current_folder, self.current_search,
                sort_field=self.view_bar.sort_field,
                sort_desc=self.view_bar.sort_desc,
                limit=2000,
            )
            emails = self._apply_grouping(emails)
            self._loaded_count = self._total_count  # treat as fully loaded
        else:
            self._loaded_count += len(emails)

        for e in emails:
            self.email_list.add_email(e, self.current_folder)

    def _apply_grouping(self, emails: list) -> list:
        """Group emails by normalized subject (Re:/Fwd: stripped). Within a
        group, the most recent email is shown; the count is appended to the
        subject. This is a lightweight conversation view — clicking a
        grouped item opens the latest message of that thread."""
        import re
        groups: dict[str, list] = {}
        order: list[str] = []
        for e in emails:
            subj = (e.get("subject") or "").strip()
            normalized = re.sub(
                r"^(?:re|fw|fwd|aw|sv)\s*:\s*", "",
                subj, flags=re.IGNORECASE,
            ).strip().lower() or "(no subject)"
            if normalized not in groups:
                groups[normalized] = []
                order.append(normalized)
            groups[normalized].append(e)

        result = []
        for key in order:
            items = groups[key]
            head = dict(items[0])  # most recent per current sort
            if len(items) > 1:
                head["subject"] = (
                    f"{head.get('subject') or '(no subject)'}  "
                    f"({len(items)} messages)"
                )
                # If any in the thread is unread, mark head unread so the
                # bold/blue accent shows
                if any(not it.get("is_read") for it in items):
                    head["is_read"] = 0
            result.append(head)
        return result

    def _format_date(self, iso: str) -> str:
        # Kept for backward compat but no longer used in email list
        # (the delegate handles its own date formatting)
        if not iso:
            return ""
        try:
            dt = datetime.fromisoformat(iso.replace("Z", ""))
            today = datetime.now().date()
            if dt.date() == today:
                return dt.strftime("%H:%M")
            if dt.year == today.year:
                return dt.strftime("%b %d")
            return dt.strftime("%Y-%m-%d")
        except Exception:
            return iso

    def _on_email_selected(self):
        email_id = self.email_list.selected_email_id()
        if email_id is None:
            self.viewer.show_empty()
            return
        # Drafts open in compose dialog instead of read-only viewer
        if self.current_folder == "drafts":
            self._open_draft(email_id)
            self.email_list.clearSelection()
            return
        # Outbox: show read-only viewer (don't mark as read; it's outgoing)
        if self.current_folder == "outbox":
            self.viewer.show_email(email_id)
            return
        database.mark_read(email_id, True)
        self.viewer.show_email(email_id)
        # Visual: remove unread bar/bold for the now-read row
        row = self.email_list.currentRow()
        if row >= 0:
            self.email_list.mark_read_visual(row)
        self._update_folder_counts()

    def _update_folder_counts(self):
        if self.current_account_id is None:
            return
        counts = database.folder_counts(self.current_account_id)
        for i in range(self.tree.topLevelItemCount()):
            top = self.tree.topLevelItem(i)
            data = top.data(0, Qt.UserRole)
            if not data or data[1] != self.current_account_id:
                continue
            for j in range(top.childCount()):
                child = top.child(j)
                cdata = child.data(0, Qt.UserRole)
                if not cdata:
                    continue
                fkey = cdata[2]
                fname = next((n for k, n, _ in self.FOLDERS if k == fkey), fkey)
                icon = next((ic for k, _, ic in self.FOLDERS if k == fkey), "")
                c = counts.get(fkey, {"total": 0, "unread": 0})
                if fkey == "inbox" and c["unread"]:
                    child.setText(0, f'{icon}  {fname} ({c["unread"]})')
                elif fkey == "outbox" and c["total"]:
                    child.setText(0, f'{icon}  {fname} ({c["total"]})')
                    child.setForeground(0, Qt.red)
                else:
                    child.setText(0, f'{icon}  {fname}')
                    child.setForeground(0, Qt.black if fkey != "outbox" else Qt.black)

    def _on_search_changed(self, text):
        self.current_search = text.strip()
        self._refresh_email_list()

    def _list_menu(self, pos):
        idx = self.email_list.indexAt(pos)
        if not idx.isValid():
            return
        item = self.email_list.item(idx.row())
        if not item:
            return
        email_id = item.data(ROLE_EMAIL_ID)
        menu = QMenu(self)
        menu.addAction("Open", lambda: self.viewer.show_email(email_id))
        menu.addSeparator()
        menu.addAction("Mark as read", lambda: self._mark_read(email_id, True))
        menu.addAction("Mark as unread", lambda: self._mark_read(email_id, False))
        menu.addSeparator()
        if self.current_folder == "spam":
            menu.addAction("Not junk (move to Inbox)",
                           lambda: self._mark_not_spam(email_id))
        elif self.current_folder == "trash":
            menu.addAction("Delete permanently",
                           lambda: self._delete_email(email_id))
        else:
            menu.addAction("Move to Junk 🚫",
                           lambda: self._mark_spam(email_id))
            menu.addAction("Move to Trash",
                           lambda: self._trash_email(email_id))
        if self.current_folder == "trash":
            menu.addAction("Delete permanently",
                           lambda: self._delete_email(email_id))
        menu.exec_(self.email_list.viewport().mapToGlobal(pos))

    def _mark_spam(self, email_id):
        database.move_to_spam(email_id)
        self._refresh_email_list()
        self._update_folder_counts()
        self.status_label.setText("Moved to Junk (server folder is not affected — POP3 limitation)")

    def _maybe_refresh_junk(self, account_id: int):
        """Trigger a background IMAP Junk fetch if the account has IMAP
        enabled. No-ops otherwise. Errors are silent — IMAP is best-effort."""
        acc = database.get_account(account_id)
        if not acc or not acc.get("imap_enabled") or not acc.get("imap_host"):
            return
        worker = JunkFetchWorker(acc)
        worker.done.connect(self._on_junk_refreshed)
        self.workers.append(worker)
        worker.start()

    def _on_junk_refreshed(self, account_id: int, new_count: int, err: str):
        if err:
            return
        if new_count > 0 and self.current_folder == "spam" and self.current_account_id == account_id:
            self._refresh_email_list()
            self._update_folder_counts()
            self.status_label.setText(f"Fetched {new_count} new junk message(s) from server.")

    def _mark_not_spam(self, email_id):
        database.move_to_inbox(email_id)
        self._refresh_email_list()
        self._update_folder_counts()

    def _mark_read(self, email_id, read):
        database.mark_read(email_id, read)
        self._refresh_email_list()
        self._update_folder_counts()

    def _trash_email(self, email_id):
        database.move_to_trash(email_id)
        self._refresh_email_list()
        self._update_folder_counts()

    def _delete_email(self, email_id):
        if QMessageBox.question(self, "Delete", "Permanently delete this email?") == QMessageBox.Yes:
            database.delete_email(email_id)
            self._refresh_email_list()
            self._update_folder_counts()

    # ----- Account actions -----
    def _open_accounts(self):
        """Toolbar 'Accounts' button: list view of all accounts."""
        dlg = AccountsListDialog(self)
        dlg.exec_()
        self._refresh_accounts_tree()

    def _add_account(self):
        dlg = AccountDialog(self)
        if dlg.exec_():
            self._refresh_accounts_tree()

    def _edit_account(self, account_id):
        acc = database.get_account(account_id)
        if not acc:
            return
        dlg = AccountDialog(self, account=acc)
        if dlg.exec_():
            self._refresh_accounts_tree()

    def _delete_account(self, account_id):
        if QMessageBox.question(
            self, "Delete account",
            "Delete this account and ALL its emails from the local database?\n"
            "(messages on the server are NOT touched)",
        ) == QMessageBox.Yes:
            database.delete_account(account_id)
            if self.current_account_id == account_id:
                self.current_account_id = None
            self._refresh_accounts_tree()
            self._refresh_email_list()

    # ----- Send / Receive -----
    def _fetch_current(self):
        if self.current_account_id is None:
            QMessageBox.information(self, "RunLab Mail", "Please add and select an account.")
            return
        # Use the rich progress dialog for explicit Send/Receive clicks
        self._start_sync_with_dialog([self.current_account_id])

    def _auto_fetch_all(self):
        # Silent background fetch — no dialog, just status bar updates
        for acc in database.list_accounts():
            self._fetch_account(acc["id"], silent=True)
        self._flush_outbox_silent()

    def _start_sync_with_dialog(self, account_ids: list[int]):
        """Run a Send/Receive cycle for the given accounts with a visible
        progress + log dialog (Outlook-style)."""
        from .sync_progress_dialog import SyncProgressDialog
        dlg = SyncProgressDialog(self)
        self._sync_dialog = dlg
        # Track account → worker so we can wire up callbacks per task
        self._sync_remaining = 0
        self._sync_workers = []

        accounts = [database.get_account(a) for a in account_ids]
        accounts = [a for a in accounts if a]
        if not accounts:
            return

        for acc in accounts:
            key = f"acc-{acc['id']}"
            dlg.add_task(key, f"{acc['name']} <{acc['email']}>")

        dlg.cancel_requested.connect(self._on_sync_cancel)
        # Show non-modal so user can keep using the app
        dlg.show()
        dlg.log(f"Starting Send/Receive for {len(accounts)} account(s)...",
                level="info")

        # Also flush outbox first
        dlg.log("Flushing Outbox...", level="info")
        self._flush_outbox_silent()

        # Spin up one worker per account, run sequentially via signal chain
        # to keep network load gentle (and order predictable in the log)
        self._sync_queue = list(accounts)
        self._sync_run_next()

    def _sync_run_next(self):
        if not getattr(self, "_sync_queue", None):
            self._sync_dialog.all_done()
            return
        if self._sync_dialog.is_cancelled:
            self._sync_dialog.all_done()
            return
        acc = self._sync_queue.pop(0)
        key = f"acc-{acc['id']}"
        self._sync_dialog.start_task(key)
        self._sync_dialog.log(
            f"[{acc['name']}] Connecting to {acc['pop3_host']}:"
            f"{acc['pop3_port']}...",
            level="info",
        )

        worker = FetchWorker(acc)
        self._sync_workers.append(worker)
        worker.log.connect(
            lambda msg, k=key, name=acc["name"]:
                self._sync_dialog.log(f"[{name}] {msg}", level="info")
        )
        worker.progress.connect(
            lambda i, t, k=key:
                self._sync_dialog.update_task(k, "fetching", i, t)
        )
        worker.done.connect(
            lambda aid, n, err, k=key, name=acc["name"]:
                self._on_sync_account_done(k, name, aid, n, err)
        )
        worker.start()

    def _on_sync_account_done(self, key, name, account_id, new_count, err):
        if err:
            self._sync_dialog.log(f"[{name}] ERROR: {err}", level="error")
            self._sync_dialog.finish_task(key, success=False,
                                          summary=f"failed: {err[:50]}")
        else:
            if new_count > 0:
                self._sync_dialog.log(
                    f"[{name}] Done — {new_count} new email(s).",
                    level="success",
                )
                self._sync_dialog.finish_task(
                    key, success=True,
                    summary=f"{new_count} new",
                )
            else:
                self._sync_dialog.log(f"[{name}] Done — no new email.",
                                      level="success")
                self._sync_dialog.finish_task(
                    key, success=True, summary="up to date"
                )
            # Auto-register / refresh license after a successful POP3 fetch
            self._maybe_auto_register_license(account_id)

        # Refresh UI for whichever account is currently visible
        if account_id == self.current_account_id:
            self._refresh_email_list()
        self._refresh_accounts_tree()
        # Cleanup
        self._sync_workers = [w for w in self._sync_workers if w.isRunning()]
        # Continue to next account in the queue
        self._sync_run_next()

    def _on_sync_cancel(self):
        # Mark queue empty so no new accounts are dispatched. Workers
        # already running will run to completion (POP3 doesn't support
        # mid-fetch cancellation cleanly).
        self._sync_queue = []
        self._sync_dialog.log(
            "Will stop after the current account finishes...",
            level="warn",
        )

    def _flush_outbox_silent(self):
        """Background outbox flush. Quietly tries to send queued mail."""
        if self._outbox_worker and self._outbox_worker.isRunning():
            return
        # Skip work if outbox is empty across all accounts
        any_pending = False
        for acc in database.list_accounts():
            if database.list_outbox(acc["id"]):
                any_pending = True
                break
        if not any_pending:
            return
        self._outbox_worker = OutboxFlushWorker()
        self._outbox_worker.sent_one.connect(self._on_outbox_sent)
        self._outbox_worker.done.connect(self._on_outbox_flush_done)
        self._outbox_worker.start()

    def _flush_outbox_now(self):
        """Manual: triggered by user pressing toolbar Send/Receive on Outbox."""
        if self._outbox_worker and self._outbox_worker.isRunning():
            self.status_label.setText("Outbox is already flushing...")
            return
        self.status_label.setText("Flushing outbox...")
        self._outbox_worker = OutboxFlushWorker()
        self._outbox_worker.sent_one.connect(self._on_outbox_sent)
        self._outbox_worker.done.connect(self._on_outbox_flush_done_manual)
        self._outbox_worker.start()

    def _on_outbox_sent(self, account_id: int, outbox_id: int):
        # Refresh whichever folder is currently visible
        if self.current_account_id == account_id and self.current_folder in (
            "outbox", "sent"
        ):
            self._refresh_email_list()
        self._update_folder_counts()

    def _on_outbox_flush_done(self, sent: int, failed: int):
        if sent > 0:
            self.status_label.setText(f"Outbox: sent {sent} email(s).")
        # silently ignore failures; will retry next tick
        self._refresh_accounts_tree()

    def _on_outbox_flush_done_manual(self, sent: int, failed: int):
        msg = f"Outbox flush complete. Sent: {sent}"
        if failed:
            msg += f", failed: {failed} (still in Outbox, will retry)"
        self.status_label.setText(msg)
        self._refresh_accounts_tree()
        if self.current_folder in ("outbox", "sent"):
            self._refresh_email_list()

    # ----- Auto license registration -----
    def _maybe_auto_register_license(self, account_id: int):
        """If we don't have a valid license yet, register this machine
        with the Worker now that we've proven a working POP3 setup."""
        # Already registered (any non-empty real payload, not the
        # _unactivated placeholder)
        if self.license_payload and self.license_payload.get("license_id"):
            return
        if getattr(self, "_register_worker", None) and \
                self._register_worker.isRunning():
            return
        acc = database.get_account(account_id)
        if not acc:
            return
        worker = LicenseRegisterWorker(
            email=acc.get("email") or "",
            name=acc.get("name") or "",
        )
        self._register_worker = worker
        worker.success.connect(self._on_register_success)
        worker.error.connect(self._on_register_error)
        worker.start()

    def _on_register_success(self, payload: dict, expires_at: str):
        self.license_payload = payload
        # Friendly banner so the user knows what happened
        from datetime import datetime
        try:
            exp_dt = datetime.fromisoformat(
                expires_at.replace("Z", "+00:00")
            )
            exp_str = exp_dt.strftime("%d %b %Y")
        except Exception:
            exp_str = expires_at
        self.banner.show_message(
            f"<b>🎉 RunLab Mail trial activated.</b> Your license is valid "
            f"until <b>{exp_str}</b>. Contact your admin to extend.",
            level="success",
            duration_ms=12000,
        )

    def _on_register_error(self, err: str):
        # Soft failure — user can still use POP3 + SMTP, we just retry
        # auto-registration on the next successful sync. We don't nag
        # them with a popup since the app is otherwise working fine.
        self.update_status_label.setText(
            "License registration failed (will retry)"
        )
        self.update_status_label.setToolTip(err)

    def _poll_worker_status(self):
        """Hit /verify on the Worker to detect admin actions (extend,
        revoke) within ~60 seconds. If the Worker reports the license
        was extended, re-register to fetch the new signed license."""
        if not self.license_payload or not self.license_payload.get("license_id"):
            return  # no license yet — nothing to verify
        license_id = self.license_payload["license_id"]
        worker = WorkerVerifyWorker(license_id)
        worker.result.connect(self._on_worker_verify)
        worker.start()
        self._verify_worker = worker  # keep ref so it isn't GC'd

    def _on_worker_verify(self, status: str, expires_at: str):
        """Called with the Worker's view of our license."""
        if status == "revoked":
            # Worker sees us as revoked → re-run the local license check
            # which will pick up the blacklist and kick the user out.
            self._recheck_license()
            return
        if status == "expired":
            # Same: local check will catch it
            self._recheck_license()
            return
        if status == "active":
            # Compare expires_at — if the Worker has a newer expiry date,
            # it means the admin extended us and we need a fresh signed
            # license file.
            local_exp = (self.license_payload.get("expires_at") or "").strip()
            if expires_at and expires_at != local_exp:
                self._refetch_license_from_worker()

    def _refetch_license_from_worker(self):
        """Re-register with the Worker so it returns a fresh signed
        license with the updated expiry date."""
        # Find the email we used for the current license
        email = self.license_payload.get("email") or ""
        name = self.license_payload.get("name") or ""
        if not email:
            # Fall back to the first account's email
            accounts = database.list_accounts()
            if accounts:
                email = accounts[0].get("email") or ""
                name = accounts[0].get("name") or ""
        if not email:
            return
        worker = LicenseRegisterWorker(email=email, name=name)
        worker.success.connect(self._on_license_refetched)
        worker.error.connect(lambda err: None)  # silent retry next tick
        worker.start()
        self._refetch_worker = worker

    def _on_license_refetched(self, payload: dict, expires_at: str):
        """A re-fetched license came back from the Worker — update our
        in-memory payload and tell the user."""
        self.license_payload = payload
        # Hide any prior trial-expiry banner since the situation changed
        self.banner.hide()
        # Re-evaluate trial status with the fresh data
        self._maybe_show_trial_banner()
        # Subtle confirmation in the dedicated update label
        self.update_status_label.setText("✓ License updated")
        QTimer.singleShot(
            6000, lambda: self.update_status_label.setText("")
        )

    # ----- Legacy attachment migration -----
    def _kick_off_migration(self):
        """Decide whether to start the BLOB → filesystem migration."""
        try:
            pending = database.pending_migration_count()
        except Exception:
            return
        if pending <= 0:
            return
        self._migration_pending = pending
        self.update_status_label.setText(
            f"Optimizing storage… {pending} attachments queued"
        )
        # 1 batch every 800ms — slow enough to not affect UI, fast enough
        # that a user with thousands of legacy blobs migrates in a few minutes
        self._migration_timer.start(800)

    def _migration_tick(self):
        try:
            migrated = database.run_pending_migrations()
        except Exception:
            self._migration_timer.stop()
            return
        if migrated == 0:
            self._migration_timer.stop()
            self.update_status_label.setText("Storage optimization complete")
            QTimer.singleShot(
                4000, lambda: self.update_status_label.setText("")
            )
            return
        try:
            remaining = database.pending_migration_count()
            self.update_status_label.setText(
                f"Optimizing storage… {remaining} attachments left"
            )
        except Exception:
            pass

    def _fetch_account(self, account_id, silent=False):
        acc = database.get_account(account_id)
        if not acc:
            return
        worker = FetchWorker(acc)
        self.workers.append(worker)
        worker.log.connect(lambda m: self.status_label.setText(m))
        worker.progress.connect(lambda i, t: self.status_label.setText(f"Fetching {i}/{t}..."))
        worker.done.connect(lambda aid, n, err: self._on_fetch_done(aid, n, err, silent))
        worker.start()

    def _on_fetch_done(self, account_id, new_count, error, silent):
        if error:
            self.status_label.setText(f"Error: {error}")
            if not silent:
                QMessageBox.critical(self, "Send/Receive failed", error)
        else:
            self.status_label.setText(f"Done. {new_count} new email(s).")
        if account_id == self.current_account_id:
            self._refresh_email_list()
        self._refresh_accounts_tree()
        # Cleanup finished workers
        self.workers = [w for w in self.workers if w.isRunning()]

    # ----- Compose / reply -----
    def _track_compose(self, dlg: "ComposeDialog"):
        """Register a compose dialog so it can be auto-saved before app
        restarts for updates."""
        self._compose_dialogs.append(dlg)
        dlg.draft_saved.connect(self._on_draft_saved)
        dlg.finished.connect(lambda *_: self._untrack_compose(dlg))

    def _untrack_compose(self, dlg):
        try:
            self._compose_dialogs.remove(dlg)
        except ValueError:
            pass

    def _on_draft_saved(self, account_id):
        # Refresh tree counts and (if we're viewing Drafts of this account)
        # the email list
        self._refresh_accounts_tree()
        if (account_id == self.current_account_id
                and self.current_folder == "drafts"):
            self._refresh_email_list()

    def _save_all_drafts(self) -> int:
        """Force-save all open compose windows. Returns count saved."""
        saved = 0
        for dlg in list(self._compose_dialogs):
            try:
                if dlg.force_save_for_shutdown():
                    saved += 1
            except Exception:
                pass
        return saved

    def _compose_new(self):
        if not database.list_accounts():
            QMessageBox.warning(self, "RunLab Mail", "Please add an account first.")
            return
        dlg = ComposeDialog(self, account_id=self.current_account_id)
        self._track_compose(dlg)
        dlg.sent.connect(self._on_email_sent)
        dlg.show()  # non-modal so user can keep using the app

    def _open_draft(self, draft_id: int):
        d = database.get_draft(draft_id)
        if not d:
            return
        prefill = {
            "to": d.get("recipients") or "",
            "cc": d.get("cc") or "",
            "bcc": d.get("bcc") or "",
            "subject": d.get("subject") or "",
            "body": d.get("body_plain") or "",
        }
        dlg = ComposeDialog(
            self,
            account_id=d.get("account_id"),
            prefill=prefill,
            draft_id=draft_id,
        )
        self._track_compose(dlg)
        dlg.sent.connect(self._on_email_sent)
        dlg.show()

    def _on_email_sent(self, account_id):
        if account_id == self.current_account_id and self.current_folder == "sent":
            self._refresh_email_list()
        self._refresh_accounts_tree()

    def _reply_or_forward(self, email, mode):
        if not email:
            return
        prefill = {}
        original_subject = email.get("subject") or ""

        # Build quoted body — prefer HTML so the original email's formatting
        # (signatures, colors, tables, embedded images) survives the reply.
        body_html_quoted = self._quote_body_html(email)

        if mode == "reply":
            prefill["to"] = email.get("sender") or ""
            prefill["subject"] = self._prefix_subject(original_subject, "Re: ")
            prefill["body_html"] = body_html_quoted
        elif mode == "reply_all":
            prefill["to"] = email.get("sender") or ""
            prefill["cc"] = email.get("cc") or ""
            prefill["subject"] = self._prefix_subject(original_subject, "Re: ")
            prefill["body_html"] = body_html_quoted
        elif mode == "forward":
            prefill["subject"] = self._prefix_subject(original_subject, "Fwd: ")
            prefill["body_html"] = body_html_quoted

        # Re-attach the original email's attachments. Forward sends them
        # along automatically (Outlook behavior). Reply / Reply All also
        # carry them so the user doesn't lose context — they can hit
        # Remove if not needed.
        try:
            atts = database.get_attachments_for_email(email["id"])
        except Exception:
            atts = []
        if atts:
            prefill["forwarded_attachments"] = atts

        dlg = ComposeDialog(
            self, account_id=self.current_account_id, prefill=prefill
        )
        self._track_compose(dlg)
        dlg.sent.connect(self._on_email_sent)
        dlg.show()

    @staticmethod
    def _prefix_subject(subject, prefix):
        if subject.lower().startswith(prefix.lower()):
            return subject
        return f"{prefix}{subject}"

    @staticmethod
    def _quote_body_html(email) -> str:
        """Build the quoted body for a reply or forward.

        Outlook-style: a separator block with the original sender/date/subject,
        then the original HTML body (or plain body wrapped in <pre>) inset
        with a left border.

        Returns an HTML string ready to drop into a QTextEdit via insertHtml().
        """
        import html as html_lib
        sender = email.get("sender") or ""
        recipients = email.get("recipients") or ""
        date = email.get("date_sent") or email.get("date_received") or ""
        subject = email.get("subject") or "(no subject)"
        body_html = email.get("body_html") or ""
        body_plain = email.get("body_plain") or ""

        # Header block (Outlook style: bold labels, monospace not needed)
        # Use a <p> with border-top instead of <hr> because Qt's HTML
        # subset doesn't always render <hr>
        header = (
            "<p style=\"border-top:1px solid #c8c6c4;margin:14px 0 6px 0;"
            "padding-top:10px;\">&nbsp;</p>"
            "<div style=\"font-family:'Segoe UI',sans-serif;font-size:10pt;"
            "color:#605e5c;margin-bottom:6px;\">"
            f"<b>From:</b> {html_lib.escape(sender)}<br>"
            f"<b>Sent:</b> {html_lib.escape(date)}<br>"
            f"<b>To:</b> {html_lib.escape(recipients)}<br>"
        )
        cc = email.get("cc")
        if cc:
            header += f"<b>Cc:</b> {html_lib.escape(cc)}<br>"
        header += f"<b>Subject:</b> {html_lib.escape(subject)}"
        header += "</div>"

        # Body block — use HTML if we have it, otherwise wrap plain text
        if body_html:
            inner = body_html
        else:
            escaped = html_lib.escape(body_plain)
            inner = (
                "<pre style=\"font-family:'Segoe UI',sans-serif;font-size:10pt;"
                "white-space:pre-wrap;margin:0;\">"
                f"{escaped}</pre>"
            )

        return header + (
            "<div style=\"border-left:3px solid #c8c6c4;padding-left:12px;"
            "margin-left:0;\">" + inner + "</div>"
        )

    # ----- Single instance -----
    def bring_to_front(self):
        """Called when another RunLab Mail launch is attempted. Surface this window."""
        # Restore from minimized — if previously maximized, restore to maximized
        if self.isMinimized():
            self.showNormal()
            self.showMaximized()
        else:
            self.show()
        self.setWindowState(
            (self.windowState() & ~Qt.WindowMinimized) | Qt.WindowActive
        )
        self.raise_()
        self.activateWindow()
        from PyQt5.QtWidgets import QApplication
        QApplication.alert(self, 1500)

    def closeEvent(self, event):
        """Save any open compose drafts before exiting."""
        try:
            self._save_all_drafts()
        except Exception:
            pass
        super().closeEvent(event)

    # ----- Auto-update -----
    def _check_for_updates(self, silent: bool = True):
        if self._update_worker and self._update_worker.isRunning():
            return
        self._update_silent = silent
        if not silent:
            self.update_status_label.setText("⟳ Checking for updates...")
        self._update_worker = UpdateCheckWorker()
        self._update_worker.found.connect(self._on_update_found)
        self._update_worker.none.connect(self._on_no_update)
        self._update_worker.start()

    def _on_update_found(self, manifest: dict):
        version = manifest.get("version", "?")
        # Auto-download in background. Status shown in dedicated label that
        # never gets overwritten by fetch progress.
        self.update_status_label.setText(f"⬇ Downloading update {version}...")
        self._auto_install(manifest)

    def _auto_install(self, manifest: dict):
        if not updater.is_frozen():
            self.update_status_label.setText(
                f"Update {manifest.get('version', '?')} available (dev mode)"
            )
            return

        from .update_dialog import _DownloadWorker
        self._auto_dl_worker = _DownloadWorker(manifest)
        self._auto_dl_worker.progress.connect(self._on_auto_dl_progress)
        self._auto_dl_worker.finished_ok.connect(
            lambda path: self._on_auto_dl_done(path, manifest)
        )
        self._auto_dl_worker.finished_err.connect(self._on_auto_dl_error)
        self._auto_dl_worker.start()

    def _on_auto_dl_progress(self, downloaded, total):
        if total > 0:
            pct = int(downloaded * 100 / total)
            self.update_status_label.setText(
                f"⬇ Downloading update: {pct}%"
            )
        else:
            self.update_status_label.setText(
                f"⬇ Downloading update: {downloaded/1024/1024:.1f} MB"
            )

    def _on_auto_dl_done(self, path, manifest):
        from pathlib import Path
        self._pending_update_path = Path(path)
        self._pending_update_seconds = 5
        version = manifest.get("version", "?")
        self.update_status_label.setText(f"✓ Update {version} downloaded")
        self._show_update_countdown(version)

    def _show_update_countdown(self, version: str):
        n = self._pending_update_seconds
        if n <= 0:
            self._install_pending_update(version)
            return

        # Adjust message based on whether there are open compose windows
        compose_count = len(self._compose_dialogs)
        if compose_count > 0:
            extra = (
                f" <span style='color:#666;'>"
                f"({compose_count} draft akan disimpan otomatis)</span>"
            )
        else:
            extra = ""
        self.banner.show_message(
            f"<b>RunLab Mail {version} ready.</b> "
            f"Restarting in {n}s. <a href='now'>Restart now</a> "
            f"<a href='cancel' style='color:#666;'>Cancel</a>{extra}",
            level="success",
            duration_ms=0,
        )
        try:
            self.banner.label.linkActivated.disconnect()
        except TypeError:
            pass
        self.banner.label.linkActivated.connect(self._on_countdown_link)

        self._pending_update_seconds -= 1
        QTimer.singleShot(1000, lambda: self._show_update_countdown(version))

    def _install_pending_update(self, version: str):
        """Save drafts, then trigger the install. Single point of entry."""
        # 1. Save any open compose windows as drafts so nothing is lost
        saved = self._save_all_drafts()
        if saved > 0:
            self.banner.show_message(
                f"Saved {saved} draft(s). Installing RunLab Mail {version}...",
                level="info", duration_ms=0,
            )
        else:
            self.banner.show_message(
                f"Installing RunLab Mail {version}...",
                level="info", duration_ms=0,
            )
        # Force-process pending events so the user sees the message
        from PyQt5.QtWidgets import QApplication
        QApplication.processEvents()

        # 2. Close compose dialogs (they've been saved already)
        for dlg in list(self._compose_dialogs):
            try:
                dlg.close()
            except Exception:
                pass

        # 3. Trigger the actual swap-and-restart
        try:
            updater.install_and_restart(self._pending_update_path)
        except Exception as e:
            self.banner.show_message(
                f"Update install failed: {e}",
                level="error", duration_ms=0,
            )

    def _on_countdown_link(self, action: str):
        if action == "now":
            self._restart_now()
        elif action == "cancel":
            self._pending_update_seconds = -999
            self.banner.hide()
            self.update_status_label.setText(
                "Update postponed (will retry on next launch)"
            )

    def _restart_now(self, *_):
        self._pending_update_seconds = 0
        version = "update"
        try:
            # Reuse the current banner version label if known
            pass
        except Exception:
            pass
        self._install_pending_update(version)

    def _on_auto_dl_error(self, err: str):
        self.update_status_label.setText("⚠ Update download failed")
        self.update_status_label.setToolTip(err)

    def _open_update_dialog(self, manifest: dict):
        """Manual route to dialog (kept for fallback / future use)."""
        self.banner.hide()
        dlg = UpdateDialog(manifest, self)
        dlg.exec_()

    def _on_no_update(self):
        # Auto-check at startup: clear any stale message, no banner spam
        self.update_status_label.setText("")
        self.status_label.setText("Ready")

    # ----- License -----
    def _show_license_info(self):
        if not self.license_payload:
            QMessageBox.information(self, "RunLab Mail", "No license info available.")
            return
        LicenseInfoDialog(self.license_payload, self).exec_()

    def _open_about(self):
        dlg = AboutDialog(self, license_payload=self.license_payload)
        dlg.exec_()

    def _is_admin(self) -> bool:
        """Admin = the developer who built this RunLab Mail. We check both the
        license note (set when issuing your own license) and a known email."""
        if not self.license_payload:
            return False
        note = (self.license_payload.get("note") or "").lower()
        if "admin" in note or "owner" in note or "developer" in note:
            return True
        # Fallback: email recognized as admin
        admin_emails = {
            "khatar@intra.tunasgroup.com",
            "khatarmalayki21@gmail.com",
        }
        return (
            (self.license_payload.get("email") or "").lower()
            in admin_emails
        )

    def _open_license_manager(self):
        # Defensive double-check
        if not self._is_admin():
            return
        # The manager dialog needs to import only when used (avoids loading
        # admin code paths for non-admin users)
        try:
            from .license_manager_dialog import LicenseManagerDialog
            LicenseManagerDialog(self).exec_()
        except Exception as e:
            QMessageBox.critical(
                self, "License Manager", f"Could not open: {e}"
            )

    def _maybe_show_trial_banner(self):
        """If this is a trial license and expiry is within 7 days, show a
        persistent warning banner. Already-expired trials are also shown
        (the validate_license check at startup would normally catch them,
        but a license can become expired mid-session)."""
        if not licmod.is_trial(self.license_payload):
            return
        days = licmod.days_until_expiry(self.license_payload)
        if days is None:
            return
        if days < 0:
            self.banner.show_message(
                f"<b>Trial expired.</b> Please contact your admin to "
                f"upgrade. <a href='sync'>Sync now</a>",
                level="error", duration_ms=0,
            )
        elif days == 0:
            self.banner.show_message(
                f"<b>Trial expires today.</b> Contact your admin to extend. "
                f"<a href='sync'>Sync now</a>",
                level="warning", duration_ms=0,
            )
        elif days <= 7:
            self.banner.show_message(
                f"<b>Trial license:</b> {days} day{'s' if days != 1 else ''} "
                f"remaining. <a href='sync'>Sync now</a> after admin extends.",
                level="warning", duration_ms=0,
            )
        else:
            return
        # Wire the "Sync now" link to immediate refetch
        try:
            self.banner.label.linkActivated.disconnect()
        except TypeError:
            pass
        self.banner.label.linkActivated.connect(
            lambda _: self._refetch_license_from_worker()
        )

    def _open_settings(self):
        from .settings_dialog import SettingsDialog
        SettingsDialog(self).exec_()

    # ----- Backup / Restore -----
    def _open_backup_menu(self):
        menu = QMenu(self)
        # Email DB
        menu.addAction("Export backup (.pymail)", self._export_backup)
        menu.addAction("Import backup (.pymail)", self._import_backup)
        menu.addSeparator()
        # Contacts
        menu.addAction("Export contacts to CSV...", self._export_contacts)
        menu.addAction("Import contacts from CSV...", self._import_contacts)
        menu.addSeparator()
        info = menu.addAction(
            f"Database location: {database.DB_PATH}"
        )
        info.setEnabled(False)
        # Position menu near the button
        from PyQt5.QtGui import QCursor
        menu.exec_(QCursor.pos())

    def _export_contacts(self):
        from PyQt5.QtWidgets import QFileDialog
        from datetime import datetime
        from core import contacts as contacts_mod
        default_name = (
            f"pymail-contacts-{datetime.now().strftime('%Y%m%d')}.csv"
        )
        path, _ = QFileDialog.getSaveFileName(
            self, "Export contacts", default_name,
            "CSV file (*.csv);;All files (*)",
        )
        if not path:
            return
        try:
            count = contacts_mod.export_csv(path)
            QMessageBox.information(
                self, "Contacts exported",
                f"Exported {count} contact(s) to:\n{path}\n\n"
                f"This CSV can be imported by Outlook, Gmail, Apple "
                f"Contacts, or Thunderbird.",
            )
        except Exception as e:
            QMessageBox.critical(self, "Export failed", str(e))

    def _import_contacts(self):
        from PyQt5.QtWidgets import QFileDialog
        from core import contacts as contacts_mod
        path, _ = QFileDialog.getOpenFileName(
            self, "Import contacts", "",
            "CSV file (*.csv);;All files (*)",
        )
        if not path:
            return
        try:
            added, skipped = contacts_mod.import_csv(path)
            msg = f"Imported {added} contact(s)."
            if skipped:
                msg += f" Skipped {skipped} row(s) with no email."
            QMessageBox.information(self, "Contacts imported", msg)
        except Exception as e:
            QMessageBox.critical(
                self, "Import failed",
                f"{e}\n\n"
                f"Tips:\n"
                f"  - From Outlook: File → Open & Export → Import/Export "
                f"→ Export to a file → CSV.\n"
                f"  - From Gmail: Contacts → More → Export → "
                f"'Outlook CSV' format."
            )

    def _export_backup(self):
        from PyQt5.QtWidgets import QFileDialog
        from datetime import datetime
        import shutil
        default_name = (
            f"pymail-backup-{datetime.now().strftime('%Y%m%d-%H%M%S')}.pymail"
        )
        path, _ = QFileDialog.getSaveFileName(
            self, "Export RunLab Mail backup", default_name,
            "RunLab Mail backup (*.pymail);;All files (*)"
        )
        if not path:
            return
        try:
            # Close DB connections by completing any pending writes;
            # SQLite file copy is safe as long as no transaction is open.
            shutil.copy2(database.DB_PATH, path)
            size_mb = (database.DB_PATH and __import__("os").path.getsize(database.DB_PATH)) / 1024 / 1024
            QMessageBox.information(
                self, "Backup exported",
                f"Saved to:\n{path}\n\nSize: {size_mb:.1f} MB",
            )
        except Exception as e:
            QMessageBox.critical(self, "Export failed", str(e))

    def _import_backup(self):
        from PyQt5.QtWidgets import QFileDialog
        import shutil
        path, _ = QFileDialog.getOpenFileName(
            self, "Import RunLab Mail backup", "",
            "RunLab Mail backup (*.pymail);;Database files (*.db);;All files (*)"
        )
        if not path:
            return
        ret = QMessageBox.warning(
            self, "Replace current data?",
            "Importing this backup will REPLACE your current RunLab Mail data "
            "(all accounts and emails).\n\n"
            "A safety copy of your current data will be created next to it "
            "before replacing.\n\nProceed?",
            QMessageBox.Yes | QMessageBox.Cancel,
            QMessageBox.Cancel,
        )
        if ret != QMessageBox.Yes:
            return
        try:
            from datetime import datetime
            from pathlib import Path
            current = Path(database.DB_PATH)
            if current.is_file():
                stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
                safety = current.with_name(f"pymail.db.before-import-{stamp}")
                shutil.copy2(current, safety)
            shutil.copy2(path, current)
            QMessageBox.information(
                self, "Imported",
                "Backup imported. RunLab Mail will close now — please re-open it "
                "to load the imported data.",
            )
            from PyQt5.QtWidgets import QApplication
            QApplication.instance().quit()
        except Exception as e:
            QMessageBox.critical(self, "Import failed", str(e))

    def _recheck_license(self):
        if self._license_worker and self._license_worker.isRunning():
            return
        self._license_worker = LicenseCheckWorker()
        self._license_worker.revoked.connect(self._on_license_revoked)
        self._license_worker.start()

    def _on_license_revoked(self, err: str):
        self.banner.show_message(
            f"<b>License invalid:</b> {err} The application will close.",
            level="error",
            duration_ms=0,
        )
        QMessageBox.critical(
            self, "License revoked",
            f"{err}\n\nRunLab Mail will close now.",
        )
        # Force quit
        from PyQt5.QtWidgets import QApplication
        QApplication.instance().quit()
