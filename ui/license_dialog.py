"""
License activation dialog. Shown when no valid license exists.

Also includes a "About license" small viewer used from a menu so the
user can see who their copy is licensed to.
"""
from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPlainTextEdit,
    QPushButton, QMessageBox, QApplication,
)
from PyQt5.QtCore import Qt
from core import license as licmod
from core.license import LicenseError


class LicenseActivationDialog(QDialog):
    """Blocking dialog: user must paste a valid license to enter the app."""

    def __init__(self, parent=None, error: str = ""):
        super().__init__(parent)
        self.setWindowTitle("RunLab Mail - Activate License")
        self.setModal(True)
        self.resize(560, 420)
        self.payload = None
        self._build_ui(error)

    def _build_ui(self, prior_error: str):
        layout = QVBoxLayout(self)

        title = QLabel("<h3>License required</h3>")
        layout.addWidget(title)

        info = QLabel(
            "Paste the license key sent to you by your administrator below.<br>"
            "Don't have one yet? Send your <b>Machine ID</b> to your admin."
        )
        info.setTextFormat(Qt.RichText)
        info.setWordWrap(True)
        layout.addWidget(info)

        # Machine ID display
        mid_row = QHBoxLayout()
        mid_label = QLabel(f"Machine ID:")
        mid_label.setStyleSheet("color:#666;")
        self.mid_value = QLabel(licmod.get_machine_id())
        self.mid_value.setStyleSheet(
            "font-family: Consolas, monospace; "
            "background:#f5f5f5; padding:4px 8px; border-radius:3px;"
        )
        self.mid_value.setTextInteractionFlags(Qt.TextSelectableByMouse)
        copy_btn = QPushButton("Copy")
        copy_btn.clicked.connect(self._copy_machine_id)
        mid_row.addWidget(mid_label)
        mid_row.addWidget(self.mid_value, 1)
        mid_row.addWidget(copy_btn)
        layout.addLayout(mid_row)

        layout.addSpacing(8)

        layout.addWidget(QLabel("License key:"))
        self.key_edit = QPlainTextEdit()
        self.key_edit.setPlaceholderText(
            "Paste your license key here..."
        )
        self.key_edit.setStyleSheet("font-family: Consolas, monospace;")
        layout.addWidget(self.key_edit, 1)

        if prior_error:
            err = QLabel(f"<span style='color:#b71c1c;'>{prior_error}</span>")
            err.setTextFormat(Qt.RichText)
            err.setWordWrap(True)
            layout.addWidget(err)

        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        self.cancel_btn = QPushButton("Quit")
        self.cancel_btn.clicked.connect(self.reject)
        self.activate_btn = QPushButton("Activate")
        self.activate_btn.setDefault(True)
        self.activate_btn.clicked.connect(self._activate)
        btn_row.addWidget(self.cancel_btn)
        btn_row.addWidget(self.activate_btn)
        layout.addLayout(btn_row)

    def _copy_machine_id(self):
        QApplication.clipboard().setText(self.mid_value.text())

    def _activate(self):
        key = self.key_edit.toPlainText().strip()
        if not key:
            QMessageBox.warning(self, "Missing", "Please paste your license key.")
            return
        try:
            obj = licmod.parse_license_string(key)
            payload = licmod.validate_license(obj)
        except LicenseError as e:
            QMessageBox.critical(self, "License invalid", str(e))
            return

        licmod.save_license(obj)
        self.payload = payload
        QMessageBox.information(
            self, "Activated",
            f"License activated for {payload.get('name', 'user')}.\n"
            f"Welcome to RunLab Mail.",
        )
        self.accept()


class LicenseInfoDialog(QDialog):
    """Read-only dialog showing the current license info, with an option to
    enter/replace the license key manually (like Outlook / eM Client)."""

    def __init__(self, payload: dict, parent=None):
        super().__init__(parent)
        self.setWindowTitle("About License")
        self.resize(460, 280)
        self._payload = payload or {}
        self._new_payload = None  # set if the user activates a new key
        layout = QVBoxLayout(self)

        rows = [
            ("Licensed to", payload.get("name") or "-"),
            ("Email", payload.get("email") or "-"),
            ("License ID", payload.get("license_id") or "-"),
            ("Issued", payload.get("issued_at") or "-"),
            ("Expires", payload.get("expires_at") or "(never)"),
            ("Machine bound", "yes" if payload.get("machine_id_hash") else "no"),
            ("Machine ID", licmod.get_machine_id()),
        ]
        for k, v in rows:
            row = QHBoxLayout()
            lk = QLabel(f"<b>{k}:</b>")
            lk.setTextFormat(Qt.RichText)
            lk.setMinimumWidth(120)
            lv = QLabel(str(v))
            lv.setTextInteractionFlags(Qt.TextSelectableByMouse)
            lv.setWordWrap(True)
            row.addWidget(lk)
            row.addWidget(lv, 1)
            if k == "Machine ID":
                copy_mid = QPushButton("Copy")
                copy_mid.setToolTip(
                    "Send this to your admin to move your license to this device."
                )
                copy_mid.clicked.connect(
                    lambda _=False, val=str(v):
                        QApplication.clipboard().setText(val)
                )
                row.addWidget(copy_mid)
            layout.addLayout(row)

        layout.addStretch(1)

        btn_row = QHBoxLayout()
        enter_btn = QPushButton("Enter / replace license key...")
        enter_btn.setToolTip(
            "Paste a license key your administrator gave you to activate "
            "or update this installation."
        )
        enter_btn.clicked.connect(self._enter_license)
        btn_row.addWidget(enter_btn)
        btn_row.addStretch(1)
        btn = QPushButton("Close")
        btn.clicked.connect(self.accept)
        btn_row.addWidget(btn)
        layout.addLayout(btn_row)

    def _enter_license(self):
        """Open the activation dialog so the user can paste a new key. On
        success, the new license is saved and this dialog reports it."""
        dlg = LicenseActivationDialog(self)
        # Pre-fill nothing; the user pastes their key. Reuse its activation.
        if dlg.exec_() == QDialog.Accepted and dlg.payload:
            self._new_payload = dlg.payload
            QMessageBox.information(
                self, "License updated",
                "Your license has been updated. Some changes may take effect "
                "after restarting RunLab Mail.",
            )
            self.accept()
