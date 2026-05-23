"""
Outlook-style view options bar above the email list.

Provides: sort field, sort direction, group/conversation toggle.
Emits view_changed when any option changes; the email list refreshes.
"""
from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtWidgets import (
    QWidget, QHBoxLayout, QLabel, QToolButton, QMenu, QAction,
)


SORT_FIELDS = [
    ("date_received", "Date"),
    ("sender", "From"),
    ("subject", "Subject"),
    ("raw_size", "Size"),
]


class ViewBar(QWidget):
    view_changed = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.sort_field = "date_received"
        self.sort_desc = True
        self.group_by_conversation = False
        self._build_ui()

    def _build_ui(self):
        self.setStyleSheet(
            "ViewBar { background:#faf9f8; border-bottom:1px solid #e1dfdd; }"
            "QToolButton { padding:4px 8px; border-radius:3px; "
            "color:#605e5c; }"
            "QToolButton:hover { background:#f3f2f1; color:#201f1e; }"
            "QToolButton:checked { color:#0078d4; font-weight:600; }"
            "QLabel { color:#605e5c; }"
        )
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 4, 8, 4)
        layout.setSpacing(4)

        # Sort by (button with menu)
        self.sort_btn = QToolButton()
        self.sort_btn.setPopupMode(QToolButton.InstantPopup)
        self.sort_btn.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.sort_btn.setText(f"By Date ↓")
        self.sort_btn.setToolTip("Change sort field and direction")
        self._build_sort_menu()
        layout.addWidget(self.sort_btn)

        sep = QLabel("  •  ")
        sep.setStyleSheet("color:#c8c6c4;")
        layout.addWidget(sep)

        # Group by conversation toggle
        self.group_btn = QToolButton()
        self.group_btn.setText("Group by conversation")
        self.group_btn.setCheckable(True)
        self.group_btn.setToolTip(
            "Group emails with the same subject under one expandable header "
            "(threading)."
        )
        self.group_btn.toggled.connect(self._on_group_toggled)
        layout.addWidget(self.group_btn)

        layout.addStretch(1)

        # Right-side: count label
        self.count_label = QLabel("")
        self.count_label.setStyleSheet("color:#605e5c; padding-right:6px;")
        layout.addWidget(self.count_label)

    def _build_sort_menu(self):
        menu = QMenu(self.sort_btn)
        menu.setStyleSheet("QMenu { padding:4px 0; }")
        # Field options
        self._field_actions = {}
        for field, label in SORT_FIELDS:
            act = QAction(label, menu)
            act.setCheckable(True)
            act.setChecked(field == self.sort_field)
            act.triggered.connect(lambda _, f=field: self._set_sort_field(f))
            menu.addAction(act)
            self._field_actions[field] = act
        menu.addSeparator()
        self.act_desc = QAction("Newest on top", menu, checkable=True)
        self.act_desc.setChecked(self.sort_desc)
        self.act_desc.triggered.connect(lambda: self._set_direction(True))
        menu.addAction(self.act_desc)
        self.act_asc = QAction("Oldest on top", menu, checkable=True)
        self.act_asc.setChecked(not self.sort_desc)
        self.act_asc.triggered.connect(lambda: self._set_direction(False))
        menu.addAction(self.act_asc)
        self.sort_btn.setMenu(menu)
        self._refresh_button_label()

    def _refresh_button_label(self):
        field_label = next(
            (lbl for f, lbl in SORT_FIELDS if f == self.sort_field), "Date"
        )
        arrow = "↓" if self.sort_desc else "↑"
        self.sort_btn.setText(f"By {field_label} {arrow}")

    def _set_sort_field(self, field: str):
        if self.sort_field == field:
            return
        self.sort_field = field
        for f, act in self._field_actions.items():
            act.setChecked(f == field)
        self._refresh_button_label()
        self.view_changed.emit()

    def _set_direction(self, desc: bool):
        if self.sort_desc == desc:
            # Keep both checks consistent
            self.act_desc.setChecked(desc)
            self.act_asc.setChecked(not desc)
            return
        self.sort_desc = desc
        self.act_desc.setChecked(desc)
        self.act_asc.setChecked(not desc)
        self._refresh_button_label()
        self.view_changed.emit()

    def _on_group_toggled(self, checked: bool):
        self.group_by_conversation = checked
        self.view_changed.emit()

    def set_count(self, count: int):
        self.count_label.setText(f"{count} email{'s' if count != 1 else ''}")