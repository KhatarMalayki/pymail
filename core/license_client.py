"""
License client — talks to the Cloudflare Worker for auto-registration
and admin operations.

Public flow:
    1. RunLab Mail starts → if local license missing/expired, do nothing yet
       (user can still set up an account)
    2. After the first POP3 fetch succeeds, call register_now() which
       hits POST /register with machine_id + email. The Worker returns
       a signed license key, we save it to ~/.pymail/license.json.
    3. App reloads license, app is now in "trial" mode (30 days by default).

Admin flow (only the developer machine has the admin token):
    list_users(), extend_license(), revoke_license(), restore_license()

The Worker URL is read from `LICENSE_API_URL` in core/version.py, with
optional override via pymail_update.json (key 'license_api_url').
"""
import json
import os
import platform
import socket
import urllib.error
import urllib.request

from . import license as licmod
from .version import __version__, LICENSE_API_URL


# ---------- URL config ----------

def get_api_url() -> str:
    """Returns the Worker base URL. Trims trailing slash for clean joins."""
    # Allow runtime override via pymail_update.json next to the .exe
    try:
        import sys
        from pathlib import Path
        if getattr(sys, "frozen", False):
            cfg = Path(sys.executable).parent / "pymail_update.json"
        else:
            cfg = Path(__file__).parent.parent / "pymail_update.json"
        if cfg.is_file():
            data = json.loads(cfg.read_text(encoding="utf-8"))
            url = data.get("license_api_url")
            if url:
                return url.rstrip("/")
    except Exception:
        pass
    return (LICENSE_API_URL or "").rstrip("/")


# ---------- HTTP helpers ----------

class WorkerError(Exception):
    pass


def _post(path: str, body: dict, timeout: int = 10,
          headers: dict | None = None) -> dict:
    base = get_api_url()
    if not base:
        raise WorkerError("License API URL is not configured.")
    url = f"{base}{path}"
    payload = json.dumps(body).encode("utf-8")
    req_headers = {
        "Content-Type": "application/json",
        "User-Agent": f"PyMail/{__version__}",
    }
    if headers:
        req_headers.update(headers)
    req = urllib.request.Request(url, data=payload, headers=req_headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            err_body = json.loads(e.read().decode("utf-8"))
            msg = err_body.get("error") or e.reason
        except Exception:
            msg = e.reason
        raise WorkerError(f"HTTP {e.code}: {msg}")
    except (urllib.error.URLError, socket.error, TimeoutError) as e:
        raise WorkerError(f"Network error: {e}")
    except json.JSONDecodeError:
        raise WorkerError("Server returned non-JSON response")


# ---------- Public API (used by the running RunLab Mail) ----------

def register_now(email: str, name: str = "") -> dict:
    """Register this machine + email with the license Worker.

    Returns the parsed response (contains license_key + expires_at).
    The signed license key is also saved to ~/.pymail/license.json so
    RunLab Mail picks it up automatically.
    """
    body = {
        "machine_id": licmod.get_machine_id(),
        "email": (email or "").strip().lower(),
        "name": name.strip(),
        "hostname": socket.gethostname(),
        "os_user": _safe_user(),
        "version": __version__,
        "is_new_user": not bool(licmod.load_license()),
    }
    resp = _post("/register", body)
    license_key = resp.get("license_key")
    if license_key:
        # Parse + verify locally before saving (defence in depth)
        try:
            obj = licmod.parse_license_string(license_key)
            licmod.verify_signature(obj)
            licmod.save_license(obj)
            # Clear the blacklist cache so the new license-id isn't
            # accidentally checked against a stale list
            try:
                if licmod.BLACKLIST_CACHE_FILE.is_file():
                    licmod.BLACKLIST_CACHE_FILE.unlink()
            except Exception:
                pass
        except Exception as e:
            raise WorkerError(f"Server returned invalid license: {e}")
    return resp


def verify_now(license_id: str) -> dict:
    """Ping the Worker for fresh status of a license_id (active / revoked /
    expired). Used periodically to detect admin actions."""
    return _post("/verify", {"license_id": license_id})


def _safe_user() -> str:
    try:
        return os.getlogin()
    except Exception:
        return os.environ.get("USERNAME") or os.environ.get("USER") or ""


# ---------- Admin API (only the developer's machine has the token) ----------

def admin_headers(token: str) -> dict:
    return {"X-Admin-Token": token}


def admin_list_users(token: str) -> list[dict]:
    resp = _post("/admin/list", {}, headers=admin_headers(token), timeout=15)
    return resp.get("users") or []


def admin_extend(token: str, license_id: str, days: int) -> dict:
    """Extend (or shorten) trial. days=0 means perpetual."""
    return _post(
        "/admin/extend",
        {"license_id": license_id, "days": days},
        headers=admin_headers(token),
    )


def admin_revoke(token: str, license_id: str, reason: str = "") -> dict:
    return _post(
        "/admin/revoke",
        {"license_id": license_id, "reason": reason},
        headers=admin_headers(token),
    )


def admin_restore(token: str, license_id: str) -> dict:
    return _post(
        "/admin/restore",
        {"license_id": license_id},
        headers=admin_headers(token),
    )


def admin_import(token: str, license_obj: dict, *,
                 machine_id: str = "",
                 hostname: str = "",
                 os_user: str = "",
                 version: str = "") -> dict:
    """Push an offline-issued license into the Worker registry so it
    shows up in License Manager. license_obj is the parsed
    {payload, signature} dict from licmod.parse_license_string()."""
    payload = license_obj.get("payload") if isinstance(license_obj, dict) else {}
    body = {
        "payload": payload,
        "machine_id": machine_id,
        "hostname": hostname,
        "os_user": os_user,
        "version": version,
    }
    return _post("/admin/import", body, headers=admin_headers(token))


def admin_delete(token: str, license_id: str) -> dict:
    """Permanently remove a user from the registry. Different from revoke
    (which keeps the row). For cleanup of test accounts."""
    return _post(
        "/admin/delete",
        {"license_id": license_id},
        headers=admin_headers(token),
    )
