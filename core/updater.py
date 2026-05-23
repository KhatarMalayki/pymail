"""
Self-updater for PyMail.

Workflow:
1. On launch, check_for_updates() fetches a JSON manifest.
2. If manifest['version'] > current __version__, return the manifest.
3. download_and_install(manifest) downloads new .exe to %TEMP%,
   verifies SHA256 if provided, writes a small .bat helper, spawns it
   detached, and exits the app.
4. The .bat waits for our process to exit, replaces the old .exe,
   and re-launches the app.

Only works when running as a frozen PyInstaller .exe.
In source mode it returns None / raises gracefully.
"""
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.request
from datetime import datetime
from pathlib import Path

from .version import __version__, DEFAULT_MANIFEST_URL


# Persistent state file for "last checked" timestamps
_STATE_DIR = Path(os.path.expanduser("~")) / ".pymail"
_STATE_DIR.mkdir(parents=True, exist_ok=True)
_STATE_FILE = _STATE_DIR / "update_state.json"


def _read_state() -> dict:
    if not _STATE_FILE.is_file():
        return {}
    try:
        return json.loads(_STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _write_state(state: dict):
    try:
        _STATE_FILE.write_text(json.dumps(state), encoding="utf-8")
    except Exception:
        pass


def get_last_checked_iso() -> str | None:
    """Return ISO-8601 timestamp of the last successful update check, or None."""
    return _read_state().get("last_checked")


def get_last_checked_human() -> str:
    """Return a friendly 'Last checked' string, or 'never'."""
    iso = get_last_checked_iso()
    if not iso:
        return "never"
    try:
        dt = datetime.fromisoformat(iso)
        return dt.strftime("%d %b %Y, %H:%M:%S")
    except Exception:
        return iso


def _stamp_last_checked():
    state = _read_state()
    state["last_checked"] = datetime.now().isoformat(timespec="seconds")
    _write_state(state)


# Windows: launch detached so the helper survives our exit
DETACHED_PROCESS = 0x00000008
CREATE_NEW_PROCESS_GROUP = 0x00000200
CREATE_NO_WINDOW = 0x08000000


def is_frozen() -> bool:
    """True when running as a PyInstaller-built .exe."""
    return bool(getattr(sys, "frozen", False))


def get_app_path() -> Path:
    """Path to the running .exe (or main script in dev mode)."""
    if is_frozen():
        return Path(sys.executable)
    return Path(sys.argv[0]).resolve()


def get_app_dir() -> Path:
    return get_app_path().parent


def _parse_version(v: str) -> tuple:
    parts = []
    for x in (v or "0").strip().lstrip("vV").split("."):
        try:
            parts.append(int("".join(c for c in x if c.isdigit()) or 0))
        except ValueError:
            parts.append(0)
    return tuple(parts)


def get_manifest_url() -> str:
    """Allow users to override the manifest URL via pymail_update.json
    placed next to the executable."""
    cfg = get_app_dir() / "pymail_update.json"
    if cfg.is_file():
        try:
            data = json.loads(cfg.read_text(encoding="utf-8"))
            url = data.get("manifest_url")
            if url:
                return url
        except Exception:
            pass
    return DEFAULT_MANIFEST_URL


def check_for_updates(timeout: int = 8) -> dict | None:
    """Return manifest dict if a newer version is available, else None.

    Never raises. On any error returns None. Used by silent background checks.
    For the explicit/manual check that distinguishes "no update" from
    "network error", use check_now() instead.
    """
    try:
        url = get_manifest_url()
        req = urllib.request.Request(
            url, headers={"User-Agent": f"PyMail/{__version__}"},
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        _stamp_last_checked()
        latest = str(data.get("version", "0"))
        if _parse_version(latest) > _parse_version(__version__):
            return data
    except Exception:
        return None
    return None


def check_now(timeout: int = 8) -> tuple[str, object]:
    """Explicit update check. Returns one of:

        ("update",  manifest_dict)  — newer version available
        ("ok",      None)           — server reachable, already latest
        ("error",   error_message)  — network/parse failure

    The "Last checked" timestamp is stamped only on "update" or "ok"
    (i.e. when the server actually replied), never on "error".
    """
    try:
        url = get_manifest_url()
        req = urllib.request.Request(
            url, headers={"User-Agent": f"PyMail/{__version__}"},
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.URLError as e:
        return ("error", _friendly_network_error(e))
    except (TimeoutError, OSError) as e:
        return ("error", _friendly_network_error(e))
    except json.JSONDecodeError:
        return ("error", "Update server returned invalid data.")
    except Exception as e:
        return ("error", str(e))

    _stamp_last_checked()
    latest = str(data.get("version", "0"))
    if _parse_version(latest) > _parse_version(__version__):
        return ("update", data)
    return ("ok", None)


def _friendly_network_error(exc: Exception) -> str:
    """Translate a low-level network exception into a user-friendly message."""
    msg = str(exc).lower()
    if "name or service not known" in msg or "getaddrinfo" in msg \
            or "no address associated" in msg or "name resolution" in msg:
        return ("No internet connection. Please check your network and "
                "try again.")
    if "timed out" in msg or "timeout" in msg:
        return ("Connection timed out. Please check your internet "
                "connection.")
    if "unreachable" in msg or "no route" in msg:
        return ("Update server unreachable. Please check your internet "
                "connection.")
    if "refused" in msg:
        return "Update server refused the connection."
    if "ssl" in msg or "certificate" in msg:
        return f"Secure connection failed: {exc}"
    return f"Cannot reach update server: {exc}"


def download(manifest: dict, progress_cb=None) -> Path:
    """Download the new .exe to a temp file, verify SHA256 if provided.

    Returns the verified file path. Raises on hash mismatch.
    """
    url = manifest["url"]
    expected_sha = (manifest.get("sha256") or "").lower().strip()
    temp_dir = Path(tempfile.gettempdir()) / "pymail_update"
    temp_dir.mkdir(parents=True, exist_ok=True)
    target = temp_dir / f"PyMail-{manifest.get('version', 'new')}.exe"

    req = urllib.request.Request(
        url, headers={"User-Agent": f"PyMail/{__version__}"}
    )
    hasher = hashlib.sha256()
    with urllib.request.urlopen(req, timeout=30) as resp, open(target, "wb") as f:
        total = int(resp.headers.get("Content-Length") or 0)
        chunk = 256 * 1024
        downloaded = 0
        while True:
            buf = resp.read(chunk)
            if not buf:
                break
            f.write(buf)
            hasher.update(buf)
            downloaded += len(buf)
            if progress_cb:
                progress_cb(downloaded, total)

    if expected_sha:
        actual_sha = hasher.hexdigest()
        if actual_sha != expected_sha:
            try:
                target.unlink()
            except Exception:
                pass
            raise RuntimeError(
                f"Downloaded file integrity check failed.\n"
                f"Expected SHA256: {expected_sha}\n"
                f"Actual SHA256:   {actual_sha}\n"
                f"The download may have been tampered with. "
                f"Update aborted for your safety."
            )
    return target


def install_and_restart(new_exe: Path) -> None:
    """Spawn a detached helper that swaps the .exe and relaunches.
    Then exits the current process."""
    if not is_frozen():
        raise RuntimeError("Auto-install requires the frozen .exe build.")

    current_exe = get_app_path()
    pid = os.getpid()
    log_file = Path(tempfile.gettempdir()) / "pymail_update.log"

    update_dir = Path(tempfile.gettempdir()) / "pymail_update"
    update_dir.mkdir(parents=True, exist_ok=True)
    bat_path = update_dir / "do_update.bat"
    vbs_path = update_dir / "do_update.vbs"

    # The batch does the actual work. It logs to %TEMP%\pymail_update.log
    bat_content = f"""@echo off
setlocal
set LOG="{log_file}"
echo [%date% %time%] Update starting > %LOG%

rem Wait for old PyMail (PID {pid}) to exit
set /a tries=0
:wait
tasklist /FI "PID eq {pid}" 2>NUL | find " {pid} " >NUL
if errorlevel 1 goto exited
set /a tries+=1
if %tries% GEQ 60 goto force
ping -n 2 127.0.0.1 >NUL
goto wait

:force
echo [%date% %time%] Process did not exit, killing PID {pid} >> %LOG%
taskkill /PID {pid} /F >NUL 2>&1
ping -n 3 127.0.0.1 >NUL

:exited
echo [%date% %time%] Replacing exe >> %LOG%
set /a copy_tries=0
:copy
copy /Y "{new_exe}" "{current_exe}" >> %LOG% 2>&1
if not errorlevel 1 goto refresh
set /a copy_tries+=1
if %copy_tries% GEQ 10 goto fail
ping -n 2 127.0.0.1 >NUL
goto copy

:refresh
rem Force Windows to refresh the icon cache for the new .exe so the
rem Desktop / file explorer icon updates without a logoff.
echo [%date% %time%] Refreshing icon cache >> %LOG%
ie4uinit.exe -show >NUL 2>&1
ie4uinit.exe -ClearIconCache >NUL 2>&1

:restart
echo [%date% %time%] Restarting >> %LOG%
start "" "{current_exe}"
del "{new_exe}" >NUL 2>&1
exit /b 0

:fail
echo [%date% %time%] Update FAILED to copy file >> %LOG%
exit /b 1
"""
    bat_path.write_text(bat_content, encoding="ascii")

    # VBScript wrapper to launch the .bat completely hidden.
    # CreateObject("WScript.Shell").Run with intWindowStyle=0 = hidden.
    # This is the reliable way to hide a console window on Windows 10/11
    # because CREATE_NO_WINDOW alone is unreliable when launched from
    # Windows Terminal / certain PyInstaller bootloaders.
    vbs_content = (
        f'Set sh = CreateObject("WScript.Shell")\r\n'
        f'sh.Run "cmd /c ""{bat_path}""", 0, False\r\n'
    )
    vbs_path.write_text(vbs_content, encoding="ascii")

    # Launch the VBS via wscript (no console). It in turn launches the
    # batch hidden, then exits immediately.
    subprocess.Popen(
        ["wscript.exe", str(vbs_path)],
        creationflags=DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW,
        close_fds=True,
    )

    # Hand off — exit so the .bat can replace our .exe
    sys.exit(0)
