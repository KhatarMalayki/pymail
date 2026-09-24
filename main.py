"""
RunLab Mail - Desktop Email Client
Entry point.
"""
import sys
from pathlib import Path
from PyQt5.QtWidgets import QApplication, QMessageBox, QDialog
from PyQt5.QtCore import Qt, QTimer

from core import license as licmod
from core.single_instance import SingleInstance
from core import install_location
from ui.main_window import MainWindow


def main():
    QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)

    app = QApplication(sys.argv)
    app.setApplicationName("RunLab Mail")
    app.setOrganizationName("RunLab")
    app.setStyle("Fusion")
    app.setQuitOnLastWindowClosed(True)

    from ui.theme import apply_theme
    apply_theme(app)

    _set_app_icon(app)

    # Release builds use this isolated mode to prove that the frozen EXE can
    # bootstrap and run its Qt event loop. It deliberately bypasses the
    # single-instance lock, license dialogs, and user database so smoke tests
    # remain valid while the installed RunLab Mail is already open.
    if "--smoke-test" in sys.argv:
        QTimer.singleShot(10_000, app.quit)
        sys.exit(app.exec_())

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
        # License exists but is invalid (revoked, expired, tampered, or bound
        # to another device). Give the user a chance to paste a fresh key the
        # admin generated for them. Only quit if they cancel or paste nothing
        # valid.
        from ui.license_dialog import LicenseActivationDialog
        dlg = LicenseActivationDialog(
            error=(
                f"{err}\n\n"
                "Your RunLab Mail license is no longer valid. Paste a new "
                "license key from your administrator below, or send them your "
                "Machine ID to get one."
            )
        )
        if dlg.exec_() == QDialog.Accepted and dlg.payload:
            # User pasted a valid key — adopt it and continue into the app.
            payload = dlg.payload
        else:
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
            None, "RunLab Mail",
            f"You launched RunLab Mail from a cloud-synced folder ({hint}).\n\n"
            f"RunLab Mail is already installed at:\n  {target}\n\n"
            f"Please use that one instead. This launcher will exit.",
        )
        sys.exit(0)
    ret = QMessageBox.question(
        None, "Relocate RunLab Mail",
        f"RunLab Mail is currently running from a cloud-synced folder ({hint}).\n\n"
        f"This breaks auto-updates because the sync agent locks the file.\n\n"
        f"Move RunLab Mail to:\n  {target}\n\n"
        f"A Desktop shortcut will be created automatically.",
        QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes,
    )
    if ret != QMessageBox.Yes:
        return True
    shortcut = install_location.create_desktop_shortcut(target)
    msg = f"RunLab Mail will now move to:\n  {target}\n"
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
