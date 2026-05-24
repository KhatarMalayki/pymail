"""
Application settings: data folder location, future global preferences.
"""
import os
from pathlib import Path
from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QFormLayout, QLabel, QLineEdit,
    QPushButton, QFileDialog, QMessageBox, QGroupBox, QRadioButton,
    QButtonGroup, QWidget,
)
from core import config


class SettingsDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("RunLab Mail Settings")
        self.resize(620, 420)
        self._build_ui()
        self._load()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(14)

        # ---- Data folder ----
        gb = QGroupBox("Data folder")
        form = QVBoxLayout(gb)
        info = QLabel(
            "RunLab Mail stores all your accounts, emails, attachments, and "
            "contacts in a single SQLite database (pymail.db). You can move "
            "it, point at a backup, or share it across PCs."
        )
        info.setWordWrap(True)
        info.setStyleSheet("color:#605e5c;")
        form.addWidget(info)

        # Current location row
        cur_row = QHBoxLayout()
        cur_row.addWidget(QLabel("Current:"))
        self.path_edit = QLineEdit()
        self.path_edit.setReadOnly(True)
        self.path_edit.setStyleSheet("font-family: Consolas, monospace;")
        cur_row.addWidget(self.path_edit, 1)
        form.addLayout(cur_row)

        form.addSpacing(6)

        # Mode picker
        self.mode_group = QButtonGroup(self)

        self.mode_move = QRadioButton(
            "&Move my data to a new folder (copy current pymail.db, then "
            "switch to it)"
        )
        self.mode_move.setChecked(True)
        self.mode_group.addButton(self.mode_move, 1)
        form.addWidget(self.mode_move)

        self.mode_use_existing = QRadioButton(
            "&Use the existing pymail.db in another folder (e.g. restore "
            "from a backup, share with another PC)"
        )
        self.mode_group.addButton(self.mode_use_existing, 2)
        form.addWidget(self.mode_use_existing)

        # Target folder row
        target_row = QHBoxLayout()
        target_row.addWidget(QLabel("Target:"))
        self.target_edit = QLineEdit()
        self.target_edit.setStyleSheet("font-family: Consolas, monospace;")
        target_row.addWidget(self.target_edit, 1)
        browse = QPushButton("Browse...")
        browse.clicked.connect(self._browse)
        target_row.addWidget(browse)
        form.addLayout(target_row)

        warn = QLabel(
            "<i>The app will close after applying. Restart it to load data "
            "from the new location.</i>"
        )
        warn.setTextFormat(Qt.RichText)
        warn.setStyleSheet("color:#605e5c;")
        warn.setWordWrap(True)
        form.addWidget(warn)

        layout.addWidget(gb)

        layout.addStretch(1)

        # ---- Buttons ----
        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        save = QPushButton("Apply && Restart")
        save.setDefault(True)
        save.clicked.connect(self._save)
        btn_row.addWidget(cancel)
        btn_row.addWidget(save)
        layout.addLayout(btn_row)

    def _load(self):
        current = str(config.get_data_dir())
        self.path_edit.setText(current)
        self.target_edit.setText(current)

    def _browse(self):
        current = self.target_edit.text() or str(config.get_data_dir())
        new_dir = QFileDialog.getExistingDirectory(
            self, "Choose folder containing pymail.db (or a new empty folder)",
            current,
        )
        if new_dir:
            self.target_edit.setText(new_dir)

    def _save(self):
        new_dir_str = self.target_edit.text().strip()
        if not new_dir_str:
            return
        new_dir = Path(new_dir_str)
        if new_dir.resolve() == config.get_data_dir().resolve():
            self.reject()
            return

        copy_existing = self.mode_move.isChecked()
        # When using "Use existing", verify there's a pymail.db there
        if not copy_existing:
            target_db = new_dir / "pymail.db"
            if not target_db.is_file():
                ret = QMessageBox.question(
                    self, "No pymail.db found",
                    f"There's no pymail.db in:\n  {new_dir}\n\n"
                    f"RunLab Mail will create a new empty database there. "
                    f"Your current data will remain untouched in:\n"
                    f"  {config.get_data_dir()}\n\n"
                    f"Continue?",
                    QMessageBox.Yes | QMessageBox.Cancel,
                    QMessageBox.Cancel,
                )
                if ret != QMessageBox.Yes:
                    return
            else:
                ret = QMessageBox.question(
                    self, "Use this database?",
                    f"RunLab Mail will switch to:\n  {target_db}\n\n"
                    f"Your current data is NOT modified — it stays in:\n"
                    f"  {config.get_data_dir()}\n\n"
                    f"Continue?",
                    QMessageBox.Yes | QMessageBox.Cancel,
                    QMessageBox.Yes,
                )
                if ret != QMessageBox.Yes:
                    return

        try:
            config.change_data_dir(new_dir, copy_existing=copy_existing)
        except Exception as e:
            QMessageBox.critical(self, "Failed to change data folder", str(e))
            return

        QMessageBox.information(
            self, "Restart required",
            f"Data folder changed to:\n{new_dir}\n\n"
            f"RunLab Mail will close now. Re-open it to use the new location.",
        )
        self.accept()
        from PyQt5.QtWidgets import QApplication
        QApplication.instance().quit()
