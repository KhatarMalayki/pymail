"""
Ribbon-style toolbar widget (Outlook-like) for PyQt5.

Usage:
    ribbon = RibbonToolbar()
    
    # Add tab
    home_tab = ribbon.add_tab("Home")
    
    # Add group to tab
    new_group = home_tab.add_group("New")
    new_group.add_button("New Email", icon, callback)
    
    # Set current tab
    ribbon.set_current_tab(0)
"""
from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QToolButton,
    QTabWidget, QFrame, QSizePolicy, QSpacerItem
)
from PyQt5.QtGui import QIcon, QFont
from .theme import color as theme_color


class RibbonGroup(QFrame):
    """A group of related buttons within a ribbon tab."""
    
    def __init__(self, title: str, parent=None):
        super().__init__(parent)
        self.setFrameShape(QFrame.NoFrame)
        self.setStyleSheet("""
            RibbonGroup {
                border: none;
                background-color: transparent;
            }
        """)
        
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 4, 6, 4)
        layout.setSpacing(4)
        
        # Button container (horizontal)
        self.btn_layout = QHBoxLayout()
        self.btn_layout.setSpacing(6)
        layout.addLayout(self.btn_layout)
        
        # Group title label (bottom)
        self.title_label = QLabel(title)
        self.title_label.setAlignment(Qt.AlignCenter)
        self.title_label.setStyleSheet(f"""
            color: {theme_color('text_muted')};
            font-size: 11px;
            font-weight: 500;
            padding-top: 4px;
            border-top: 1px solid {theme_color('border')};
        """)
        layout.addWidget(self.title_label)
        
    def add_button(self, text: str, icon=None, callback=None, large=False, tooltip=""):
        """Add a button to this group."""
        from PyQt5.QtCore import QSize
        btn = QToolButton()
        btn.setText(text)
        if icon:
            btn.setIcon(icon)
            btn.setIconSize(QSize(20, 20))
        if tooltip:
            btn.setToolTip(tooltip)
        
        # Modern button styling
        btn.setStyleSheet(f"""
            QToolButton {{
                background-color: transparent;
                border: 1px solid transparent;
                border-radius: 4px;
                color: {theme_color('text_subtle')};
            }}
            QToolButton:hover {{
                background-color: {theme_color('bg_hover')};
                border-color: {theme_color('border')};
            }}
            QToolButton:pressed {{
                background-color: {theme_color('bg_pressed')};
            }}
        """)
        
        if large:
            # Large button: icon on top, label below (Outlook-style). The
            # label is shown so the action is clear even before hovering.
            btn.setToolButtonStyle(Qt.ToolButtonTextUnderIcon)
            btn.setIconSize(QSize(28, 28))
            btn.setMinimumSize(58, 64)
            btn.setMaximumHeight(64)
            btn.setStyleSheet(btn.styleSheet() + """
                QToolButton { padding: 4px 6px; font-size: 11px; }
            """)
        else:
            # Small button: icon + text
            btn.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
            btn.setMinimumSize(40, 36)
            btn.setMaximumHeight(36)
        
        if callback:
            btn.clicked.connect(callback)
        
        self.btn_layout.addWidget(btn)
        return btn
    
    def add_separator(self):
        """Add vertical separator."""
        sep = QFrame()
        sep.setFrameShape(QFrame.VLine)
        sep.setFrameShadow(QFrame.Sunken)
        sep.setStyleSheet(f"color: {theme_color('border_strong')};")
        sep.setMaximumWidth(2)
        self.btn_layout.addWidget(sep)


class RibbonTab(QWidget):
    """A single tab in the ribbon containing multiple groups."""
    
    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 4, 8, 4)
        layout.setSpacing(8)
        layout.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        self.setLayout(layout)
        self._groups = []
    
    def add_group(self, title: str) -> RibbonGroup:
        """Add a new group to this tab."""
        group = RibbonGroup(title)
        self.layout().addWidget(group)
        self._groups.append(group)
        return group
    
    def add_spacer(self):
        """Add expanding spacer at the end."""
        spacer = QSpacerItem(20, 20, QSizePolicy.Expanding, QSizePolicy.Minimum)
        self.layout().addSpacerItem(spacer)


class RibbonToolbar(QTabWidget):
    """Main ribbon toolbar widget."""
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.apply_theme()
        self.setDocumentMode(True)
        self.setMaximumHeight(140)
        self._tabs = []

    def apply_theme(self):
        self.setStyleSheet(f"""
            QTabWidget::pane {{
                border: none;
                border-bottom: 1px solid {theme_color('border')};
                background-color: {theme_color('bg_sidebar')};
            }}
            QTabBar::tab {{
                background-color: {theme_color('bg_sidebar')};
                border: none;
                border-bottom: 3px solid transparent;
                padding: 10px 24px;
                font-weight: 600;
                font-size: 13px;
                color: {theme_color('text_muted')};
                margin-right: 2px;
                min-width: 60px;
            }}
            QTabBar::tab:selected {{
                background-color: {theme_color('bg')};
                color: {theme_color('primary')};
                border-bottom: 3px solid {theme_color('primary')};
            }}
            QTabBar::tab:hover:!selected {{
                background-color: {theme_color('bg_hover')};
                color: {theme_color('text_subtle')};
            }}
        """)
    
    def add_tab(self, title: str) -> RibbonTab:
        """Add a new ribbon tab."""
        tab = RibbonTab()
        self.addTab(tab, title)
        self._tabs.append(tab)
        return tab
    
    def set_current_tab(self, index: int):
        """Set the currently visible tab."""
        self.setCurrentIndex(index)
