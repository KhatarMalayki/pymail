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
import os
import sys
import tempfile
import threading
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from . import config

PRIMARY_SECRETS_FILE = config.DEFAULT_BASE / "secrets.dat"
RECOVERY_SECRETS_FILE = Path(
    os.environ.get("LOCALAPPDATA") or config.DEFAULT_BASE
) / "RunLabMail" / "secrets.dat"


def _initial_secrets_file() -> Path:
    """Prefer a previously-created recovery store over the locked legacy one."""
    recovery_dir = RECOVERY_SECRETS_FILE.parent
    try:
        candidates = list(recovery_dir.glob("secrets*.dat"))
        if candidates:
            return max(candidates, key=lambda p: p.stat().st_mtime_ns)
    except OSError:
        pass
    return PRIMARY_SECRETS_FILE


SECRETS_FILE = _initial_secrets_file()
_STORE_LOCK = threading.RLock()


class SecretStorageError(RuntimeError):
    """Raised when credentials cannot be stored durably."""

    def __init__(self, message: str, *, can_reset_store: bool = False):
        super().__init__(message)
        self.can_reset_store = can_reset_store


@dataclass(frozen=True)
class StoreRecoveryResult:
    preserved_path: Path | None
    active_path: Path
    moved_to_backup: bool


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


def _read_store(*, strict: bool = False) -> dict:
    if not SECRETS_FILE.is_file():
        return {}
    try:
        data = json.loads(SECRETS_FILE.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("credential store root is not an object")
        return data
    except Exception as exc:
        if strict:
            raise SecretStorageError(
                "The credential store is unreadable or damaged. It was not "
                "overwritten; restore it or re-enter the account password.",
                can_reset_store=True,
            ) from exc
        return {}


def _write_store(data: dict) -> None:
    """Atomically replace the credential store or raise."""
    SECRETS_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = None
    try:
        payload = json.dumps(data, indent=2)
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=SECRETS_FILE.parent,
            prefix=f".{SECRETS_FILE.name}.",
            suffix=".tmp",
            delete=False,
        ) as tmp:
            tmp_path = tmp.name
            tmp.write(payload)
            tmp.flush()
            os.fsync(tmp.fileno())
        os.replace(tmp_path, SECRETS_FILE)
        tmp_path = None

        # DPAPI already binds ciphertext to this Windows login. Do not remove
        # inherited ACLs here: on domain/renamed accounts an unresolved
        # %USERNAME% grant can make the freshly written file unreadable.
    except Exception as exc:
        raise SecretStorageError(
            f"Could not save encrypted passwords to {SECRETS_FILE}."
        ) from exc
    finally:
        if tmp_path:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass


def _encode_entry(value: str) -> dict:
    raw = (value or "").encode("utf-8")
    encrypted = _dpapi_encrypt(raw)
    if encrypted is not None:
        return {
            "v": 1,
            "enc": "dpapi",
            "data": base64.b64encode(encrypted).decode("ascii"),
        }
    if _is_windows():
        raise SecretStorageError(
            "Windows could not encrypt the password with DPAPI. The password "
            "was not changed."
        )
    return {
        "v": 1,
        "enc": "plain",
        "data": base64.b64encode(raw).decode("ascii"),
    }


def _decode_entry(entry: dict, default: str = "") -> str:
    try:
        blob = base64.b64decode(entry.get("data", "") or "")
    except Exception:
        return default
    if entry.get("enc") == "dpapi":
        decoded = _dpapi_decrypt(blob)
        if decoded is None:
            return default
        return decoded.decode("utf-8", errors="replace")
    return blob.decode("utf-8", errors="replace")


def set_secrets(values: dict[str, str]) -> None:
    """Store several secrets in one durable, verified transaction."""
    if not values:
        return
    with _STORE_LOCK:
        original_store = _read_store(strict=True)
        candidates = {}
        for key, value in values.items():
            entry = _encode_entry(value)
            # Verify DPAPI/plain encoding before replacing the last known-good
            # file. This protects the old credential if Windows returns a
            # malformed ciphertext despite reporting encryption success.
            sentinel = object()
            if _decode_entry(entry, sentinel) != (value or ""):
                raise SecretStorageError(
                    "Windows could not verify the encrypted password. The "
                    "previous password was not changed."
                )
            candidates[key] = entry

        updated_store = dict(original_store)
        updated_store.update(candidates)
        _write_store(updated_store)

        try:
            verified = _read_store(strict=True)
            for key, value in values.items():
                entry = verified.get(key)
                if (not isinstance(entry, dict)
                        or _decode_entry(entry, None) != (value or "")):
                    raise SecretStorageError(
                        "Password verification failed after writing the "
                        "credential store."
                    )
        except SecretStorageError:
            # Best-effort rollback to the in-memory last-known-good store. If
            # the disk remains unwritable, _write_store raises the more useful
            # storage error instead of claiming that the old value survived.
            _write_store(original_store)
            raise


def set_secret(key: str, value: str) -> None:
    """Store a secret. On Windows it's DPAPI-encrypted with the user's
    login credentials. On other platforms it falls back to base64
    (NOT secure — but the app should only be deployed on Windows)."""
    set_secrets({key: value})


def get_secret(key: str, default: str="") -> str:
    """Retrieve a secret. Returns default if missing or decryption fails."""
    with _STORE_LOCK:
        store = _read_store()
        entry = store.get(key)
        if not isinstance(entry, dict):
            return default
        return _decode_entry(entry, default)


def delete_secret(key: str) -> bool:
    """Best-effort removal; never clobber an unreadable credential store."""
    try:
        with _STORE_LOCK:
            store = _read_store(strict=True)
            if key not in store:
                return True
            del store[key]
            _write_store(store)
            return True
    except SecretStorageError:
        return False


def backup_and_reset_store() -> StoreRecoveryResult:
    """Move an unreadable store aside and create a new empty one.

    This is intentionally explicit and is only called after user confirmation.
    The old DPAPI ciphertext remains available for diagnosis/recovery instead
    of being deleted or overwritten.
    """
    global SECRETS_FILE
    with _STORE_LOCK:
        original_path = SECRETS_FILE
        backup_path = None
        moved_to_backup = False
        if SECRETS_FILE.exists():
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
            backup_path = SECRETS_FILE.with_name(
                f"{SECRETS_FILE.name}.unreadable-{stamp}.bak"
            )
            try:
                os.replace(SECRETS_FILE, backup_path)
                moved_to_backup = True
            except OSError:
                # The old releases could leave an ACL that denies even rename.
                # Preserve that locked file in place and switch to a fresh,
                # user-writable LocalAppData credential store instead.
                backup_path = original_path
                candidate = RECOVERY_SECRETS_FILE
                if candidate.exists() or candidate == original_path:
                    candidate = candidate.with_name(
                        f"secrets-recovered-{stamp}.dat"
                    )
                SECRETS_FILE = candidate
        try:
            _write_store({})
        except Exception:
            failed_active_path = SECRETS_FILE
            SECRETS_FILE = original_path
            if (moved_to_backup and backup_path and backup_path.exists()
                    and not original_path.exists()):
                try:
                    os.replace(backup_path, original_path)
                except OSError:
                    pass
            raise SecretStorageError(
                f"Could not create a new credential store at "
                f"{failed_active_path}."
            )
        return StoreRecoveryResult(
            preserved_path=backup_path,
            active_path=SECRETS_FILE,
            moved_to_backup=moved_to_backup,
        )


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
