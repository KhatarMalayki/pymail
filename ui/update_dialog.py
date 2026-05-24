"""
Update prompt + download progress dialogs.
"""
from pathlib import Path
from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QTextEdit,
    QProgressBar, QMessageBox, QCheckBox,
)
from PyQt5.QtCore import Qt, QThread, pyqtSignal
from core import updater
from core.version import __version__


class _DownloadWorker(QThread):
    progress = pyqtSignal(int, int)  # downloaded, total
    finished_ok = pyqtSignal(str)    # path
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


class UpdateDialog(QDialog):
    """Show "new version available", let the user install or skip."""

    def __init__(self, manifest: dict, parent=None):
        super().__init__(parent)
        self.manifest = manifest
        self.worker = None
        self.setWindowTitle("RunLab Mail Update")
        self.resize(520, 380)
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)

        title = QLabel(
            f"<h3>A new version of RunLab Mail is available</h3>"
            f"<p>Current: <b>{__version__}</b><br>"
            f"Latest: <b>{self.manifest.get('version', '?')}</b></p>"
        )
        title.setTextFormat(Qt.RichText)
        layout.addWidget(title)

        layout.addWidget(QLabel("Release notes:"))
        notes = QTextEdit()
        notes.setReadOnly(True)
        notes.setPlainText(self.manifest.get("notes") or "(no notes provided)")
        layout.addWidget(notes, 1)

        self.progress = QProgressBar()
        self.progress.setVisible(False)
        layout.addWidget(self.progress)

        self.status = QLabel("")
        self.status.setVisible(False)
        layout.addWidget(self.status)

        btn_row = QHBoxLayout()
        self.later_btn = QPushButton("Later")
        self.later_btn.clicked.connect(self.reject)
        self.install_btn = QPushButton("Install Update && Restart")
        self.install_btn.setDefault(True)
        self.install_btn.clicked.connect(self._install)
        btn_row.addStretch(1)
        btn_row.addWidget(self.later_btn)
        btn_row.addWidget(self.install_btn)
        layout.addLayout(btn_row)

        if self.manifest.get("mandatory"):
            self.later_btn.setEnabled(False)
            self.later_btn.setToolTip("This update is mandatory.")

    def _install(self):
        if not updater.is_frozen():
            QMessageBox.information(
                self, "Dev mode",
                "Auto-install only works on the .exe build. "
                "Please rebuild and redistribute the new version manually.",
            )
            return

        self.install_btn.setEnabled(False)
        self.later_btn.setEnabled(False)
        self.progress.setVisible(True)
        self.status.setVisible(True)
        self.status.setText("Downloading...")
        self.progress.setRange(0, 0)  # indeterminate until we know size

        self.worker = _DownloadWorker(self.manifest)
        self.worker.progress.connect(self._on_progress)
        self.worker.finished_ok.connect(self._on_downloaded)
        self.worker.finished_err.connect(self._on_error)
        self.worker.start()

    def _on_progress(self, downloaded, total):
        if total > 0:
            self.progress.setRange(0, total)
            self.progress.setValue(downloaded)
            self.status.setText(
                f"Downloading... {downloaded/1024/1024:.1f} / {total/1024/1024:.1f} MB"
            )
        else:
            self.status.setText(f"Downloading... {downloaded/1024/1024:.1f} MB")

    def _on_downloaded(self, path):
        self.status.setText("Installing and restarting...")
        try:
            updater.install_and_restart(Path(path))
        except Exception as e:
            QMessageBox.critical(self, "Update failed", str(e))
            self.install_btn.setEnabled(True)
            self.later_btn.setEnabled(True)

    def _on_error(self, err):
        QMessageBox.critical(self, "Download failed", err)
        self.install_btn.setEnabled(True)
        self.later_btn.setEnabled(True)
        self.progress.setVisible(False)
        self.status.setVisible(False)
