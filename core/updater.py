"""
Self-updater for RunLab Mail.

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
import urllib.request
import zipfile
from datetime import datetime
from pathlib import Path

from .version import __version__, DEFAULT_MANIFEST_URLS

# Persistent state file for "last checked" timestamps
_STATE_DIR = Path(os.path.expanduser("~")) / ".pymail"
_STATE_DIR.mkdir(parents=True, exist_ok=True)
_STATE_FILE = _STATE_DIR / "update_state.json"
_USER_AGENT = f"RunLabMail/{__version__}"


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


def _normalize_manifest_urls(data: dict | None) -> list[str]:
    urls: list[str] = []
    if not isinstance(data, dict):
        return list(DEFAULT_MANIFEST_URLS)

    listed = data.get("manifest_urls")
    if isinstance(listed, list):
        for item in listed:
            if isinstance(item, str):
                u = item.strip()
                if u:
                    urls.append(u)

    single = data.get("manifest_url")
    if isinstance(single, str):
        u = single.strip()
        if u:
            urls.append(u)

    if not urls:
        urls.extend(DEFAULT_MANIFEST_URLS)

    # Deduplicate while preserving order
    return list(dict.fromkeys(urls))


def get_manifest_urls() -> list[str]:
    """Return update manifest URLs in priority order.

    Supports both:
      - manifest_url: "https://.../update_manifest.json"
      - manifest_urls: ["https://primary/...", "https://backup/..."]
    from pymail_update.json next to the executable.
    """
    cfg = get_app_dir() / "pymail_update.json"
    if cfg.is_file():
        try:
            data = json.loads(cfg.read_text(encoding="utf-8"))
            return _normalize_manifest_urls(data)
        except Exception:
            pass
    return list(DEFAULT_MANIFEST_URLS)


def get_manifest_url() -> str:
    """Backward-compatible helper returning the first configured channel."""
    return get_manifest_urls()[0]


def _fetch_manifest(url: str, timeout: int) -> dict:
    req = urllib.request.Request(
        url, headers={"User-Agent": _USER_AGENT},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _get_allowed_version(timeout: int=5) -> str | None:
    """Fetch allowed_version for this machine from the Worker /verify.
    Returns None on failure or if field is absent (fail-open)."""
    try:
        from core import license as licmod
        obj = licmod.load_license()
        if not obj:
            return None
        payload = obj.get("payload") or {}
        license_id = str(payload.get("license_id") or "").strip()
        if not license_id:
            return None
        from core import license_client
        resp = license_client.verify_now(license_id)
        return resp.get("allowed_version") or None
    except Exception:
        return None


def check_for_updates(timeout: int=8) -> dict | None:
    """Return manifest dict if a newer version is available AND allowed, else None.

    Never raises. On any error returns None. Used by silent background checks.
    For the explicit/manual check that distinguishes "no update" from
    "network error", use check_now() instead.
    """
    urls = get_manifest_urls()
    for url in urls:
        try:
            data = _fetch_manifest(url, timeout=timeout)
            _stamp_last_checked()
            latest = str(data.get("version", "0"))
            if _parse_version(latest) <= _parse_version(__version__):
                return None
            # Check if admin has pushed this version to this user
            allowed = _get_allowed_version(timeout=timeout)
            if allowed is not None and _parse_version(allowed) < _parse_version(latest):
                return None
            return data
        except Exception:
            continue
    return None


def check_now(timeout: int=8) -> tuple[str, object]:
    """Explicit update check. Returns one of:

        ("update",  manifest_dict)  — newer version available
        ("ok",      None)           — server reachable, already latest
        ("error",   error_message)  — network/parse failure

    The "Last checked" timestamp is stamped only on "update" or "ok"
    (i.e. when the server actually replied), never on "error".
    """
    urls = get_manifest_urls()
    last_err: Exception | None = None
    invalid_data_seen = False

    for url in urls:
        try:
            data = _fetch_manifest(url, timeout=timeout)
            _stamp_last_checked()
            latest = str(data.get("version", "0"))
            if _parse_version(latest) > _parse_version(__version__):
                allowed = _get_allowed_version(timeout=timeout)
                if allowed is not None and _parse_version(allowed) < _parse_version(latest):
                    return ("ok", None)
                return ("update", data)
            return ("ok", None)
        except json.JSONDecodeError:
            invalid_data_seen = True
            continue
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            last_err = e
            continue
        except Exception as e:
            last_err = e
            continue

    if invalid_data_seen and last_err is None:
        return ("error", "Update server returned invalid data.")
    if isinstance(last_err, (urllib.error.URLError, TimeoutError, OSError)):
        return ("error", _friendly_network_error(last_err))
    if last_err is not None:
        return ("error", str(last_err))
    return ("error", "No update channel configured.")


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
    """Download update package (.exe legacy or .zip onedir), verify SHA256.

    Returns the verified file path. Raises on hash mismatch.
    """
    url = manifest["url"]
    expected_sha = (manifest.get("sha256") or "").lower().strip()
    temp_dir = Path(tempfile.gettempdir()) / "pymail_update"
    temp_dir.mkdir(parents=True, exist_ok=True)
    fname = Path((url or "").split("?", 1)[0]).name or f"PyMail-{manifest.get('version', 'new')}.exe"
    target = temp_dir / fname

    req = urllib.request.Request(
        url, headers={"User-Agent": _USER_AGENT}
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


def _resolve_extracted_source_dir(extract_root: Path, exe_name: str) -> Path:
    # Best case: zip contains a top-level app folder with the executable.
    for p in extract_root.rglob(exe_name):
        if p.is_file():
            return p.parent
    # Fallback: if no exe found (malformed package), use extract root itself.
    return extract_root


def install_and_restart(new_package: Path) -> None:
    """Spawn a detached helper that installs update and relaunches.
    Then exits the current process."""
    if not is_frozen():
        raise RuntimeError("Auto-install requires the frozen .exe build.")

    current_exe = get_app_path()
    app_dir = current_exe.parent
    pid = os.getpid()
    log_file = Path(tempfile.gettempdir()) / "pymail_update.log"

    update_dir = Path(tempfile.gettempdir()) / "pymail_update"
    update_dir.mkdir(parents=True, exist_ok=True)
    bat_path = update_dir / "do_update.bat"
    vbs_path = update_dir / "do_update.vbs"

    package_ext = new_package.suffix.lower()

    if package_ext == ".zip":
        extract_dir = update_dir / f"extract_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        extract_dir.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(new_package, "r") as zf:
            zf.extractall(extract_dir)

        new_source_dir = _resolve_extracted_source_dir(extract_dir, current_exe.name)

        bat_content = f"""@echo off
setlocal
set LOG="{log_file}"
echo [%date% %time%] Onedir update starting > %LOG%

rem Wait for old process (PID {pid}) to exit
set /a tries=0
:wait
tasklist /FI "PID eq {pid}" 2>NUL | find " {pid} " >NUL
if errorlevel 1 goto exited
set /a tries+=1
if %tries% GEQ 90 goto force
ping -n 2 127.0.0.1 >NUL
goto wait

:force
echo [%date% %time%] Process did not exit, killing PID {pid} >> %LOG%
taskkill /PID {pid} /F >NUL 2>&1
ping -n 3 127.0.0.1 >NUL

:exited
echo [%date% %time%] Mirroring new app directory >> %LOG%
robocopy "{new_source_dir}" "{app_dir}" /MIR /R:3 /W:1 /NFL /NDL /NJH /NJS >> %LOG% 2>&1
set RC=%ERRORLEVEL%
if %RC% GEQ 8 goto fail

echo [%date% %time%] Refreshing icon cache >> %LOG%
ie4uinit.exe -show >NUL 2>&1
ie4uinit.exe -ClearIconCache >NUL 2>&1

echo [%date% %time%] Restarting >> %LOG%
start "" "{current_exe}"
del "{new_package}" >NUL 2>&1
rmdir /S /Q "{extract_dir}" >NUL 2>&1
exit /b 0

:fail
echo [%date% %time%] Onedir update FAILED with robocopy code %RC% >> %LOG%
exit /b 1
"""
    else:
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
echo [%date% %time%] Backing up current exe >> %LOG%
copy /Y "{current_exe}" "{app_dir}\\Backup_{current_exe.name}" >> %LOG% 2>&1

rem Create a rollback helper script for the user
echo @echo off > "{app_dir}\\Rollback_to_Previous_Version.bat"
echo echo Memulihkan ke versi sebelumnya... >> "{app_dir}\\Rollback_to_Previous_Version.bat"
echo taskkill /IM {current_exe.name} /F ^>NUL 2^>^&1 >> "{app_dir}\\Rollback_to_Previous_Version.bat"
echo timeout /t 2 /nobreak ^>NUL >> "{app_dir}\\Rollback_to_Previous_Version.bat"
echo copy /Y "%~dp0Backup_{current_exe.name}" "%~dp0{current_exe.name}" >> "{app_dir}\\Rollback_to_Previous_Version.bat"
echo start "" "%~dp0{current_exe.name}" >> "{app_dir}\\Rollback_to_Previous_Version.bat"
echo exit >> "{app_dir}\\Rollback_to_Previous_Version.bat"

echo [%date% %time%] Replacing exe >> %LOG%
set /a copy_tries=0
:copy
copy /Y "{new_package}" "{current_exe}" >> %LOG% 2>&1
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
del "{new_package}" >NUL 2>&1
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
