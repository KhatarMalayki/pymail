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
    QMenu, QFrame, QStyle, QSizePolicy, QToolButton
)
from .ribbon_toolbar import RibbonToolbar, RibbonGroup
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
from .email_list import EmailListWidget, ROLE_EMAIL_ID, ROLE_UNREAD, ROLE_FLAGGED
from .view_bar import ViewBar


# ---------- Outbox flusher (background) ----------
class OutboxFlushWorker(QThread):
    log = pyqtSignal(str)
    sent_one = pyqtSignal(int, int)  # account_id, outbox_id (now sent)
    failed_one = pyqtSignal(int, str)  # outbox_id, error
    done = pyqtSignal(int, int)  # success_count, failure_count

    def __init__(self, account_id: int | None=None):
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
            err_lower = (err or "").lower()
            if "no license" in err_lower or "not installed" in err_lower:
                return
            self.revoked.emit(err)


# ---------- License auto-registration with the Worker ----------
class LicenseRegisterWorker(QThread):
    success = pyqtSignal(dict, str)  # payload, expires_at
    error = pyqtSignal(str)

    def __init__(self, email: str, name: str=""):
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
    from datetime import timezone
    days = int(account.get("junk_purge_days") or 0)
    if days <= 0:
        return 0
    cutoff = (_dt.datetime.now(timezone.utc) - _dt.timedelta(days=days)).isoformat()
    rows = database.list_old_junk(account["id"], cutoff)
    if not rows:
        database.set_junk_purge_last_run(account["id"], _dt.datetime.now(timezone.utc).isoformat())
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

    database.set_junk_purge_last_run(account["id"], _dt.datetime.now(timezone.utc).isoformat())
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

    def __init__(self, license_payload: dict | None=None):
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
        # Auto-update setup guard. On a brand-new machine the user is still
        # configuring their first account when the startup update check
        # fires. We must NOT download + auto-restart mid-setup (that wipes
        # the half-entered account and confuses the user). _setup_depth > 0
        # means an account dialog is open; _deferred_update_manifest holds an
        # update found during setup so we can install it once setup is done.
        self._setup_depth = 0
        self._deferred_update_manifest = None

        database.init_db()
        self._build_ui()
        self._refresh_accounts_tree()

        # Periodic auto-fetch every 5 minutes
        self.auto_timer = QTimer(self)
        self.auto_timer.timeout.connect(self._auto_fetch_all)
        self.auto_timer.start(5 * 60 * 1000)

        # Outbox is MANUAL-send: queued mail stays in the Outbox until the
        # user explicitly runs Send/Receive. This gives a chance to review or
        # edit a message before it actually goes out. (No periodic auto-flush.)
        self._outbox_worker = None

        # Send/Receive progress dialog state. Used to stop repeated clicks on
        # the Send/Receive button from stacking multiple progress dialogs and
        # launching concurrent sync runs (users tend to mash the button).
        self._sync_dialog = None
        self._sync_in_progress = False

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
        # Ribbon Toolbar (Outlook-style)
        self.ribbon = RibbonToolbar(self)
        st = self.style()
        
        # Home Tab
        home_tab = self.ribbon.add_tab("Home")
        
        # New Group
        new_group = home_tab.add_group("New")
        self._btn_new_email = new_group.add_button(
            "New Email", st.standardIcon(QStyle.SP_FileDialogNewFolder),
            self._compose_new, large=True,
            tooltip="Compose a new email (Ctrl+N)"
        )
        self._btn_new_email.setShortcut("Ctrl+N")
        
        # Send/Receive Group
        sendrecv_group = home_tab.add_group("Send/Receive")
        sendrecv_group.add_button(
            "Send/Receive", st.standardIcon(QStyle.SP_BrowserReload),
            self._fetch_current, large=True,
            tooltip="Fetch new emails and flush the Outbox"
        )
        
        # Search Group
        search_group = home_tab.add_group("Search")
        search_icon = QLabel("🔍")
        search_icon.setStyleSheet("font-size: 14px; color: #605e5c;")
        search_group.btn_layout.addWidget(search_icon)
        self.search_edit = QLineEdit()
        self.search_edit.setObjectName("searchInput")
        self.search_edit.setPlaceholderText("Search subject, sender, body...")
        self.search_edit.setMinimumWidth(280)
        self.search_edit.setMaximumWidth(420)
        self.search_edit.textChanged.connect(self._on_search_changed)
        search_group.btn_layout.addWidget(self.search_edit)
        
        # Account Group
        account_group = home_tab.add_group("Account")
        account_group.add_button(
            "Accounts", st.standardIcon(QStyle.SP_DialogOpenButton),
            self._open_accounts, tooltip="Manage email accounts"
        )
        account_group.add_button(
            "Backup", st.standardIcon(QStyle.SP_DriveHDIcon),
            self._open_backup_menu, tooltip="Export/import data"
        )
        
        # View Tab
        view_tab = self.ribbon.add_tab("View")
        
        # Settings Group
        settings_group = view_tab.add_group("Settings")
        settings_group.add_button(
            "Settings", st.standardIcon(QStyle.SP_FileDialogDetailedView),
            self._open_settings, large=True, tooltip="Application settings"
        )

        # Theme Group (light/dark toggle)
        from . import theme as theme_mod
        theme_group = view_tab.add_group("Theme")
        self._btn_theme = theme_group.add_button(
            "Dark Mode" if not theme_mod.is_dark() else "Light Mode",
            st.standardIcon(QStyle.SP_DesktopIcon),
            self._toggle_theme, large=True,
            tooltip="Switch between light and dark theme"
        )

        # Density Group (compact / cozy / comfortable)
        from core import config as _cfg
        density_group = view_tab.add_group("Density")
        self._density = _cfg.get("list_density", "comfortable")
        self._btn_density = density_group.add_button(
            self._density_label(self._density),
            st.standardIcon(QStyle.SP_FileDialogListView),
            self._cycle_density, large=True,
            tooltip="Change email list density (Compact / Cozy / Comfortable)"
        )
        
        # About Group
        about_group = view_tab.add_group("About")
        about_group.add_button(
            "About", st.standardIcon(QStyle.SP_MessageBoxInformation),
            self._open_about, tooltip="Version info"
        )
        self._btn_license_info = about_group.add_button(
            "License", st.standardIcon(QStyle.SP_FileDialogContentsView),
            self._show_license_info, tooltip="View license info (Shift+click for admin access)"
        )
        # Shift+click the License button reveals the hidden Admin tab.
        self._btn_license_info.installEventFilter(self)

        # Admin Tab — hidden by default. Revealed only when the developer
        # Shift+clicks the License button (handled in eventFilter). Regular
        # users never see it.
        self._admin_tab = self.ribbon.add_tab("Admin")
        admin_group = self._admin_tab.add_group("License")
        self._btn_license_manager = admin_group.add_button(
            "License Manager", st.standardIcon(QStyle.SP_FileDialogInfoView),
            self._open_license_manager, large=True,
            tooltip="Manage all licenses (requires admin passphrase)"
        )
        self._admin_tab_index = self.ribbon.indexOf(self._admin_tab)
        # Hide it now that it's been registered.
        self.ribbon.removeTab(self._admin_tab_index)
        self._admin_tab_visible = False
        
        home_tab.add_spacer()
        
        self.setMenuWidget(self.ribbon)

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
        self.email_list.itemDoubleClicked.connect(self._on_email_double_clicked)
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
        # Hold the auto-updater off while the welcome prompt + first account
        # setup is on screen (fresh machine). _add_account() guards itself
        # too, but the welcome box is modal and the startup update timer can
        # fire while it's open — so guard here as well.
        self._setup_depth += 1
        try:
            ret = QMessageBox.question(
                self, "Welcome to RunLab Mail",
                "No email account configured yet.\nWould you like to add one now?",
                QMessageBox.Yes | QMessageBox.No,
            )
            if ret == QMessageBox.Yes:
                self._add_account()
        finally:
            self._setup_depth -= 1
        self._resume_deferred_update_if_idle()

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
        if not data:
            return
        menu = QMenu(self)
        if data[0] == "account":
            acc_id = data[1]
            menu.addAction("Send / Receive", lambda: self._fetch_account(acc_id))
            menu.addAction("Edit account...", lambda: self._edit_account(acc_id))
            menu.addAction("Delete account", lambda: self._delete_account(acc_id))
        elif data[0] == "folder":
            _, acc_id, folder = data
            menu.addAction(
                "Mark all as read",
                lambda: self._mark_all_read_for(acc_id, folder),
            )
        else:
            return
        menu.exec_(self.tree.viewport().mapToGlobal(pos))

    # ----- Email list -----
    PAGE_SIZE = 200

    def _refresh_email_list(self):
        self.email_list.reset_threads(self.current_folder)
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
            self._pending_thread_registry = {}
            emails = self._apply_grouping(emails)
            self._loaded_count = self._total_count  # treat as fully loaded
            # Register thread children so heads can expand on click.
            for key, children in getattr(
                    self, "_pending_thread_registry", {}).items():
                self.email_list.register_thread(key, children)
        else:
            self._loaded_count += len(emails)

        for e in emails:
            self.email_list.add_email(e, self.current_folder)

    def _apply_grouping(self, emails: list) -> list:
        """Group emails into conversation threads using RFC 5322 headers
        (In-Reply-To / References) with subject as fallback. Each group's
        head row shows: (subject, "↳ N messages") and an arrow icon to
        indicate it's a thread.

        Within a group, the most-recent email is shown as the head; the
        thread is "collapsed" — clicking opens the latest message."""
        import re

        # ----- Build message_id -> email lookup
        by_msgid: dict[str, dict] = {}
        for e in emails:
            mid = (e.get("message_id") or "").strip("<>").strip()
            if mid:
                by_msgid.setdefault(mid, e)

        # ----- Union-Find for threading
        parent: dict[int, int] = {}

        def find(i):
            while parent.get(i, i) != i:
                parent[i] = parent.get(parent[i], parent[i])
                i = parent[i]
            return i

        def union(a, b):
            ra, rb = find(a), find(b)
            if ra != rb:
                parent[rb] = ra

        # Initialize each email as its own thread
        for e in emails:
            parent[e["id"]] = e["id"]

        # Link by In-Reply-To and References (split on whitespace, take all)
        ref_re = re.compile(r"<[^>]+>")
        for e in emails:
            related = []
            ir = (e.get("in_reply_to") or "").strip()
            if ir:
                related.extend(ref_re.findall(ir) or [ir])
            refs = (e.get("references_hdr") or "").strip()
            if refs:
                related.extend(ref_re.findall(refs))
            for token in related:
                token_clean = token.strip("<>").strip()
                parent_email = by_msgid.get(token_clean)
                if parent_email and parent_email["id"] != e["id"]:
                    union(parent_email["id"], e["id"])

        # ----- Subject-based grouping (conversation topic).
        # Primary threading uses Message-ID/References above. As a fallback —
        # and to match Outlook/eM Client "conversation" behavior — we also
        # group messages that share the same normalized subject (after
        # stripping any Re:/Fwd: prefixes). This catches threads whose headers
        # were stripped by mail intermediaries (common in corporate setups)
        # and conversations where the original lives in another folder.
        re_prefix = re.compile(
            r"^\s*(?:re|fw|fwd|aw|sv)\s*:\s*",
            re.IGNORECASE,
        )
        roots_by_subject: dict[str, int] = {}
        for e in emails:
            raw_subj = (e.get("subject") or "").strip()
            stripped = re_prefix.sub("", raw_subj).strip().lower()
            # Collapse repeated prefixes ("re: re: fwd:") fully.
            while True:
                nxt = re_prefix.sub("", stripped).strip()
                if nxt == stripped:
                    break
                stripped = nxt
            if not stripped or stripped == "(no subject)":
                # Don't merge empty/placeholder subjects into one giant blob.
                continue
            root_id = roots_by_subject.get(stripped)
            if root_id is None:
                # First message seen with this subject becomes the thread root.
                roots_by_subject[stripped] = e["id"]
            elif root_id != e["id"]:
                union(root_id, e["id"])
            else:
                # First non-reply seen with this subject becomes the root.
                # Subsequent non-reply messages with the same subject are
                # NOT linked — they're treated as independent (e.g. weekly
                # newsletter with the same subject every issue).
                roots_by_subject.setdefault(stripped, e["id"])

        # ----- Collect threads
        threads: dict[int, list] = {}
        order: list[int] = []
        for e in emails:
            root = find(e["id"])
            if root not in threads:
                threads[root] = []
                order.append(root)
            threads[root].append(e)

        # ----- Build the visible head row per thread
        result = []
        # Reset thread registration on the list widget for this refresh.
        thread_registry = {}
        for root in order:
            items = threads[root]
            # Most-recent first (preserve current sort order)
            head = dict(items[0])
            count = len(items)
            if count > 1:
                # Strip the "Re: / Fwd:" so the displayed subject reads cleanly.
                base_subj = head.get("subject") or "(no subject)"
                clean = re.sub(
                    r"^(?:\s*(?:re|fw|fwd|aw|sv)\s*:\s*)+", "",
                    base_subj, flags=re.IGNORECASE,
                ).strip() or "(no subject)"
                head["subject"] = f"{clean}  ({count})"
                # Mark unread if any in the thread is unread
                if any(not it.get("is_read") for it in items):
                    head["is_read"] = 0
                # Mark has_attachments if any in the thread has them
                if any(it.get("has_attachments") for it in items):
                    head["has_attachments"] = 1
                # Show a flag on the head if any message in the thread is flagged
                if any(it.get("is_flagged") for it in items):
                    head["is_flagged"] = 1
                # Threading metadata for the list widget / delegate.
                head["_thread_role"] = "head"
                head["_thread_count"] = count
                head["_thread_key"] = root
                head["_thread_expanded"] = False
                # Children = every message except the head (the latest).
                thread_registry[root] = [dict(it) for it in items[1:]]
            result.append(head)
        # Hand the children map to the list widget so it can expand on click.
        self._pending_thread_registry = thread_registry
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

    def _on_email_double_clicked(self, item):
        """Open the email in a popup window (Outlook-style detail view)."""
        from .email_window import EmailWindow
        email_id = item.data(ROLE_EMAIL_ID)
        if email_id is None:
            return
        # Drafts: open in compose dialog
        if self.current_folder == "drafts":
            self._open_draft(email_id)
            return
        # Outbox: open in compose dialog so the user can fix a typo'd
        # recipient (or anything else) before the message goes out.
        if self.current_folder == "outbox":
            self._edit_outbox_entry(email_id)
            return
        # Reuse existing window if user already opened this email

        if not hasattr(self, "_email_windows"):
            self._email_windows = []
        # Clean up closed windows
        self._email_windows = [w for w in self._email_windows if w.isVisible()]
        for w in self._email_windows:
            if getattr(w, "_email_id", None) == email_id:
                w.raise_()
                w.activateWindow()
                return
        win = EmailWindow(email_id, parent=self)
        win.compose_requested.connect(self._on_compose_from_email_window)
        self._email_windows.append(win)
        win.show()

    def _on_compose_from_email_window(self, kind: str, email_id: int):
        """Reply/Reply-All/Forward triggered from a detached EmailWindow.
        kind = 'reply' | 'reply_all' | 'forward'."""
        email = database.get_email(email_id)
        if email:
            self._reply_or_forward(email, kind)

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
        # For a collapsed thread head, actions should cover the whole
        # conversation (head + children), otherwise "mark as read" on a
        # thread appears to do nothing because unread children remain.
        thread_ids = self.email_list.email_ids_for_item(item)
        menu = QMenu(self)
        menu.addAction("Open", lambda: self.viewer.show_email(email_id))
        menu.addSeparator()
        # Outbox: stuck/failed mail management (edit + resend + remove)
        if self.current_folder == "outbox":
            menu.addAction("Edit ✏", lambda: self._edit_outbox_entry(email_id))
            menu.addAction("Resend now ↻", self._flush_outbox_now)
            menu.addAction("Remove from Outbox 🗑",
                           lambda: self._delete_outbox_entry(email_id))
            menu.exec_(self.email_list.viewport().mapToGlobal(pos))
            return

        menu.addAction("Mark as read", lambda: self._mark_read(thread_ids, True))
        menu.addAction("Mark as unread", lambda: self._mark_read(thread_ids, False))
        menu.addAction("Mark all as read", self._mark_all_read)
        menu.addSeparator()
        # Follow-up flag (Outlook-style). Toggle based on current state.
        is_flagged = bool(item.data(ROLE_FLAGGED))
        if is_flagged:
            menu.addAction("⚑  Clear flag",
                           lambda: self._set_flag(thread_ids, False))
        else:
            menu.addAction("🚩  Flag for follow-up",
                           lambda: self._set_flag(thread_ids, True))
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

    def _server_delete_if_imap(self, email_ids: list[int], reason: str=""):
        """If the given local emails came from IMAP (Junk folder), also
        delete them on the server. POP3-origin emails are silently skipped.
        Best-effort: errors are logged to the status bar but don't block
        local deletion."""
        if not email_ids:
            return
        # Group by account so we can do a single IMAP session per account
        from collections import defaultdict
        per_account = defaultdict(list)
        for eid in email_ids:
            row = database.get_email(eid)
            if not row:
                continue
            uidl = row.get("uidl") or ""
            if not uidl.startswith(imap_client.IMAP_UID_PREFIX):
                continue  # POP3 origin — server delete impossible
            per_account[row["account_id"]].append(uidl)
        for acc_id, uidls in per_account.items():
            acc = database.get_account(acc_id)
            if not acc or not acc.get("imap_enabled"):
                continue
            try:
                n = imap_client.purge_junk_on_server(acc, uidls)
                if n and reason:
                    self.status_label.setText(f"{reason} ({n} also deleted on server)")
            except Exception as e:
                self.status_label.setText(f"Server delete failed: {e}")

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
        # If from IMAP Junk: also remove from server's Junk folder.
        # The local copy stays in our Inbox so user can still see it.
        self._server_delete_if_imap([email_id], reason="Moved to Inbox")
        database.move_to_inbox(email_id)
        self._refresh_email_list()
        self._update_folder_counts()

    def _mark_read(self, email_id, read):
        ids = email_id if isinstance(email_id, (list, tuple)) else [email_id]
        for eid in ids:
            database.mark_read(eid, read)
        self._refresh_email_list()
        self._update_folder_counts()

    def _set_flag(self, email_id, flagged):
        ids = email_id if isinstance(email_id, (list, tuple)) else [email_id]
        for eid in ids:
            database.set_flagged(eid, flagged)
        # Instant visual update on the visible (head) row without a reload.
        if ids:
            self.email_list.set_flagged_visual(ids[0], flagged)

    def _mark_all_read(self):
        if self.current_account_id is None:
            return
        self._mark_all_read_for(self.current_account_id, self.current_folder)

    def _mark_all_read_for(self, account_id, folder):
        if account_id is None:
            return
        n = database.mark_all_read(account_id, folder, True)
        # Refresh the list only if we're viewing that folder right now.
        if account_id == self.current_account_id and folder == self.current_folder:
            self._refresh_email_list()
        self._update_folder_counts()
        self.status_label.setText(f"Marked {n} message(s) as read.")

    def _trash_email(self, email_id):
        # If email came from IMAP Junk, move-to-trash is effectively a server
        # delete (the server has no local "Trash"; we treat trash as gone).
        self._server_delete_if_imap([email_id], reason="Moved to Trash")
        database.move_to_trash(email_id)
        self._refresh_email_list()
        self._update_folder_counts()

    def _delete_email(self, email_id):
        if QMessageBox.question(self, "Delete", "Permanently delete this email?") == QMessageBox.Yes:
            self._server_delete_if_imap([email_id], reason="Permanently deleted")
            database.delete_email(email_id)
            self._refresh_email_list()
            self._update_folder_counts()

    # ----- Account actions -----
    def _open_accounts(self):
        """Toolbar 'Accounts' button: list view of all accounts."""
        self._setup_depth += 1
        try:
            dlg = AccountsListDialog(self)
            dlg.exec_()
        finally:
            self._setup_depth -= 1
        self._refresh_accounts_tree()
        self._resume_deferred_update_if_idle()

    def _add_account(self):
        # Guard the auto-updater: a brand-new machine is configuring its
        # first account here, and we must not download + restart mid-setup.
        self._setup_depth += 1
        try:
            dlg = AccountDialog(self)
            accepted = dlg.exec_()
        finally:
            self._setup_depth -= 1
        if accepted:
            self._refresh_accounts_tree()
        self._resume_deferred_update_if_idle()

    def _edit_account(self, account_id):
        acc = database.get_account(account_id)
        if not acc:
            return
        self._setup_depth += 1
        try:
            dlg = AccountDialog(self, account=acc)
            accepted = dlg.exec_()
        finally:
            self._setup_depth -= 1
        if accepted:
            self._refresh_accounts_tree()
        self._resume_deferred_update_if_idle()

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
        # Silent background fetch — no dialog, just status bar updates.
        # NOTE: this only RECEIVES mail. It deliberately does NOT flush the
        # Outbox — queued mail is sent only when the user runs Send/Receive,
        # so a mistakenly-sent email can still be edited beforehand.
        for acc in database.list_accounts():
            self._fetch_account(acc["id"], silent=True)

    def _start_sync_with_dialog(self, account_ids: list[int]):
        """Run a Send/Receive cycle for the given accounts with a visible
        progress + log dialog (Outlook-style)."""
        # Guard against the user mashing Send/Receive: if a sync is already
        # running, just resurface the existing progress dialog instead of
        # spawning another one (which would stack dialogs and launch
        # concurrent fetch runs against the same accounts).
        if self._sync_in_progress:
            if self._sync_dialog is not None:
                self._sync_dialog.show()
                self._sync_dialog.raise_()
                self._sync_dialog.activateWindow()
            else:
                self.status_label.setText("Send/Receive already in progress...")
            return

        from .sync_progress_dialog import SyncProgressDialog
        dlg = SyncProgressDialog(self)
        self._sync_dialog = dlg
        self._sync_in_progress = True

        # Track account → worker so we can wire up callbacks per task
        self._sync_remaining = 0
        self._sync_workers = []

        accounts = [database.get_account(a) for a in account_ids]
        accounts = [a for a in accounts if a]
        if not accounts:
            # Nothing to sync — release the guard so the button works again.
            self._sync_in_progress = False
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
            self._sync_in_progress = False  # sync finished — allow a new run
            return
        if self._sync_dialog.is_cancelled:
            self._sync_dialog.all_done()
            self._sync_in_progress = False  # cancelled — allow a new run
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
        """Outbox flush during a Send/Receive cycle.

        Tries to send queued mail. Unlike a true "silent" flush, if any email
        fails to send we DO surface a bounce log at the end — otherwise the
        user just sees mail stuck in the Outbox with no explanation (the exact
        complaint behind this fix). Successful sends still happen quietly.
        """
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
        self._outbox_errors = []  # collect (outbox_id, error) for bounce log
        self._outbox_worker = OutboxFlushWorker()
        self._outbox_worker.sent_one.connect(self._on_outbox_sent)
        self._outbox_worker.failed_one.connect(self._on_outbox_failed)
        self._outbox_worker.done.connect(self._on_outbox_flush_done)
        self._outbox_worker.start()

    def _flush_outbox_now(self):
        """Manual: triggered by user pressing toolbar Send/Receive on Outbox."""
        if self._outbox_worker and self._outbox_worker.isRunning():
            self.status_label.setText("Outbox is already flushing...")
            return
        self.status_label.setText("Flushing outbox...")
        self._outbox_errors = []  # collect (outbox_id, error) for bounce log
        self._outbox_worker = OutboxFlushWorker()
        self._outbox_worker.sent_one.connect(self._on_outbox_sent)
        self._outbox_worker.failed_one.connect(self._on_outbox_failed)
        self._outbox_worker.done.connect(self._on_outbox_flush_done_manual)
        self._outbox_worker.start()

    def _on_outbox_failed(self, outbox_id: int, error: str):
        """Record a per-email send failure so we can show a bounce log."""
        if not hasattr(self, "_outbox_errors"):
            self._outbox_errors = []
        self._outbox_errors.append((outbox_id, error))

    def _delete_outbox_entry(self, email_id: int):
        """Remove a stuck/failed email from the Outbox (user-confirmed)."""
        if QMessageBox.question(
            self, "Remove from Outbox",
            "Remove this email from the Outbox?\n"
            "It will NOT be sent and cannot be recovered.",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        ) != QMessageBox.Yes:
            return
        try:
            database.delete_outbox(email_id)
        except Exception as e:
            QMessageBox.critical(self, "Remove failed", str(e))
            return
        self._refresh_email_list()
        self._update_folder_counts()
        self._refresh_accounts_tree()
        self.status_label.setText("Removed from Outbox.")

    def _edit_outbox_entry(self, email_id: int):
        """Reopen a queued/stuck Outbox email in the compose dialog so the
        user can fix a typo'd recipient (or anything else) before it goes
        out. We parse the stored raw MIME back into editable fields, remove
        the Outbox entry, and let the user re-queue it on Send.

        This fixes the case where a message with a wrong recipient lands in
        the Outbox and previously could only be resent (and bounce again) or
        deleted — never corrected."""
        row = database.get_email(email_id)
        if not row:
            return
        account_id = row.get("account_id")

        # Recover the full editable content from the raw MIME bytes (keeps
        # the HTML body, embedded images, and attachments intact). Fall back
        # to the stored plain-text fields if the raw payload is missing.
        prefill = {
            "to": row.get("recipients") or "",
            "cc": row.get("cc") or "",
            "bcc": row.get("bcc") or "",
            "subject": row.get("subject") or "",
            "body": row.get("body_plain") or "",
            # Don't re-insert the signature — the body already contains it.
            "suppress_signature": True,
        }
        try:
            raw = database.get_outbox_raw(email_id)
        except Exception:
            raw = None
        if raw:
            try:
                from core.mail_parser import parse_message
                parsed, atts = parse_message(raw)
                prefill["to"] = parsed.get("to") or prefill["to"]
                prefill["cc"] = parsed.get("cc") or prefill["cc"]
                prefill["bcc"] = parsed.get("bcc") or prefill["bcc"]
                prefill["subject"] = parsed.get("subject") or prefill["subject"]
                if parsed.get("body_html"):
                    prefill["body_html"] = parsed["body_html"]
                if parsed.get("body_plain"):
                    prefill["body"] = parsed["body_plain"]
                if atts:
                    prefill["forwarded_attachments"] = atts
            except Exception:
                pass

        # Remove the stuck entry so it isn't sent twice. If the user closes
        # the dialog without sending, the compose auto-save keeps it as a
        # draft, so nothing is lost.
        try:
            database.delete_outbox(email_id)
        except Exception as e:
            QMessageBox.critical(self, "Edit failed", str(e))
            return

        dlg = ComposeDialog(self, account_id=account_id, prefill=prefill)
        self._track_compose(dlg)
        dlg.sent.connect(self._on_email_sent)
        dlg.show()

        self._refresh_email_list()
        self._update_folder_counts()
        self._refresh_accounts_tree()
        self.status_label.setText("Editing email from Outbox.")

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
        if failed:
            self.status_label.setText(
                f"Outbox: sent {sent}, failed {failed} (still in Outbox)."
            )
        self._refresh_accounts_tree()
        if self.current_folder in ("outbox", "sent"):
            self._refresh_email_list()
        # Surface a bounce log so the user knows WHY mail is stuck in the
        # Outbox after a Send/Receive (previously failures were swallowed
        # silently, leaving them confused about un-sent mail).
        errors = getattr(self, "_outbox_errors", [])
        if errors:
            self._show_outbox_error_log(errors)
            self._outbox_errors = []

    def _on_outbox_flush_done_manual(self, sent: int, failed: int):
        msg = f"Outbox flush complete. Sent: {sent}"
        if failed:
            msg += f", failed: {failed} (still in Outbox, will retry)"
        self.status_label.setText(msg)
        self._refresh_accounts_tree()
        if self.current_folder in ("outbox", "sent"):
            self._refresh_email_list()
        # Show a bounce/error log so the user knows WHY mail didn't go out
        # and which recipients were refused.
        errors = getattr(self, "_outbox_errors", [])
        if errors:
            self._show_outbox_error_log(errors)

    def _show_outbox_error_log(self, errors: list):
        """Display a detailed log of failed Outbox sends (bounce log).

        errors: list of (outbox_id, error_message) tuples.
        """
        from PyQt5.QtWidgets import (
            QDialog, QVBoxLayout, QLabel, QTextEdit, QDialogButtonBox
        )
        dlg = QDialog(self)
        dlg.setWindowTitle("Outbox — Send Failures")
        dlg.resize(640, 420)
        layout = QVBoxLayout(dlg)
        header = QLabel(
            f"<b>{len(errors)} email(s) could not be sent.</b><br>"
            "They remain in the Outbox. Common causes: wrong recipient "
            "address, SMTP auth/connection failure, or the server rejecting "
            "a recipient. Details below:"
        )
        header.setWordWrap(True)
        layout.addWidget(header)
        log = QTextEdit()
        log.setReadOnly(True)
        lines = []
        for idx, (outbox_id, err) in enumerate(errors, 1):
            subject = ""
            try:
                row = database.get_email(outbox_id)
                if row:
                    subject = row.get("subject") or "(no subject)"
            except Exception:
                pass
            lines.append(
                f"#{idx}  {subject}\n"
                f"     {err}\n"
            )
        log.setPlainText("\n".join(lines))
        layout.addWidget(log, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok)
        buttons.accepted.connect(dlg.accept)
        layout.addWidget(buttons)
        dlg.exec_()

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
        # Soft failure — the app still works for POP3/SMTP and we retry
        # auto-registration on the next successful sync. But we no longer
        # fail completely silently: a network/firewall block here is the
        # #1 cause of "kok lisensinya nggak ke-register?" so we surface a
        # clear, actionable banner ONCE (not on every retry tick), with the
        # Machine ID the user can send to the admin as a fallback.
        self.update_status_label.setText(
            "License registration failed (will retry)"
        )
        self.update_status_label.setToolTip(err)

        # Only show the actionable banner the first time, so repeated sync
        # cycles don't keep popping it back up while the user is working.
        if getattr(self, "_register_error_notified", False):
            return
        self._register_error_notified = True

        # Grab this machine's ID so the user can hand it to the admin if the
        # Worker stays unreachable (firewall/proxy blocks outbound HTTPS).
        try:
            machine_id = licmod.get_machine_id()
        except Exception:
            machine_id = ""

        is_network = "network error" in (err or "").lower()
        if is_network:
            detail = (
                "RunLab Mail tidak bisa menghubungi server lisensi "
                "(kemungkinan firewall/proxy memblokir koneksi). "
                "Aplikasi tetap bisa dipakai; registrasi akan dicoba lagi "
                "otomatis saat Send/Receive berikutnya."
            )
        else:
            detail = (
                "Registrasi lisensi gagal. Aplikasi tetap bisa dipakai dan "
                "akan dicoba lagi otomatis."
            )
        mid_part = (
            f" Kalau tetap gagal, kirim Machine ID ini ke admin: "
            f"<b>{machine_id}</b>."
            if machine_id else ""
        )
        self.banner.show_message(
            f"<b>⚠ Lisensi belum ter-registrasi.</b> {detail}{mid_part}",
            level="warning",
            duration_ms=0,
        )


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
            "body_html": d.get("body_html") or None,
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
        # Queue-first design: a composed email goes to the Outbox and stays
        # there until the user runs Send/Receive (manual flush). We do NOT
        # auto-flush here — that would send immediately, which the user does
        # not expect from an Outbox workflow.
        if account_id == self.current_account_id and self.current_folder in ("sent", "outbox"):
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

        try:
            dlg = ComposeDialog(
                self, account_id=self.current_account_id, prefill=prefill
            )
            self._track_compose(dlg)
            dlg.sent.connect(self._on_email_sent)
            dlg.show()
        except Exception as e:
            import traceback
            QMessageBox.critical(
                self, "Error",
                f"Failed to open compose dialog:\n{str(e)}\n\n"
                f"{traceback.format_exc()}"
            )

    @staticmethod
    def _prefix_subject(subject, prefix):
        if subject.lower().startswith(prefix.lower()):
            return subject
        return f"{prefix}{subject}"

    @staticmethod
    def _quote_body_html(email) -> str:
        """Build the quoted body for a reply or forward.

        Outlook-style separator block: a thin gray rule, then a tinted
        info box with the original sender/date/subject in muted color,
        then the original HTML body inset with a left accent border.
        """
        import html as html_lib
        sender = email.get("sender") or ""
        recipients = email.get("recipients") or ""
        date = email.get("date_sent") or email.get("date_received") or ""
        subject = email.get("subject") or "(no subject)"
        body_html = email.get("body_html") or ""
        body_plain = email.get("body_plain") or ""

        # Friendlier date label
        try:
            from datetime import datetime
            dt = datetime.fromisoformat(date.replace("Z", "").replace("+00:00", ""))
            date_pretty = dt.strftime("%a, %d %b %Y, %H:%M")
        except Exception:
            date_pretty = date

        # Reply separator (Outlook-style):
        #   - 24px breathing room above
        #   - thin horizontal rule (real <hr>; Qt rich text ignores
        #     border-top on a div, so a styled <hr> is what actually paints)
        #   - light gray box with bold field labels
        header = (
            "<div style=\"margin:24px 0 0 0;\">"
            "<hr style=\"border:none;border-top:1px solid #c8c6c4;"
            "margin:0 0 12px 0;\" />"
            "<div style=\"font-family:'Segoe UI','Calibri',sans-serif;"
            "font-size:10pt;color:#444;background:#f7f7f7;"
            "border-left:3px solid #0078d4;padding:10px 14px;"
            "border-radius:2px;line-height:1.5;\">"
            f"<div><b style=\"color:#222;\">From:</b> "
            f"{html_lib.escape(sender)}</div>"
            f"<div><b style=\"color:#222;\">Sent:</b> "
            f"{html_lib.escape(date_pretty)}</div>"
            f"<div><b style=\"color:#222;\">To:</b> "
            f"{html_lib.escape(recipients)}</div>"
        )
        cc = email.get("cc")
        if cc:
            header += (
                f"<div><b style=\"color:#222;\">Cc:</b> "
                f"{html_lib.escape(cc)}</div>"
            )
        header += (
            f"<div><b style=\"color:#222;\">Subject:</b> "
            f"{html_lib.escape(subject)}</div>"
            "</div>"
            "</div>"
        )

        # Body block — use HTML if we have it, otherwise wrap plain text
        if body_html:
            inner = body_html
        else:
            escaped = html_lib.escape(body_plain)
            inner = (
                "<pre style=\"font-family:'Segoe UI','Calibri',sans-serif;"
                "font-size:10pt;white-space:pre-wrap;margin:0;\">"
                f"{escaped}</pre>"
            )

        # Original body with subtle left accent
        return header + (
            "<div style=\"margin:14px 0 0 0;padding:8px 0 0 14px;"
            "border-left:2px solid #e1e1e1;\">"
            +inner + "</div>"
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
    def _check_for_updates(self, silent: bool=True):
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
        # Defer if the user is mid-setup (account dialog open). A brand-new
        # machine downloads + auto-restarts here otherwise, wiping the
        # half-entered account and yanking the app out from under the user.
        # We stash the manifest and resume once the dialog closes.
        if self._setup_depth > 0:
            self._deferred_update_manifest = manifest
            self.update_status_label.setText(
                f"Update {version} ready — will install after setup"
            )
            return
        # Auto-download in background. Status shown in dedicated label that
        # never gets overwritten by fetch progress.
        self.update_status_label.setText(f"⬇ Downloading update {version}...")
        self._auto_install(manifest)

    def _resume_deferred_update_if_idle(self):
        """Kick off any update that was deferred while an account dialog was
        open. No-ops if setup is still in progress (nested dialogs) or no
        update is pending."""
        if self._setup_depth > 0:
            return
        manifest = self._deferred_update_manifest
        if not manifest:
            return
        self._deferred_update_manifest = None
        self._on_update_found(manifest)

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
        dlg = LicenseInfoDialog(self.license_payload, self)
        dlg.exec_()
        # If the user pasted a new key, adopt it for this session.
        if getattr(dlg, "_new_payload", None):
            self.license_payload = dlg._new_payload

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

    # SHA-256 of the admin passphrase. To change: run
    #   python -c "import hashlib; print(hashlib.sha256(b'YOUR_PASS').hexdigest())"
    # and replace the value below.
    _ADMIN_PASS_HASH = "f6f99ad5794eeebf16794908efb39e0c0944adcf57c0bc6b3d041a70d755804c"

    def _open_license_manager(self):
        import hashlib
        from PyQt5.QtWidgets import QInputDialog, QLineEdit

        # Check if we've already verified this session
        if getattr(self, "_admin_unlocked", False):
            self._launch_license_manager()
            return

        phrase, ok = QInputDialog.getText(
            self, "Admin access",
            "Enter admin passphrase:",
            QLineEdit.Password,
        )
        if not ok:
            return
        digest = hashlib.sha256(phrase.encode()).hexdigest()
        if digest != self._ADMIN_PASS_HASH:
            QMessageBox.warning(self, "Access denied", "Incorrect passphrase.")
            return
        self._admin_unlocked = True
        self._launch_license_manager()

    def _launch_license_manager(self):
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

    def _density_label(self, density: str) -> str:
        return {
            "compact": "Compact",
            "cozy": "Cozy",
            "comfortable": "Comfortable",
        }.get(density, "Comfortable")

    def _cycle_density(self):
        """Cycle list density: comfortable -> cozy -> compact -> comfortable."""
        from core import config
        order = ["comfortable", "cozy", "compact"]
        try:
            cur = self.email_list.current_density()
        except Exception:
            cur = self._density
        nxt = order[(order.index(cur) + 1) % len(order)] if cur in order else "comfortable"
        self._density = nxt
        self.email_list.set_density(nxt)
        config.set_value("list_density", nxt)
        if getattr(self, "_btn_density", None) is not None:
            self._btn_density.setText(self._density_label(nxt))
        self.status_label.setText(f"List density: {self._density_label(nxt)}")

    def _toggle_theme(self):
        """Switch between light and dark theme and re-skin the whole app live."""
        from . import theme as theme_mod
        from PyQt5.QtWidgets import QApplication
        new_mode = theme_mod.toggle_mode()
        # Re-apply the global stylesheet for the new palette.
        theme_mod.apply_theme(QApplication.instance())
        # Update the toggle button label.
        if getattr(self, "_btn_theme", None) is not None:
            self._btn_theme.setText(
                "Light Mode" if new_mode == "dark" else "Dark Mode"
            )
        # Rebuild widgets that bake colors into their own stylesheets.
        self._reskin_dynamic_widgets()
        # Repaint custom-painted views (email-list delegate reads the theme).
        try:
            self.email_list.viewport().update()
        except Exception:
            pass
        self.update()

    def _reskin_dynamic_widgets(self):
        """Re-apply stylesheets on widgets that build their own QSS from theme
        tokens (they don't auto-update when the global stylesheet changes)."""
        # Ribbon tab bar
        rb = getattr(self, "ribbon", None)
        if rb is not None and hasattr(rb, "apply_theme"):
            try:
                rb.apply_theme()
            except Exception:
                pass
        # ViewBar
        vb = getattr(self, "view_bar", None)
        if vb is not None and hasattr(vb, "apply_theme"):
            try:
                vb.apply_theme()
            except Exception:
                pass
        # Email viewer header/labels
        ev = getattr(self, "viewer", None)
        if ev is not None and hasattr(ev, "apply_theme"):
            try:
                ev.apply_theme()
            except Exception:
                pass

    # ----- Backup / Restore -----
    def _open_backup_menu(self):
        menu = QMenu(self)
        # Import from other mail apps
        menu.addAction("Import from Outlook / Live Mail / eM Client...",
                       self._open_import_dialog)
        menu.addSeparator()
        # Email DB
        menu.addAction("Export backup file...", self._export_backup)
        menu.addAction("Import backup file...", self._import_backup)
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

    def _open_import_dialog(self):
        from .import_dialog import ImportDialog
        dlg = ImportDialog(self, current_account_id=self.current_account_id)
        dlg.imported.connect(self._on_import_done)
        dlg.exec_()

    def _on_import_done(self):
        # Refresh accounts (a new "Imported Mail" account may have appeared)
        # and the current email list so imported messages show immediately.
        self._refresh_accounts_tree()
        self._refresh_email_list()
        self._update_folder_counts()

    def _export_contacts(self):
        from PyQt5.QtWidgets import QFileDialog
        from datetime import datetime
        from core import contacts as contacts_mod
        default_name = (
            f"runlabmail-contacts-{datetime.now().strftime('%Y%m%d')}.csv"
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
            f"runlabmail-backup-{datetime.now().strftime('%Y%m%d-%H%M%S')}.runlabmail"
        )
        path, _ = QFileDialog.getSaveFileName(
            self, "Export RunLab Mail backup", default_name,
            "RunLab Mail backup (*.runlabmail *.pymail);;All files (*)"
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
            "RunLab Mail backup (*.runlabmail *.pymail);;Database files (*.db);;All files (*)"
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
                safety = current.with_name(f"runlabmail.db.before-import-{stamp}")
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
        err_lower = err.lower()
        if "no license" in err_lower or "not installed" in err_lower:
            title = "No license installed"
            msg = (
                "No license is installed on this machine.\n\n"
                "Please ask your administrator to issue a license for this device.\n\n"
                "RunLab Mail will close now."
            )
        elif "expired" in err_lower:
            title = "License expired"
            msg = f"{err}\n\nPlease contact your administrator to renew.\n\nRunLab Mail will close now."
        else:
            title = "License revoked"
            msg = f"{err}\n\nRunLab Mail will close now."
        QMessageBox.critical(self, title, msg)
        # Force quit
        from PyQt5.QtWidgets import QApplication
        QApplication.instance().quit()

    def eventFilter(self, obj, event):
        from PyQt5.QtCore import QEvent, Qt
        if event.type() == QEvent.MouseButtonPress:
            if (obj is getattr(self, "_btn_license_info", None)
                    and event.button() == Qt.LeftButton
                    and (event.modifiers() & Qt.ShiftModifier)):
                # Shift+click on License: reveal the hidden Admin tab and
                # prompt for the admin passphrase.
                self._reveal_admin_tab()
                self._open_license_manager()
                return True  # consume; don't also open the License info dialog
        return super().eventFilter(obj, event)

    def _reveal_admin_tab(self):
        """Show the Admin ribbon tab (hidden by default) and switch to it."""
        if getattr(self, "_admin_tab_visible", False):
            # Already visible — just focus it.
            idx = self.ribbon.indexOf(self._admin_tab)
            if idx >= 0:
                self.ribbon.setCurrentIndex(idx)
            return
        self.ribbon.addTab(self._admin_tab, "Admin")
        self._admin_tab_visible = True
        idx = self.ribbon.indexOf(self._admin_tab)
        if idx >= 0:
            self.ribbon.setCurrentIndex(idx)
