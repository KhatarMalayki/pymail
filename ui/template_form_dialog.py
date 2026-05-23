"""
Single-form dialog for filling in Tunas signature template fields.
Replaces the chain of QInputDialog popups with one tidy form.
"""
from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QFormLayout, QLineEdit, QLabel, QDialogButtonBox,
)


class TunasTemplateForm(QDialog):
    def __init__(self, parent=None,
                 default_name: str = "",
                 default_email: str = "",
                 default_role: str = "IT Operational",
                 default_phone: str = "021-7486 1000",
                 default_address: str = "Bintaro Komersial CBD B7 Kavling "
                                        "A1/02, Bintaro Jaya, Tangerang 15224"):
        super().__init__(parent)
        self.setWindowTitle("Tunas Signature Template")
        self.resize(560, 320)
        self.result_data: dict | None = None
        self._build_ui(
            default_name, default_email, default_role,
            default_phone, default_address,
        )

    def _build_ui(self, name, email, role, phone, address):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(12)

        intro = QLabel(
            "Fill in the fields below — they'll be inserted into the Tunas "
            "corporate signature template."
        )
        intro.setStyleSheet("color:#605e5c;")
        intro.setWordWrap(True)
        layout.addWidget(intro)

        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)
        form.setHorizontalSpacing(14)
        form.setVerticalSpacing(8)

        self.name_edit = QLineEdit(name)
        self.name_edit.setPlaceholderText("e.g. Mohamad Khatar Malayki")
        form.addRow("Full name:", self.name_edit)

        self.email_edit = QLineEdit(email)
        self.email_edit.setPlaceholderText("e.g. khatar@tunasgroup.com")
        form.addRow("Email:", self.email_edit)

        self.role_edit = QLineEdit(role)
        self.role_edit.setPlaceholderText("e.g. IT Operational")
        form.addRow("Job title:", self.role_edit)

        self.phone_edit = QLineEdit(phone)
        self.phone_edit.setPlaceholderText("e.g. 021-7486 1000")
        form.addRow("Phone:", self.phone_edit)

        self.address_edit = QLineEdit(address)
        self.address_edit.setPlaceholderText(
            "e.g. Bintaro Komersial CBD B7 Kavling A1/02"
        )
        form.addRow("Address:", self.address_edit)

        layout.addLayout(form)

        hint = QLabel(
            "<i style='color:#605e5c;font-size:9pt;'>"
            "External images (logo, social icons, banners) will be downloaded "
            "and embedded so the signature works offline."
            "</i>"
        )
        hint.setTextFormat(Qt.RichText)
        hint.setWordWrap(True)
        layout.addWidget(hint)

        layout.addStretch(1)

        btns = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel
        )
        btns.button(QDialogButtonBox.Ok).setText("Insert template")
        btns.button(QDialogButtonBox.Ok).setDefault(True)
        btns.accepted.connect(self._accept)
        btns.rejected.connect(self.reject)
        layout.addWidget(btns)

    def _accept(self):
        self.result_data = {
            "name": self.name_edit.text().strip(),
            "email": self.email_edit.text().strip(),
            "role": self.role_edit.text().strip(),
            "phone": self.phone_edit.text().strip(),
            "address": self.address_edit.text().strip(),
        }
        self.accept()
