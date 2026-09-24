"""Manage per-account local color categories."""

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QColor, QIcon, QPixmap
from PyQt5.QtWidgets import (
    QAbstractItemView, QColorDialog, QDialog, QDialogButtonBox, QHBoxLayout,
    QInputDialog, QListWidget, QListWidgetItem, QMessageBox, QPushButton,
    QVBoxLayout,
)

from core import database


class CategoryManagerDialog(QDialog):
    def __init__(self, account_id: int, parent=None):
        super().__init__(parent)
        self.account_id = account_id
        self.changed = False
        self.setWindowTitle("Manage Categories")
        self.setMinimumSize(420, 360)

        layout = QVBoxLayout(self)
        self.list_widget = QListWidget()
        self.list_widget.setSelectionMode(QAbstractItemView.SingleSelection)
        self.list_widget.itemDoubleClicked.connect(self._edit)
        layout.addWidget(self.list_widget, 1)

        actions = QHBoxLayout()
        new_btn = QPushButton("New")
        edit_btn = QPushButton("Edit")
        delete_btn = QPushButton("Delete")
        new_btn.clicked.connect(self._new)
        edit_btn.clicked.connect(self._edit)
        delete_btn.clicked.connect(self._delete)
        actions.addWidget(new_btn)
        actions.addWidget(edit_btn)
        actions.addWidget(delete_btn)
        actions.addStretch(1)
        layout.addLayout(actions)

        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self._reload()

    @staticmethod
    def _color_icon(color: str) -> QIcon:
        pixmap = QPixmap(18, 18)
        pixmap.fill(QColor(color))
        return QIcon(pixmap)

    def _reload(self):
        self.list_widget.clear()
        for category in database.list_categories(self.account_id):
            item = QListWidgetItem(
                self._color_icon(category["color"]), category["name"]
            )
            item.setData(Qt.UserRole, category)
            self.list_widget.addItem(item)

    def _selected(self):
        item = self.list_widget.currentItem()
        return item.data(Qt.UserRole) if item else None

    def _ask_name_color(self, title: str, name: str="", color: str="#0078d4"):
        name, ok = QInputDialog.getText(self, title, "Category name:", text=name)
        name = name.strip()
        if not ok or not name:
            return None
        chosen = QColorDialog.getColor(QColor(color), self, "Category color")
        if not chosen.isValid():
            return None
        return name, chosen.name()

    def _new(self):
        value = self._ask_name_color("New Category")
        if not value:
            return
        try:
            database.create_category(self.account_id, *value)
        except Exception as exc:
            QMessageBox.warning(self, "Category", str(exc))
            return
        self.changed = True
        self._reload()

    def _edit(self, *_):
        category = self._selected()
        if not category:
            return
        value = self._ask_name_color(
            "Edit Category", category["name"], category["color"]
        )
        if not value:
            return
        try:
            database.update_category(category["id"], *value)
        except Exception as exc:
            QMessageBox.warning(self, "Category", str(exc))
            return
        self.changed = True
        self._reload()

    def _delete(self):
        category = self._selected()
        if not category:
            return
        if QMessageBox.question(
            self, "Delete Category",
            f'Delete category "{category["name"]}" from all emails?',
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        ) != QMessageBox.Yes:
            return
        database.delete_category(category["id"])
        self.changed = True
        self._reload()
