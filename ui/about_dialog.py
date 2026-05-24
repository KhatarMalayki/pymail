"""
About / Version dialog with manual update check.
"""
from datetime import datetime
from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QFormLayout, QLabel, QPushButton,
    QProgressBar, QTextEdit, QFrame, QSizePolicy, QApplication,
)
from PyQt5.QtCore import Qt, QThread, pyqtSignal
from core import updater
from core.version import __version__


class _CheckWorker(QThread):
    found = pyqtSignal(dict)
    none = pyqtSignal()
    err = pyqtSignal(str)

    def run(self):
        outcome, data = updater.check_now()
        if outcome == "update":
            self.found.emit(data)
        elif outcome == "ok":
            self.none.emit()
        else:  # "error"
            self.err.emit(str(data) if data else "Unknown error")


class _DownloadWorker(QThread):
    progress = pyqtSignal(int, int)
    finished_ok = pyqtSignal(str)
    finished_err = pyqtSignal(str)

    def __init__(self, manifest):
        super().__init__()
        self.manifest = manifest

    def run(self):
        try:
            path = updater.download(
                self.manifest,
                progress_cb=lambda d, t: self.progress.emit(d, t),
            )
            self.finished_ok.emit(str(path))
        except Exception as e:
            self.finished_err.emit(str(e))


class AboutDialog(QDialog):
    def __init__(self, parent=None, license_payload: dict | None = None):
        super().__init__(parent)
        self.license_payload = license_payload or {}
        self.manifest = None
        self.check_worker = None
        self.dl_worker = None
        self.setWindowTitle("About RunLab Mail")
        self.resize(540, 480)
        self._build_ui()
        # Show last checked from prior sessions before kicking a new check
        self.last_checked_label.setText(updater.get_last_checked_human())
        # Start a check immediately so the user sees status
        self._start_check()

    def _build_ui(self):
        layout = QVBoxLayout(self)

        # App header
        title = QLabel("<h2>RunLab Mail</h2>")
        title.setTextFormat(Qt.RichText)
        layout.addWidget(title)

        subtitle = QLabel(
            "Desktop email client with POP3 + SMTP support."
        )
        subtitle.setStyleSheet("color:#666;")
        layout.addWidget(subtitle)

        layout.addSpacing(8)

        # Version info form
        info = QFormLayout()
        info.setLabelAlignment(Qt.AlignRight)
        self.current_version_label = QLabel(f"<b>{__version__}</b>")
        self.current_version_label.setTextFormat(Qt.RichText)
        info.addRow("Current version:", self.current_version_label)

        self.latest_version_label = QLabel("checking...")
        info.addRow("Latest version:", self.latest_version_label)

        self.last_checked_label = QLabel("—")
        self.last_checked_label.setStyleSheet("color:#666;")
        info.addRow("Last checked:", self.last_checked_label)

        self.status_label = QLabel("")
        self.status_label.setWordWrap(True)
        info.addRow("Status:", self.status_label)

        manifest_url = updater.get_manifest_url()
        url_label = QLabel(f'<a href="{manifest_url}">{manifest_url}</a>')
        url_label.setOpenExternalLinks(True)
        url_label.setTextFormat(Qt.RichText)
        url_label.setWordWrap(True)
        url_label.setStyleSheet("color:#666; font-size: 11px;")
        info.addRow("Update channel:", url_label)

        # License info
        if self.license_payload:
            licensed_to = (
                self.license_payload.get("name")
                or self.license_payload.get("email")
                or "(unknown)"
            )
            info.addRow("Licensed to:", QLabel(licensed_to))
            lid = self.license_payload.get("license_id") or "-"
            info.addRow("License ID:", QLabel(lid))

            # Expires + trial status
            from core import license as licmod
            exp = self.license_payload.get("expires_at") or "(never)"
            if licmod.is_trial(self.license_payload):
                days = licmod.days_until_expiry(self.license_payload)
                if days is None:
                    badge = "TRIAL"
                    color = "#bf6900"
                elif days < 0:
                    badge = f"TRIAL EXPIRED ({-days} day{'s' if -days != 1 else ''} ago)"
                    color = "#a4262c"
                elif days == 0:
                    badge = "TRIAL — expires today"
                    color = "#a4262c"
                elif days <= 7:
                    badge = f"TRIAL — {days} day{'s' if days != 1 else ''} remaining"
                    color = "#bf6900"
                else:
                    badge = f"TRIAL — {days} days remaining"
                    color = "#107c10"
                exp_label = QLabel(
                    f"{exp[:10]}  "
                    f"<span style='color:{color};font-weight:600;'>"
                    f"[{badge}]</span>"
                )
                exp_label.setTextFormat(Qt.RichText)
            else:
                exp_label = QLabel(
                    f"{exp}  "
                    f"<span style='color:#107c10;font-weight:600;'>"
                    f"[PERPETUAL]</span>"
                )
                exp_label.setTextFormat(Qt.RichText)
            info.addRow("Expires:", exp_label)

        layout.addLayout(info)

        # Buttons row for license-related actions
        license_btn_row = QHBoxLayout()
        self.refresh_lic_btn = QPushButton("Refresh license status")
        self.refresh_lic_btn.setToolTip(
            "Force-fetch the latest revocation list from the server "
            "(bypasses the 1-hour cache)."
        )
        self.refresh_lic_btn.clicked.connect(self._force_refresh_license)
        license_btn_row.addStretch(1)
        license_btn_row.addWidget(self.refresh_lic_btn)
        layout.addLayout(license_btn_row)

        # Release notes panel
        layout.addSpacing(8)
        layout.addWidget(QLabel("Release notes:"))
        self.notes_view = QTextEdit()
        self.notes_view.setReadOnly(True)
        self.notes_view.setMaximumHeight(110)
        layout.addWidget(self.notes_view)

        # Progress bar (hidden by default)
        self.progress = QProgressBar()
        self.progress.setVisible(False)
        layout.addWidget(self.progress)

        # Buttons
        btn_row = QHBoxLayout()
        self.check_btn = QPushButton("Check Again")
        self.check_btn.clicked.connect(self._start_check)
        self.update_btn = QPushButton("Download && Install Update")
        self.update_btn.setEnabled(False)
        self.update_btn.setDefault(True)
        self.update_btn.clicked.connect(self._start_install)
        self.close_btn = QPushButton("Close")
        self.close_btn.clicked.connect(self.accept)
        btn_row.addWidget(self.check_btn)
        btn_row.addStretch(1)
        btn_row.addWidget(self.update_btn)
        btn_row.addWidget(self.close_btn)
        layout.addLayout(btn_row)

    # ----- Update flow -----
    def _start_check(self):
        self.latest_version_label.setText("checking...")
        self.status_label.setText("Contacting update server...")
        self.status_label.setStyleSheet("")  # reset error color
        self.notes_view.setPlainText("")
        self.update_btn.setEnabled(False)
        self.check_btn.setEnabled(False)
        self.progress.setVisible(False)

        self.check_worker = _CheckWorker()
        self.check_worker.found.connect(self._on_update_found)
        self.check_worker.none.connect(self._on_no_update)
        self.check_worker.err.connect(self._on_check_error)
        self.check_worker.start()

    def _stamp_last_checked(self):
        now = datetime.now().strftime("%d %b %Y, %H:%M:%S")
        self.last_checked_label.setText(now)

    def _on_update_found(self, manifest: dict):
        self._stamp_last_checked()
        self.manifest = manifest
        version = manifest.get("version", "?")
        self.latest_version_label.setText(f"<b style='color:#1565c0;'>{version}</b> (newer)")
        self.latest_version_label.setTextFormat(Qt.RichText)
        self.status_label.setText("New version available. Click 'Download & Install' to update.")
        self.notes_view.setPlainText(manifest.get("notes") or "")
        self.update_btn.setEnabled(True)
        self.check_btn.setEnabled(True)

    def _on_no_update(self):
        self._stamp_last_checked()
        self.latest_version_label.setText(
            f"<span style='color:#2e7d32;'>{__version__}</span> (up to date)"
        )
        self.latest_version_label.setTextFormat(Qt.RichText)
        self.status_label.setText("Your RunLab Mail is up to date.")
        self.check_btn.setEnabled(True)

    def _on_check_error(self, msg: str):
        # Do NOT stamp last_checked — we never reached the server
        self.latest_version_label.setText(
            "<span style='color:#b71c1c;'>connection error</span>"
        )
        self.latest_version_label.setTextFormat(Qt.RichText)
        self.status_label.setText(msg)
        self.status_label.setStyleSheet("color:#b71c1c;")
        self.check_btn.setEnabled(True)

    def _start_install(self):
        if not self.manifest:
            return
        if not updater.is_frozen():
            self.status_label.setText(
                "Auto-install only works on the .exe build (you're running from source)."
            )
            return

        self.update_btn.setEnabled(False)
        self.check_btn.setEnabled(False)
        self.close_btn.setEnabled(False)
        self.progress.setVisible(True)
        self.progress.setRange(0, 0)
        self.status_label.setText("Downloading update...")

        self.dl_worker = _DownloadWorker(self.manifest)
        self.dl_worker.progress.connect(self._on_dl_progress)
        self.dl_worker.finished_ok.connect(self._on_dl_done)
        self.dl_worker.finished_err.connect(self._on_dl_err)
        self.dl_worker.start()

    def _on_dl_progress(self, downloaded, total):
        if total > 0:
            self.progress.setRange(0, total)
            self.progress.setValue(downloaded)
            self.status_label.setText(
                f"Downloading: {downloaded/1024/1024:.1f} / {total/1024/1024:.1f} MB"
            )
        else:
            self.status_label.setText(
                f"Downloading: {downloaded/1024/1024:.1f} MB"
            )

    def _on_dl_done(self, path: str):
        from pathlib import Path
        self.status_label.setText("Verified. Installing and restarting...")
        try:
            updater.install_and_restart(Path(path))
        except Exception as e:
            self._on_dl_err(str(e))

    def _on_dl_err(self, err: str):
        self.progress.setVisible(False)
        self.status_label.setText(f"<span style='color:#b71c1c;'>{err}</span>")
        self.status_label.setTextFormat(Qt.RichText)
        self.update_btn.setEnabled(True)
        self.check_btn.setEnabled(True)
        self.close_btn.setEnabled(True)

    # ----- License refresh -----
    def _force_refresh_license(self):
        from core import license as licmod
        self.refresh_lic_btn.setEnabled(False)
        self.refresh_lic_btn.setText("Checking...")
        QApplication.processEvents()
        try:
            licmod.force_refresh_blacklist()
            ok, payload, err = licmod.is_licensed()
            if ok:
                self.refresh_lic_btn.setText("✓ License is valid")
            else:
                self.refresh_lic_btn.setText(f"✗ {err[:60]}")
                # Notify parent so it can act
                from ui.main_window import MainWindow
                p = self.parent()
                if isinstance(p, MainWindow):
                    p._on_license_revoked(err)
        except Exception as e:
            self.refresh_lic_btn.setText(f"Error: {e}")
        finally:
            from PyQt5.QtCore import QTimer
            QTimer.singleShot(
                3000,
                lambda: (
                    self.refresh_lic_btn.setText("Refresh license status"),
                    self.refresh_lic_btn.setEnabled(True),
                ),
            )
