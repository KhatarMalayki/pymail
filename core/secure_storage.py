"""
Windows DPAPI-backed secure storage for short secrets.

Uses CryptProtectData / CryptUnprotectData via ctypes — same API that
Chrome / Edge / git-credential-manager use to encrypt secrets at rest.

Key properties:
- Encrypted with the user's Windows login credentials. The ciphertext is
  meaningless if copied to another machine or accessed by another user
  on the same machine.
- No external dependency (ships with Windows).
- Falls back to plain-text storage on non-Windows so the rest of the app
  still works in dev mode on Linux/macOS.

Storage location: alongside other config in ~/.runlabmail/secrets.dat
"""
import base64
import json
import sys

from . import config

SECRETS_FILE = config.DEFAULT_BASE / "secrets.dat"


def _is_windows() -> bool:
    return sys.platform == "win32"


def _dpapi_encrypt(plaintext: bytes) -> bytes | None:
    """Encrypt bytes using Windows DPAPI. Returns None on non-Windows
    or any failure."""
    if not _is_windows():
        return None
    try:
        import ctypes
        from ctypes import wintypes

        class DATA_BLOB(ctypes.Structure):
            _fields_ = [
                ("cbData", wintypes.DWORD),
                ("pbData", ctypes.POINTER(ctypes.c_char)),
            ]

        crypt32 = ctypes.windll.crypt32
        # IMPORTANT: keep a Python reference to the input buffer for the
        # whole duration of the Win32 call. If we inline
        # create_string_buffer(...) directly inside cast(...), nothing holds
        # the buffer alive and the garbage collector may free it before (or
        # during) CryptProtectData runs. That makes the call read freed /
        # reused memory and intermittently fail — which is exactly why some
        # accounts lost their saved POP3 password right after saving.
        in_buf = ctypes.create_string_buffer(plaintext, len(plaintext))
        in_blob = DATA_BLOB(len(plaintext),
                            ctypes.cast(in_buf, ctypes.POINTER(ctypes.c_char)))
        out_blob = DATA_BLOB()
        if not crypt32.CryptProtectData(
            ctypes.byref(in_blob), None, None, None, None, 0,
            ctypes.byref(out_blob),
        ):
            return None
        try:
            data = ctypes.string_at(out_blob.pbData, out_blob.cbData)
        finally:
            ctypes.windll.kernel32.LocalFree(out_blob.pbData)
        return data
    except Exception:
        return None


def _dpapi_decrypt(ciphertext: bytes) -> bytes | None:
    """Decrypt bytes encrypted with _dpapi_encrypt. Returns None on
    failure (different user, different machine, corrupted data)."""
    if not _is_windows():
        return None
    try:
        import ctypes
        from ctypes import wintypes

        class DATA_BLOB(ctypes.Structure):
            _fields_ = [
                ("cbData", wintypes.DWORD),
                ("pbData", ctypes.POINTER(ctypes.c_char)),
            ]

        crypt32 = ctypes.windll.crypt32
        # Keep a live reference to the input buffer (see _dpapi_encrypt for
        # why). create_string_buffer with an explicit length copies the full
        # ciphertext verbatim, including any embedded NUL bytes.
        in_buf = ctypes.create_string_buffer(ciphertext, len(ciphertext))
        in_blob = DATA_BLOB(len(ciphertext),
                            ctypes.cast(in_buf, ctypes.POINTER(ctypes.c_char)))
        out_blob = DATA_BLOB()
        if not crypt32.CryptUnprotectData(
            ctypes.byref(in_blob), None, None, None, None, 0,
            ctypes.byref(out_blob),
        ):
            return None
        try:
            data = ctypes.string_at(out_blob.pbData, out_blob.cbData)
        finally:
            ctypes.windll.kernel32.LocalFree(out_blob.pbData)
        return data
    except Exception:
        return None

# ---------- Public API ----------


def _read_store() -> dict:
    if not SECRETS_FILE.is_file():
        return {}
    try:
        return json.loads(SECRETS_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _write_store(data: dict) -> None:
    try:
        SECRETS_FILE.write_text(
            json.dumps(data, indent=2), encoding="utf-8"
        )
        # Permissions: best-effort tighten on Windows so other users can't
        # read the file. (DPAPI alone is enough security but defence in depth.)
        if _is_windows():
            import os
            try:
                os.system(f'icacls "{SECRETS_FILE}" /inheritance:r '
                          f'/grant:r "%USERNAME%":F >nul 2>&1')
            except Exception:
                pass
    except Exception:
        pass


def set_secret(key: str, value: str) -> None:
    """Store a secret. On Windows it's DPAPI-encrypted with the user's
    login credentials. On other platforms it falls back to base64
    (NOT secure — but the app should only be deployed on Windows)."""
    raw = (value or "").encode("utf-8")
    encrypted = _dpapi_encrypt(raw)
    store = _read_store()
    if encrypted is not None:
        store[key] = {
            "v": 1,
            "enc": "dpapi",
            "data": base64.b64encode(encrypted).decode("ascii"),
        }
    else:
        # Fallback (dev mode / non-Windows)
        store[key] = {
            "v": 1,
            "enc": "plain",
            "data": base64.b64encode(raw).decode("ascii"),
        }
    _write_store(store)


def get_secret(key: str, default: str="") -> str:
    """Retrieve a secret. Returns default if missing or decryption fails."""
    store = _read_store()
    entry = store.get(key)
    if not entry:
        return default
    blob = base64.b64decode(entry.get("data", "") or "")
    if entry.get("enc") == "dpapi":
        decoded = _dpapi_decrypt(blob)
        if decoded is None:
            return default
        return decoded.decode("utf-8", errors="replace")
    return blob.decode("utf-8", errors="replace")


def delete_secret(key: str) -> None:
    store = _read_store()
    if key in store:
        del store[key]
        _write_store(store)


def migrate_from_config(key: str) -> bool:
    """Migrate a plain-text secret stored in config.json to the secure
    secrets store. Returns True if migrated."""
    plain = config.get(key, "")
    if not plain:
        return False
    set_secret(key, plain)
    # Wipe from plain-text config
    cfg = config._load()
    if key in cfg:
        del cfg[key]
        config._save(cfg)
    return True
