"""
PyMail - Desktop Email Client
Entry point.
"""
import sys
from pathlib import Path
from PyQt5.QtWidgets import QApplication, QMessageBox
from PyQt5.QtCore import Qt

from core import license as licmod
from core.single_instance import SingleInstance
from core import install_location
from ui.main_window import MainWindow


def main():
    QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)

    app = QApplication(sys.argv)
    app.setApplicationName("PyMail")
    app.setOrganizationName("PyMail")
    app.setStyle("Fusion")
    app.setQuitOnLastWindowClosed(True)

    from ui.theme import apply_theme
    apply_theme(app)

    _set_app_icon(app)

    ensure_safe_install_location(app)

    instance = SingleInstance()
    if not instance.acquire():
        sys.exit(0)

    # Load whatever license is currently on disk. May be:
    #   - None (brand-new user — will auto-register on first POP3 fetch)
    #   - Valid license (returning user)
    #   - Revoked/expired license (will block on first sync)
    ok, payload, err = licmod.is_licensed()
    if not ok and payload is None and err == "No license installed.":
        # No license yet — let the user set up an account. The license
        # will be auto-issued when the first POP3 fetch succeeds.
        payload = {"_unactivated": True}
    elif not ok:
        # License exists but is invalid (revoked, expired, tampered)
        # → show a clean explanation and quit. We don't ask for a paste
        # because the new flow is auto-register.
        QMessageBox.critical(
            None, "License invalid",
            f"{err}\n\n"
            f"Your PyMail license is no longer valid. Please contact your "
            f"administrator to extend or reactivate it.",
        )
        instance.release()
        sys.exit(0)

    window = MainWindow(license_payload=payload or {})
    instance.another_instance_started.connect(window.bring_to_front)
    window.showMaximized()
    sys.exit(app.exec_())


def ensure_safe_install_location(app: QApplication) -> bool:
    if not getattr(sys, "frozen", False):
        return True
    current_exe = Path(sys.executable)
    is_cloud, hint = install_location.is_in_cloud_folder(current_exe)
    if not is_cloud:
        return True
    target = install_location.safe_install_path()
    if target.is_file() and target.resolve() != current_exe.resolve():
        QMessageBox.information(
            None, "PyMail",
            f"You launched PyMail from a cloud-synced folder ({hint}).\n\n"
            f"PyMail is already installed at:\n  {target}\n\n"
            f"Please use that one instead. This launcher will exit.",
        )
        sys.exit(0)
    ret = QMessageBox.question(
        None, "Relocate PyMail",
        f"PyMail is currently running from a cloud-synced folder ({hint}).\n\n"
        f"This breaks auto-updates because the sync agent locks the file.\n\n"
        f"Move PyMail to:\n  {target}\n\n"
        f"A Desktop shortcut will be created automatically.",
        QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes,
    )
    if ret != QMessageBox.Yes:
        return True
    shortcut = install_location.create_desktop_shortcut(target)
    msg = f"PyMail will now move to:\n  {target}\n"
    if shortcut:
        msg += f"\nA shortcut was created at:\n  {shortcut}"
    msg += "\n\nThe app will restart automatically."
    QMessageBox.information(None, "Relocating", msg)
    install_location.relocate_self(current_exe, target)
    return False


def _set_app_icon(app: QApplication) -> None:
    from PyQt5.QtGui import QIcon
    if getattr(sys, "frozen", False):
        base = Path(sys._MEIPASS)
    else:
        base = Path(__file__).parent
    ico = base / "resources" / "pymail.ico"
    if ico.is_file():
        app.setWindowIcon(QIcon(str(ico)))


if __name__ == "__main__":
    main()
