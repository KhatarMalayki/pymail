r"""
Detect when PyMail.exe is running from a cloud-synced folder (OneDrive,
Dropbox, Google Drive, iCloud) and offer to relocate to a safe local
folder. Cloud-sync folders break auto-updates because:

  - The sync agent locks the .exe while uploading new versions
  - Files On-Demand mode can convert local files to cloud-only placeholders,
    making PyInstaller fail to read its own bundle.

Safe location: %LocalAppData%\PyMail\PyMail.exe
LocalAppData is per-user, never synced, always local.
"""
import os
import shutil
import subprocess
import sys
from pathlib import Path

# Folder name fragments that indicate cloud sync.
# We check both the env var name and substrings of the path to catch
# corporate OneDrive setups like "OneDrive - PT. Tunas Ridean Tbk".
_CLOUD_HINTS = (
    "OneDrive",
    "OneDrive - ",
    "Dropbox",
    "Google Drive",
    "GoogleDrive",
    "iCloudDrive",
    "iCloud Drive",
    "Box Sync",
)


def is_in_cloud_folder(path: Path) -> tuple[bool, str]:
    """Return (True, name) if path is inside a known cloud-sync folder."""
    p_str = str(path).replace("\\", "/").lower()
    for hint in _CLOUD_HINTS:
        if f"/{hint.lower()}" in p_str:
            return True, hint
    # Also check Windows reparse-point flag (OneDrive Files On-Demand)
    try:
        attrs = path.stat().st_file_attributes if hasattr(path.stat(), "st_file_attributes") else 0
        # 0x400 = FILE_ATTRIBUTE_REPARSE_POINT
        if attrs & 0x400:
            return True, "Cloud sync (reparse point)"
    except Exception:
        pass
    return False, ""


def safe_install_dir() -> Path:
    """Return the recommended local-only install directory."""
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~/AppData/Local")
    return Path(base) / "PyMail"


def safe_install_path() -> Path:
    return safe_install_dir() / "PyMail.exe"


def relocate_self(current_exe: Path, target_exe: Path) -> None:
    """Copy the current .exe to target_exe, then spawn it and exit.

    Uses a small helper batch script so we don't need write access to our
    own image while running.
    """
    target_exe.parent.mkdir(parents=True, exist_ok=True)
    pid = os.getpid()
    import tempfile
    helper_dir = Path(tempfile.gettempdir()) / "pymail_relocate"
    helper_dir.mkdir(parents=True, exist_ok=True)
    bat = helper_dir / "do_relocate.bat"
    vbs = helper_dir / "do_relocate.vbs"

    bat.write_text(
        f"""@echo off
setlocal
set /a tries=0
:wait
tasklist /FI "PID eq {pid}" 2>NUL | find " {pid} " >NUL
if errorlevel 1 goto copy
set /a tries+=1
if %tries% GEQ 30 goto force
ping -n 2 127.0.0.1 >NUL
goto wait

:force
taskkill /PID {pid} /F >NUL 2>&1
ping -n 2 127.0.0.1 >NUL

:copy
copy /Y "{current_exe}" "{target_exe}" >NUL
if errorlevel 1 exit /b 1
start "" "{target_exe}"
ie4uinit.exe -show >NUL 2>&1
exit /b 0
""",
        encoding="ascii",
    )
    vbs.write_text(
        f'Set sh = CreateObject("WScript.Shell")\r\n'
        f'sh.Run "cmd /c ""{bat}""", 0, False\r\n',
        encoding="ascii",
    )
    DETACHED_PROCESS = 0x00000008
    CREATE_NEW_PROCESS_GROUP = 0x00000200
    CREATE_NO_WINDOW = 0x08000000
    subprocess.Popen(
        ["wscript.exe", str(vbs)],
        creationflags=DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW,
        close_fds=True,
    )
    sys.exit(0)


def create_desktop_shortcut(target_exe: Path) -> Path | None:
    """Create a Desktop shortcut pointing at the safely-located .exe.

    Tries the local Desktop first, falls back to OneDrive Desktop if that's
    where the user lives.
    """
    desktop_candidates = []
    user_desktop = Path(os.path.expanduser("~/Desktop"))
    if user_desktop.is_dir():
        desktop_candidates.append(user_desktop)
    onedrive = os.environ.get("OneDrive") or os.environ.get("OneDriveCommercial")
    if onedrive:
        od_desk = Path(onedrive) / "Desktop"
        if od_desk.is_dir():
            desktop_candidates.append(od_desk)

    if not desktop_candidates:
        return None

    shortcut_path = desktop_candidates[0] / "PyMail.lnk"

    # Use PowerShell to create the .lnk (no extra deps)
    ps_script = (
        f'$s = (New-Object -ComObject WScript.Shell).CreateShortcut('
        f'"{shortcut_path}"); '
        f'$s.TargetPath = "{target_exe}"; '
        f'$s.IconLocation = "{target_exe},0"; '
        f'$s.WorkingDirectory = "{target_exe.parent}"; '
        f'$s.Save()'
    )
    try:
        subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps_script],
            check=True, capture_output=True,
        )
        return shortcut_path
    except Exception:
        return None
