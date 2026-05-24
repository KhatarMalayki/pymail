"""
Account add/edit dialog with POP3 + SMTP test buttons.
"""
from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QFormLayout, QLabel, QLineEdit,
    QSpinBox, QCheckBox, QComboBox, QPushButton, QTabWidget, QWidget,
    QMessageBox, QDialogButtonBox, QGroupBox, QPlainTextEdit, QTextEdit,
)
from PyQt5.QtCore import Qt
from PyQt5.QtGui import QTextCharFormat, QFont
from core import database, pop3_client, smtp_client
from core import imap_client


class AccountDialog(QDialog):
    def __init__(self, parent=None, account=None):
        super().__init__(parent)
        self.account = account
        self.setWindowTitle("Edit Account" if account else "Add Account")
        self.resize(720, 620)
        self._build_ui()
        if account:
            self._populate(account)

    def _build_ui(self):
        layout = QVBoxLayout(self)

        # ---- General ----
        general = QGroupBox("Identity")
        gform = QFormLayout(general)
        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("Your Name")
        self.email_edit = QLineEdit()
        self.email_edit.setPlaceholderText("you@example.com")
        gform.addRow("Display name:", self.name_edit)
        gform.addRow("Email address:", self.email_edit)
        layout.addWidget(general)

        # ---- Tabs ----
        tabs = QTabWidget()
        layout.addWidget(tabs, 1)

        # POP3 tab
        pop_tab = QWidget()
        pform = QFormLayout(pop_tab)
        self.pop_host = QLineEdit("pop.gmail.com")
        self.pop_port = QSpinBox(); self.pop_port.setRange(1, 65535); self.pop_port.setValue(995)
        self.pop_ssl = QCheckBox("Use SSL/TLS")
        self.pop_ssl.setChecked(True)
        self.pop_user = QLineEdit()
        self.pop_user.setPlaceholderText("(default = email address)")
        self.pop_pass = QLineEdit(); self.pop_pass.setEchoMode(QLineEdit.Password)
        self.leave_on_server = QCheckBox("Leave a copy of messages on the server")
        self.leave_on_server.setChecked(True)
        self.leave_on_server.setToolTip(
            "Recommended ON for shared mailboxes. If OFF, RunLab Mail will issue a "
            "DELETE command to the POP3 server after each download, removing "
            "the email from the server permanently. Use this if your mailbox "
            "fills up and you want RunLab Mail to be your archive."
        )
        leave_hint = QLabel(
            "<i style='color:#605e5c; font-size:9pt;'>"
            "ON: emails stay on server (mailbox can fill up).<br>"
            "OFF: emails are deleted from server after download "
            "(saves quota; RunLab Mail becomes the only copy)."
            "</i>"
        )
        leave_hint.setTextFormat(Qt.RichText)
        leave_hint.setWordWrap(True)
        self.pop_test_btn = QPushButton("Test POP3")
        self.pop_test_btn.clicked.connect(self._test_pop)

        pform.addRow("Server:", self.pop_host)
        pform.addRow("Port:", self.pop_port)
        pform.addRow("", self.pop_ssl)
        pform.addRow("Username:", self.pop_user)
        pform.addRow("Password:", self.pop_pass)
        pform.addRow("", self.leave_on_server)
        pform.addRow("", leave_hint)
        pform.addRow("", self.pop_test_btn)
        tabs.addTab(pop_tab, "Incoming (POP3)")

        # SMTP tab
        smtp_tab = QWidget()
        sform = QFormLayout(smtp_tab)
        self.smtp_host = QLineEdit("smtp.gmail.com")
        self.smtp_port = QSpinBox(); self.smtp_port.setRange(1, 65535); self.smtp_port.setValue(465)
        self.smtp_security = QComboBox()
        self.smtp_security.addItems(["SSL", "STARTTLS", "NONE"])
        self.smtp_user = QLineEdit()
        self.smtp_user.setPlaceholderText("(default = email address)")
        self.smtp_pass = QLineEdit(); self.smtp_pass.setEchoMode(QLineEdit.Password)
        self.smtp_test_btn = QPushButton("Test SMTP")
        self.smtp_test_btn.clicked.connect(self._test_smtp)

        # "Same as POP3" convenience checkbox
        self.same_as_pop = QCheckBox("Use same username && password as Incoming (POP3)")
        self.same_as_pop.setToolTip(
            "Most providers (Gmail, Carbonio, Outlook.com) use the same "
            "credentials for both POP3 and SMTP. Tick this to keep them "
            "synced automatically."
        )
        self.same_as_pop.toggled.connect(self._on_same_toggled)

        sform.addRow("Server:", self.smtp_host)
        sform.addRow("Port:", self.smtp_port)
        sform.addRow("Security:", self.smtp_security)
        sform.addRow("", self.same_as_pop)
        sform.addRow("Username:", self.smtp_user)
        sform.addRow("Password:", self.smtp_pass)
        sform.addRow("", self.smtp_test_btn)
        tabs.addTab(smtp_tab, "Outgoing (SMTP)")

        # IMAP tab — only used to fetch the Junk folder from the server,
        # because POP3 cannot list folders. When IMAP is enabled and a
        # purge threshold is set, deletions are mirrored to the server
        # automatically (standard IMAP behavior).
        imap_tab = QWidget()
        iform = QFormLayout(imap_tab)
        iform.setLabelAlignment(Qt.AlignRight)
        iform.setHorizontalSpacing(14)
        iform.setVerticalSpacing(8)

        imap_intro = QLabel(
            "POP3 only delivers your <b>Inbox</b>. To also see emails the "
            "server flagged as <b>Junk/Spam</b>, enable IMAP here. Reading "
            "is non-destructive; deletions only happen if you set the "
            "auto-purge threshold below."
        )
        imap_intro.setTextFormat(Qt.RichText)
        imap_intro.setStyleSheet("color:#605e5c; padding:0 0 6px 0;")
        imap_intro.setWordWrap(True)
        iform.addRow(imap_intro)

        self.imap_enabled = QCheckBox("Fetch server-side Junk folder via IMAP")
        self.imap_enabled.toggled.connect(self._on_imap_toggled)
        iform.addRow("", self.imap_enabled)

        self.imap_host = QLineEdit()
        self.imap_host.setPlaceholderText("e.g. mailbox.tunasgroup.com (often same as POP3)")
        self.imap_port = QSpinBox()
        self.imap_port.setRange(1, 65535)
        self.imap_port.setValue(993)
        self.imap_ssl = QCheckBox("Use SSL/TLS")
        self.imap_ssl.setChecked(True)
        self.junk_folder = QLineEdit()
        self.junk_folder.setPlaceholderText("(auto-detect on first sync)")
        self.junk_folder.setToolTip(
            "Leave blank to auto-detect (tries Junk, Spam, INBOX/Junk, etc.). "
            "Or specify exactly, e.g. 'Junk' or 'INBOX/Junk'."
        )

        self.imap_copy_pop_btn = QPushButton("Copy from POP3")
        self.imap_copy_pop_btn.setToolTip("Use the same host as POP3 (most servers accept both).")
        self.imap_copy_pop_btn.clicked.connect(self._copy_pop_to_imap)

        self.imap_test_btn = QPushButton("Test IMAP && detect Junk folder")
        self.imap_test_btn.clicked.connect(self._test_imap)

        iform.addRow("Server:", self.imap_host)
        iform.addRow("Port:", self.imap_port)
        iform.addRow("", self.imap_ssl)
        iform.addRow("Junk folder:", self.junk_folder)

        # Side-by-side action buttons (compact)
        btn_row = QHBoxLayout()
        btn_row.setContentsMargins(0, 0, 0, 0)
        btn_row.setSpacing(8)
        btn_row.addWidget(self.imap_copy_pop_btn)
        btn_row.addWidget(self.imap_test_btn, 1)
        btn_wrap = QWidget()
        btn_wrap.setLayout(btn_row)
        iform.addRow("", btn_wrap)

        # ---- Auto-purge ----
        purge_label = QLabel(
            '<div style="margin-top:14px; padding-top:10px; '
            'border-top:1px solid #edebe9;">'
            '<b style="color:#201f1e;">Auto-purge old Junk</b><br>'
            '<span style="color:#605e5c;">'
            "Periodically delete Junk older than the threshold. "
            "Runs as part of every Send/Receive. "
            "Deletions also remove the message from the server "
            "(standard IMAP behavior)."
            "</span></div>"
        )
        purge_label.setTextFormat(Qt.RichText)
        purge_label.setWordWrap(True)
        iform.addRow(purge_label)

        self.junk_purge_days = QSpinBox()
        self.junk_purge_days.setRange(0, 365)
        self.junk_purge_days.setSuffix(" days")
        self.junk_purge_days.setSpecialValueText("Disabled")
        self.junk_purge_days.setValue(0)
        self.junk_purge_days.setToolTip(
            "0 = never auto-purge.\n"
            "30 = delete junk older than 30 days.\n"
            "Recommended: 30–90 days."
        )
        # Hidden but kept for backward compatibility — when purge is enabled
        # and IMAP is active, server delete is implicit (standard IMAP).
        self.junk_purge_server = QCheckBox()
        self.junk_purge_server.setVisible(False)
        self.junk_purge_server.setChecked(True)

        iform.addRow("Delete junk older than:", self.junk_purge_days)

        tabs.addTab(imap_tab, "Junk via IMAP")

        # Initial state: disabled until checkbox ticked
        self._on_imap_toggled(False)

        # Signature tab
        sig_tab = QWidget()
        sig_layout = QVBoxLayout(sig_tab)
        sig_info = QLabel(
            "Your signature is appended automatically to the bottom of every "
            "new email and reply. <b>Paste directly from Outlook</b> — colors, "
            "bold, links, and images are preserved. External images are "
            "auto-downloaded and embedded so the signature works offline and "
            "reaches your recipients intact."
        )
        sig_info.setTextFormat(Qt.RichText)
        sig_info.setStyleSheet("color:#605e5c;")
        sig_info.setWordWrap(True)
        sig_layout.addWidget(sig_info)

        # Formatting toolbar (compact)
        sig_toolbar = QHBoxLayout()
        sig_toolbar.setContentsMargins(0, 4, 0, 4)
        sig_toolbar.setSpacing(4)

        def _mk_fmt_btn(label, slot, italic=False, bold=False, underline=False):
            btn = QPushButton(label)
            btn.setMaximumWidth(36)
            btn.setMinimumWidth(36)
            btn.setStyleSheet(
                "QPushButton { padding:4px; min-width:36px; }"
            )
            f = btn.font()
            if bold: f.setBold(True)
            if italic: f.setItalic(True)
            if underline: f.setUnderline(True)
            btn.setFont(f)
            btn.clicked.connect(slot)
            return btn

        sig_toolbar.addWidget(_mk_fmt_btn("B", self._sig_bold, bold=True))
        sig_toolbar.addWidget(_mk_fmt_btn("I", self._sig_italic, italic=True))
        sig_toolbar.addWidget(_mk_fmt_btn("U", self._sig_underline, underline=True))
        sig_toolbar.addSpacing(8)
        clear_btn = QPushButton("Clear formatting")
        clear_btn.setStyleSheet("QPushButton { padding:4px 10px; }")
        clear_btn.clicked.connect(self._sig_clear_format)
        sig_toolbar.addWidget(clear_btn)
        sig_toolbar.addSpacing(8)

        # Template inserter
        tpl_btn = QPushButton("Use Tunas template")
        tpl_btn.setStyleSheet("QPushButton { padding:4px 10px; }")
        tpl_btn.setToolTip(
            "Insert a Tunas Group corporate signature with your name, role, "
            "and phone. Replaces current signature content."
        )
        tpl_btn.clicked.connect(self._insert_tunas_template)
        sig_toolbar.addWidget(tpl_btn)

        sig_toolbar.addStretch(1)
        # Status (paste image download progress)
        self.sig_status = QLabel("")
        self.sig_status.setStyleSheet("color:#605e5c; font-size:9pt;")
        sig_toolbar.addWidget(self.sig_status)
        sig_layout.addLayout(sig_toolbar)

        from .paste_aware_edit import PasteAwareTextEdit
        self.signature_edit = PasteAwareTextEdit()
        self.signature_edit.setPlaceholderText(
            "Paste your signature here, e.g. from Outlook web.\n\n"
            "Best regards,\nKhatar Malayki | IT Operational\n"
            "Tel: 021-7486 1000\nkhatar@tunasgroup.com"
        )
        sig_layout.addWidget(self.signature_edit, 1)
        tabs.addTab(sig_tab, "Signature")

        # Hint
        hint = QLabel(
            '<i>Tip: For Gmail/Outlook, enable "App Password" or POP3 access first.</i>'
        )
        hint.setWordWrap(True)
        layout.addWidget(hint)

        # Buttons
        btns = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        btns.accepted.connect(self._save)
        btns.rejected.connect(self.reject)
        layout.addWidget(btns)

        # Auto-sync user fields when email changes
        self.email_edit.textChanged.connect(self._sync_users)

    def _sync_users(self, value):
        if not self.pop_user.text():
            self.pop_user.setPlaceholderText(value or "(default = email address)")
        if not self.smtp_user.text():
            self.smtp_user.setPlaceholderText(value or "(default = email address)")
        # If "same as POP3" is checked, keep mirroring
        if hasattr(self, "same_as_pop") and self.same_as_pop.isChecked():
            self._mirror_pop_to_smtp()

    def _on_same_toggled(self, checked: bool):
        # Disable the SMTP user/pass fields when checked, and mirror values.
        self.smtp_user.setEnabled(not checked)
        self.smtp_pass.setEnabled(not checked)
        if checked:
            self._mirror_pop_to_smtp()
            # Mirror live as user types in POP fields too
            try:
                self.pop_user.textChanged.disconnect(self._mirror_pop_to_smtp)
                self.pop_pass.textChanged.disconnect(self._mirror_pop_to_smtp)
            except TypeError:
                pass
            self.pop_user.textChanged.connect(self._mirror_pop_to_smtp)
            self.pop_pass.textChanged.connect(self._mirror_pop_to_smtp)
        else:
            try:
                self.pop_user.textChanged.disconnect(self._mirror_pop_to_smtp)
                self.pop_pass.textChanged.disconnect(self._mirror_pop_to_smtp)
            except TypeError:
                pass

    def _mirror_pop_to_smtp(self, *_):
        self.smtp_user.setText(self.pop_user.text())
        self.smtp_pass.setText(self.pop_pass.text())

    def _populate(self, a):
        self.name_edit.setText(a.get("name") or "")
        self.email_edit.setText(a.get("email") or "")
        self.pop_host.setText(a.get("pop3_host") or "")
        self.pop_port.setValue(int(a.get("pop3_port") or 995))
        self.pop_ssl.setChecked(bool(a.get("pop3_ssl")))
        self.pop_user.setText(a.get("pop3_user") or "")
        self.pop_pass.setText(a.get("pop3_password") or "")
        self.leave_on_server.setChecked(bool(a.get("leave_on_server")))
        self.smtp_host.setText(a.get("smtp_host") or "")
        self.smtp_port.setValue(int(a.get("smtp_port") or 465))
        idx = self.smtp_security.findText((a.get("smtp_security") or "SSL").upper())
        if idx >= 0:
            self.smtp_security.setCurrentIndex(idx)
        self.smtp_user.setText(a.get("smtp_user") or "")
        self.smtp_pass.setText(a.get("smtp_password") or "")
        # Signature: stored as HTML; if it looks like plain text, treat it as such
        sig = a.get("signature") or ""
        if "<" in sig and ">" in sig:
            self.signature_edit.setHtml(sig)
            # Re-apply image size cap in case it was stored before we
            # added the constraint (legacy oversized signatures).
            try:
                self.signature_edit.shrink_oversized_images(400)
            except Exception:
                pass
        else:
            self.signature_edit.setPlainText(sig)
        # Auto-detect "same as POP3" state by comparing user/pass
        same = (
            (a.get("smtp_user") or "") == (a.get("pop3_user") or "")
            and (a.get("smtp_password") or "") == (a.get("pop3_password") or "")
        )
        if same:
            self.same_as_pop.setChecked(True)
        # IMAP fields
        self.imap_enabled.setChecked(bool(a.get("imap_enabled")))
        self.imap_host.setText(a.get("imap_host") or "")
        self.imap_port.setValue(int(a.get("imap_port") or 993))
        self.imap_ssl.setChecked(bool(a.get("imap_ssl") if a.get("imap_ssl") is not None else 1))
        self.junk_folder.setText(a.get("junk_folder_name") or "")
        self.junk_purge_days.setValue(int(a.get("junk_purge_days") or 0))
        self.junk_purge_server.setChecked(bool(a.get("junk_purge_server")))
        self._on_imap_toggled(self.imap_enabled.isChecked())

    def _collect(self) -> dict:
        email = self.email_edit.text().strip()
        # If signature has any rich content, store HTML; else plain text
        sig_doc = self.signature_edit.document()
        if any(self._has_rich_format(sig_doc.findBlockByNumber(i))
               for i in range(sig_doc.blockCount())):
            signature = self.signature_edit.toHtml()
        else:
            signature = self.signature_edit.toPlainText()
        return {
            "name": self.name_edit.text().strip() or email,
            "email": email,
            "pop3_host": self.pop_host.text().strip(),
            "pop3_port": self.pop_port.value(),
            "pop3_ssl": 1 if self.pop_ssl.isChecked() else 0,
            "pop3_user": self.pop_user.text().strip() or email,
            "pop3_password": self.pop_pass.text(),
            "leave_on_server": 1 if self.leave_on_server.isChecked() else 0,
            "smtp_host": self.smtp_host.text().strip(),
            "smtp_port": self.smtp_port.value(),
            "smtp_security": self.smtp_security.currentText(),
            "smtp_user": self.smtp_user.text().strip() or email,
            "smtp_password": self.smtp_pass.text(),
            "signature": signature,
            "imap_enabled": 1 if self.imap_enabled.isChecked() else 0,
            "imap_host": self.imap_host.text().strip(),
            "imap_port": self.imap_port.value(),
            "imap_ssl": 1 if self.imap_ssl.isChecked() else 0,
            "junk_folder_name": self.junk_folder.text().strip(),
            "junk_purge_days": self.junk_purge_days.value(),
            "junk_purge_server": 1 if self.junk_purge_server.isChecked() else 0,
        }

    @staticmethod
    def _has_rich_format(block) -> bool:
        """Return True if a QTextBlock has any non-default formatting."""
        if not block.isValid():
            return False
        it = block.begin()
        while not it.atEnd():
            frag = it.fragment()
            if frag.isValid():
                fmt = frag.charFormat()
                if (fmt.fontWeight() != QFont.Normal
                        or fmt.fontItalic()
                        or fmt.fontUnderline()
                        or fmt.foreground().color().name() != "#000000"
                        or fmt.isImageFormat()):
                    return True
            it += 1
        return False

    # ---- Signature formatting helpers ----
    def _sig_bold(self):
        cursor = self.signature_edit.textCursor()
        fmt = QTextCharFormat()
        new_weight = (
            QFont.Normal if cursor.charFormat().fontWeight() > QFont.Normal
            else QFont.Bold
        )
        fmt.setFontWeight(new_weight)
        cursor.mergeCharFormat(fmt)
        self.signature_edit.mergeCurrentCharFormat(fmt)

    def _sig_italic(self):
        cursor = self.signature_edit.textCursor()
        fmt = QTextCharFormat()
        fmt.setFontItalic(not cursor.charFormat().fontItalic())
        cursor.mergeCharFormat(fmt)
        self.signature_edit.mergeCurrentCharFormat(fmt)

    def _sig_underline(self):
        cursor = self.signature_edit.textCursor()
        fmt = QTextCharFormat()
        fmt.setFontUnderline(not cursor.charFormat().fontUnderline())
        cursor.mergeCharFormat(fmt)
        self.signature_edit.mergeCurrentCharFormat(fmt)

    def _sig_clear_format(self):
        cursor = self.signature_edit.textCursor()
        cursor.select(cursor.Document)
        plain = cursor.selection().toPlainText()
        self.signature_edit.setPlainText(plain)

    def _insert_tunas_template(self):
        """Insert the Tunas Group corporate signature template via a single
        form dialog (name, email, role, phone, address)."""
        if self.signature_edit.toPlainText().strip():
            ret = QMessageBox.question(
                self, "Replace existing signature?",
                "Inserting the template will replace your current signature.\n\n"
                "Continue?",
                QMessageBox.Yes | QMessageBox.Cancel,
                QMessageBox.Cancel,
            )
            if ret != QMessageBox.Yes:
                return

        from .template_form_dialog import TunasTemplateForm
        form = TunasTemplateForm(
            self,
            default_name=self.name_edit.text().strip(),
            default_email=self.email_edit.text().strip(),
        )
        if form.exec_() != QDialog.Accepted or not form.result_data:
            return

        d = form.result_data
        from core.sig_templates import render_tunas
        html = render_tunas(
            name=d["name"],
            role=d["role"],
            phone=d["phone"],
            email=d["email"],
            address=d["address"],
        )
        self.signature_edit.setHtml(html)
        # Auto-embed external images so the signature works offline
        from .paste_aware_edit import _embed_images_in_html
        try:
            embedded, downloaded, failed = _embed_images_in_html(
                self.signature_edit.toHtml()
            )
            self.signature_edit.setHtml(embedded)
            if downloaded:
                QMessageBox.information(
                    self, "Template inserted",
                    f"Tunas template inserted with {downloaded} image(s) "
                    f"downloaded and embedded. Signature will work offline.",
                )
        except Exception as e:
            QMessageBox.warning(
                self, "Template inserted (images may be missing)",
                f"Could not download all images: {e}\n\n"
                f"You can re-save later when online.",
            )

    def _validate(self) -> bool:
        d = self._collect()
        if not d["email"] or "@" not in d["email"]:
            QMessageBox.warning(self, "Invalid", "Please enter a valid email address.")
            return False
        if not d["pop3_host"] or not d["pop3_password"]:
            QMessageBox.warning(self, "Invalid", "POP3 host and password are required.")
            return False
        if not d["smtp_host"]:
            QMessageBox.warning(self, "Invalid", "SMTP host is required.")
            return False
        return True

    def _test_pop(self):
        if not self._validate():
            return
        self.setCursor(Qt.WaitCursor)
        ok, msg = pop3_client.test_connection(self._collect())
        self.unsetCursor()
        if ok:
            QMessageBox.information(self, "POP3 Test", msg)
        else:
            QMessageBox.critical(self, "POP3 Test failed", msg)

    def _test_smtp(self):
        if not self._validate():
            return
        self.setCursor(Qt.WaitCursor)
        ok, msg = smtp_client.test_connection(self._collect())
        self.unsetCursor()
        if ok:
            QMessageBox.information(self, "SMTP Test", msg)
        else:
            QMessageBox.critical(self, "SMTP Test failed", msg)

    # ---- IMAP (Junk fetch) ----
    def _on_imap_toggled(self, checked: bool):
        for w in (self.imap_host, self.imap_port, self.imap_ssl,
                  self.junk_folder, self.imap_copy_pop_btn, self.imap_test_btn,
                  self.junk_purge_days):
            w.setEnabled(checked)

    def _copy_pop_to_imap(self):
        host = self.pop_host.text().strip()
        if host:
            self.imap_host.setText(host)
        self.imap_ssl.setChecked(self.pop_ssl.isChecked())
        self.imap_port.setValue(993 if self.imap_ssl.isChecked() else 143)

    def _test_imap(self):
        if not self._validate():
            return
        d = self._collect()
        if not d.get("imap_host"):
            QMessageBox.warning(self, "Missing host",
                                "Please enter an IMAP host or click 'Copy from POP3'.")
            return
        self.setCursor(Qt.WaitCursor)
        ok, msg = imap_client.test_connection(d)
        self.unsetCursor()
        if ok:
            # Auto-fill detected folder name if user left it blank
            if not self.junk_folder.text().strip():
                detected = None
                try:
                    detected = imap_client.detect_junk_folder(d)
                except Exception:
                    pass
                if detected:
                    self.junk_folder.setText(detected)
            QMessageBox.information(self, "IMAP Test", msg)
        else:
            QMessageBox.critical(self, "IMAP Test failed", msg)

    def _save(self):
        if not self._validate():
            return
        data = self._collect()
        if self.account:
            database.update_account(self.account["id"], data)
        else:
            database.add_account(data)
        self.accept()
