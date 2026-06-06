"""
Import wizard: bring emails & contacts from Outlook, Windows Live Mail,
eM Client, Thunderbird, etc. into RunLab Mail.

Sources:
  - Auto-detected Windows Live Mail store
  - A folder of .eml files
  - An .mbox file
  - An Outlook .pst/.ost (via installed Outlook COM)
  - Contacts from .csv or .vcf
"""
from PyQt5.QtCore import Qt, QThread, pyqtSignal
from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QComboBox,
    QFileDialog, QMessageBox, QProgressBar, QGroupBox, QRadioButton,
    QButtonGroup, QWidget,
)

from core import database, mail_import
from core import contacts as contacts_mod
from . import theme as theme_mod


class _ImportWorker(QThread):
    progress = pyqtSignal(int, int, str)   # done, total, label
    finished_ok = pyqtSignal(dict)         # result summary
    finished_err = pyqtSignal(str)

    def __init__(self, fn, *args, **kwargs):
        super().__init__()
        self._fn = fn
        self._args = args
        self._kwargs = kwargs

    def run(self):
        try:
            def cb(done, total, label):
                self.progress.emit(done, total, label)
            self._kwargs["progress_cb"] = cb
            result = self._fn(*self._args, **self._kwargs)
            self.finished_ok.emit(result or {})
        except Exception as e:
            self.finished_err.emit(str(e))


class ImportDialog(QDialog):
    # Emitted after a successful email import so the main window can refresh.
    imported = pyqtSignal()

    def __init__(self, parent=None, current_account_id=None):
        super().__init__(parent)
        self.setWindowTitle("Import mail & contacts")
        self.resize(560, 440)
        self._worker = None
        self._current_account_id = current_account_id
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(10)

        title = QLabel("<h3>Import mail & contacts</h3>")
        title.setTextFormat(Qt.RichText)
        layout.addWidget(title)

        info = QLabel(
            "Bring your existing email and contacts from Outlook, Windows "
            "Live Mail, eM Client, or Thunderbird into RunLab Mail."
        )
        info.setWordWrap(True)
        info.setStyleSheet(f"color:{theme_mod.color('text_muted')};")
        layout.addWidget(info)

        # ---- Source selection ----
        src_box = QGroupBox("What do you want to import?")
        src_layout = QVBoxLayout(src_box)
        self._src_group = QButtonGroup(self)

        self._rb_wlm = QRadioButton("Windows Live Mail (auto-detect on this PC)")
        self._rb_eml = QRadioButton("A folder of .eml files (Outlook/eM Client/WLM export)")
        self._rb_mbox = QRadioButton("An .mbox file (Thunderbird/Apple Mail/eM Client)")
        self._rb_pst = QRadioButton("Outlook data file (.pst / .ost) — needs Outlook installed")
        self._rb_csv = QRadioButton("Contacts from .csv")
        self._rb_vcf = QRadioButton("Contacts from .vcf (vCard)")
        for i, rb in enumerate((self._rb_wlm, self._rb_eml, self._rb_mbox,
                                self._rb_pst, self._rb_csv, self._rb_vcf)):
            self._src_group.addButton(rb, i)
            src_layout.addWidget(rb)
        self._rb_wlm.setChecked(True)

        # Disable WLM option if nothing detected; disable PST if no Outlook.
        self._wlm_roots = mail_import.detect_windows_live_mail()
        if not self._wlm_roots:
            self._rb_wlm.setEnabled(False)
            self._rb_wlm.setText("Windows Live Mail (not found on this PC)")
            self._rb_eml.setChecked(True)
        if not mail_import.outlook_available():
            self._rb_pst.setText(
                "Outlook data file (.pst/.ost) — Outlook not detected"
            )
            # Still allow selecting; we show a clear error if it truly fails.

        layout.addWidget(src_box)

        # ---- Target account ----
        tgt_row = QHBoxLayout()
        tgt_row.addWidget(QLabel("Import emails into account:"))
        self.account_combo = QComboBox()
        self._reload_accounts()
        tgt_row.addWidget(self.account_combo, 1)
        layout.addLayout(tgt_row)
        self._tgt_hint = QLabel(
            "Tip: choose \"Imported Mail\" to keep imported messages separate "
            "from your live account."
        )
        self._tgt_hint.setWordWrap(True)
        self._tgt_hint.setStyleSheet(
            f"color:{theme_mod.color('text_muted')}; font-size:9pt;"
        )
        layout.addWidget(self._tgt_hint)

        # ---- Progress ----
        self.progress = QProgressBar()
        self.progress.setVisible(False)
        layout.addWidget(self.progress)
        self.status = QLabel("")
        self.status.setStyleSheet(f"color:{theme_mod.color('text_muted')};")
        layout.addWidget(self.status)

        layout.addStretch(1)

        # ---- Buttons ----
        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        self.start_btn = QPushButton("Start import")
        self.start_btn.setDefault(True)
        self.start_btn.clicked.connect(self._start)
        self.close_btn = QPushButton("Close")
        self.close_btn.clicked.connect(self.reject)
        btn_row.addWidget(self.start_btn)
        btn_row.addWidget(self.close_btn)
        layout.addLayout(btn_row)

        self._src_group.buttonToggled.connect(self._update_target_enabled)
        self._update_target_enabled()

    def _reload_accounts(self):
        self.account_combo.clear()
        for a in database.list_accounts():
            self.account_combo.addItem(f'{a["name"]} <{a["email"]}>', a["id"])
        # Always offer the dedicated import account as a choice.
        self.account_combo.addItem("➕  Imported Mail (separate, local)", "__import__")
        if self._current_account_id is not None:
            idx = self.account_combo.findData(self._current_account_id)
            if idx >= 0:
                self.account_combo.setCurrentIndex(idx)

    def _is_contacts_mode(self) -> bool:
        return self._src_group.checkedButton() in (self._rb_csv, self._rb_vcf)

    def _update_target_enabled(self, *_):
        # Contacts import doesn't need a target account.
        contacts = self._is_contacts_mode()
        self.account_combo.setEnabled(not contacts)
        self._tgt_hint.setVisible(not contacts)

    def _resolve_account_id(self) -> int:
        data = self.account_combo.currentData()
        if data == "__import__":
            return database.get_or_create_import_account()
        return int(data)

    def _start(self):
        rb = self._src_group.checkedButton()

        # ---- Contacts paths (synchronous, fast) ----
        if rb is self._rb_csv:
            path, _ = QFileDialog.getOpenFileName(
                self, "Choose contacts CSV", "", "CSV file (*.csv);;All files (*)")
            if not path:
                return
            try:
                added, skipped = contacts_mod.import_csv(path)
                QMessageBox.information(
                    self, "Contacts imported",
                    f"Imported {added} contact(s)." +
                    (f" Skipped {skipped}." if skipped else ""))
            except Exception as e:
                QMessageBox.critical(self, "Import failed", str(e))
            return
        if rb is self._rb_vcf:
            path, _ = QFileDialog.getOpenFileName(
                self, "Choose vCard file", "", "vCard (*.vcf);;All files (*)")
            if not path:
                return
            try:
                added, skipped = mail_import.import_vcf(path)
                QMessageBox.information(
                    self, "Contacts imported",
                    f"Imported {added} contact(s) from vCard.")
            except Exception as e:
                QMessageBox.critical(self, "Import failed", str(e))
            return

        # ---- Email paths (background worker) ----
        account_id = self._resolve_account_id()

        if rb is self._rb_wlm:
            if not self._wlm_roots:
                QMessageBox.warning(self, "Not found",
                                    "No Windows Live Mail store was found.")
                return
            root = self._wlm_roots[0]
            self._run_worker(
                mail_import.import_windows_live_mail, account_id, root)
        elif rb is self._rb_eml:
            folder = QFileDialog.getExistingDirectory(
                self, "Choose folder containing .eml files")
            if not folder:
                return
            self._run_worker(
                mail_import.import_eml_folder, account_id, folder)
        elif rb is self._rb_mbox:
            path, _ = QFileDialog.getOpenFileName(
                self, "Choose .mbox file", "", "mbox (*.mbox);;All files (*)")
            if not path:
                return
            self._run_worker(
                mail_import.import_mbox, account_id, path, "inbox")
        elif rb is self._rb_pst:
            if not mail_import.outlook_available():
                QMessageBox.critical(
                    self, "Outlook required",
                    "Reading .pst/.ost directly needs Microsoft Outlook "
                    "installed on this PC.\n\nAlternative: in Outlook, export "
                    "your mail to .eml or .mbox, then use that option here.")
                return
            path, _ = QFileDialog.getOpenFileName(
                self, "Choose Outlook data file", "",
                "Outlook data (*.pst *.ost);;All files (*)")
            if not path:
                return
            self._run_worker(
                mail_import.import_pst_via_outlook, account_id, path)

    def _run_worker(self, fn, *args):
        self.start_btn.setEnabled(False)
        self.close_btn.setEnabled(False)
        self.progress.setVisible(True)
        self.progress.setRange(0, 0)  # indeterminate until first progress
        self.status.setText("Scanning source...")
        self._worker = _ImportWorker(fn, *args)
        self._worker.progress.connect(self._on_progress)
        self._worker.finished_ok.connect(self._on_done)
        self._worker.finished_err.connect(self._on_err)
        self._worker.start()

    def _on_progress(self, done, total, label):
        if total > 0:
            self.progress.setRange(0, total)
            self.progress.setValue(done)
        self.status.setText(label)

    def _on_done(self, result: dict):
        self.start_btn.setEnabled(True)
        self.close_btn.setEnabled(True)
        self.progress.setVisible(False)
        added = result.get("added", 0)
        skipped = result.get("skipped", 0)
        folders = result.get("folders", {})
        # Rebuild the autocomplete contact cache from the newly imported mail.
        try:
            database.rebuild_contact_cache()
        except Exception:
            pass
        detail = ""
        if folders:
            detail = "\n\nBy folder:\n" + "\n".join(
                f"  • {k}: {v}" for k, v in sorted(folders.items()))
        QMessageBox.information(
            self, "Import complete",
            f"Imported {added} message(s)." +
            (f" Skipped {skipped} (duplicates or unreadable)." if skipped else "")
            + detail)
        self.status.setText(f"Done: {added} imported, {skipped} skipped.")
        self.imported.emit()

    def _on_err(self, err: str):
        self.start_btn.setEnabled(True)
        self.close_btn.setEnabled(True)
        self.progress.setVisible(False)
        self.status.setText("")
        QMessageBox.critical(self, "Import failed", err)
