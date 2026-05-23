"""
License verification (client-side).

How it works:
    1. Admin issues a license: a JSON payload (license_id, name, email,
       issued_at, expires_at, machine_id_hash) signed with the private key.
    2. The signed license is given to the user as a base64 string.
    3. User pastes the license string at first launch; we verify it with
       the embedded public key.
    4. We also fetch a remote blacklist (in the same release repo) to
       check if this license_id has been revoked. The blacklist is
       cached for BLACKLIST_CACHE_TTL seconds to minimize GitHub hits
       (important for installations with many users behind a NAT IP).
    5. License + machine_id binding prevents simple copy-paste of license
       between machines.

Storage:
    - License payload + signature stored at %USERPROFILE%/.pymail/license.json
    - Cached blacklist at %USERPROFILE%/.pymail/blacklist_cache.json

The public key is embedded in the binary (safe — verify-only).
The private key is on the developer's machine ONLY.
"""
import base64
import hashlib
import json
import os
import platform
import socket
import subprocess
import sys
import time
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from cryptography.exceptions import InvalidSignature

from .license_pubkey import LICENSE_PUBLIC_KEY_B64
from .version import __version__


APP_DIR = Path(os.path.expanduser("~")) / ".pymail"
APP_DIR.mkdir(parents=True, exist_ok=True)
LICENSE_FILE = APP_DIR / "license.json"
BLACKLIST_CACHE_FILE = APP_DIR / "blacklist_cache.json"

# Name shown in user-facing license error messages
ADMIN_NAME = "Khatar"

# Cache the blacklist for 30 seconds. The Worker has no CDN cache so
# changes propagate as fast as the cache window allows. With 300 users
# at this TTL we hit ~36K reads/hr which is well within Cloudflare's
# free tier (10M reads/month for R2).
BLACKLIST_CACHE_TTL = 30  # seconds

# Remote blacklist URL (Cloudflare R2 public bucket).
DEFAULT_BLACKLIST_URL = (
    "https://pub-fbab08d87bad4965bfcade6e58bd1fa6.r2.dev/revoked.json"
)


# ---------- Machine ID ----------

def get_machine_id() -> str:
    """Stable, hashed machine identifier.

    Uses Windows MachineGuid from the registry on Windows, falls back to
    hostname + MAC. Hashed so it's not personally identifiable.
    """
    raw = ""
    try:
        if sys.platform == "win32":
            out = subprocess.check_output(
                ["reg", "query",
                 r"HKLM\SOFTWARE\Microsoft\Cryptography",
                 "/v", "MachineGuid"],
                stderr=subprocess.DEVNULL, timeout=5,
            ).decode(errors="replace")
            for line in out.splitlines():
                if "MachineGuid" in line:
                    raw = line.strip().split()[-1]
                    break
        else:
            mid = Path("/etc/machine-id")
            if mid.is_file():
                raw = mid.read_text().strip()
    except Exception:
        pass

    if not raw:
        raw = f"{socket.gethostname()}-{uuid.getnode()}-{platform.system()}"

    return hashlib.sha256(raw.encode()).hexdigest()[:32]


# ---------- License signature verification ----------

class LicenseError(Exception):
    pass


def _public_key() -> Ed25519PublicKey:
    return Ed25519PublicKey.from_public_bytes(
        base64.b64decode(LICENSE_PUBLIC_KEY_B64)
    )


def _canonical(payload: dict) -> bytes:
    """Deterministic encoding so signature is stable."""
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


def parse_license_string(s: str) -> dict:
    """Decode a license string into {payload, signature_b64}."""
    s = (s or "").strip().replace("\r", "").replace("\n", "")
    try:
        decoded = base64.b64decode(s.encode("ascii"))
        obj = json.loads(decoded.decode("utf-8"))
        if "payload" not in obj or "signature" not in obj:
            raise ValueError("missing fields")
        return obj
    except Exception as e:
        raise LicenseError(f"License key tidak valid: {e}") from e


def verify_signature(license_obj: dict) -> dict:
    """Verify Ed25519 signature. Returns the payload if valid."""
    payload = license_obj["payload"]
    sig = base64.b64decode(license_obj["signature"])
    try:
        _public_key().verify(sig, _canonical(payload))
    except InvalidSignature:
        raise LicenseError("Tanda tangan license tidak valid (dipalsukan?).")
    return payload


# ---------- Local store ----------

def save_license(license_obj: dict):
    LICENSE_FILE.write_text(
        json.dumps(license_obj, indent=2), encoding="utf-8"
    )


def load_license() -> dict | None:
    if not LICENSE_FILE.is_file():
        return None
    try:
        return json.loads(LICENSE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return None


def clear_license():
    try:
        LICENSE_FILE.unlink()
    except Exception:
        pass


# ---------- Blacklist (remote) ----------

def fetch_blacklist(
    url: str = DEFAULT_BLACKLIST_URL,
    timeout: int = 5,
    *,
    use_cache: bool = True,
) -> set:
    """Fetch the revoked-license-ids set, with on-disk caching.

    Returns empty set on failure (fail-open is intentional — offline users
    can still work).
    """
    # Try cache first
    if use_cache:
        cached = _read_blacklist_cache()
        if cached is not None:
            return cached

    try:
        req = urllib.request.Request(
            url, headers={"User-Agent": f"PyMail/{__version__}"}
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        revoked = set(str(x) for x in (data.get("revoked") or []))
        _write_blacklist_cache(revoked)
        return revoked
    except Exception:
        # Network failed — try stale cache as last resort, even if expired
        stale = _read_blacklist_cache(ignore_ttl=True)
        return stale if stale is not None else set()


def _read_blacklist_cache(ignore_ttl: bool = False) -> set | None:
    """Read cached blacklist if still fresh. Returns None if missing/expired."""
    if not BLACKLIST_CACHE_FILE.is_file():
        return None
    try:
        data = json.loads(BLACKLIST_CACHE_FILE.read_text(encoding="utf-8"))
        cached_at = float(data.get("cached_at") or 0)
        age = time.time() - cached_at
        if not ignore_ttl and age > BLACKLIST_CACHE_TTL:
            return None
        return set(str(x) for x in (data.get("revoked") or []))
    except Exception:
        return None


def _write_blacklist_cache(revoked: set):
    try:
        BLACKLIST_CACHE_FILE.write_text(
            json.dumps({
                "cached_at": time.time(),
                "revoked": sorted(revoked),
            }),
            encoding="utf-8",
        )
    except Exception:
        pass


def force_refresh_blacklist():
    """Bypass cache and fetch fresh blacklist. Useful from a manual UI button."""
    return fetch_blacklist(use_cache=False)


# ---------- Top-level checks ----------

def validate_license(
    license_obj: dict,
    *,
    check_blacklist: bool = True,
    blacklist_url: str = DEFAULT_BLACKLIST_URL,
) -> dict:
    """Full validation pipeline. Returns payload on success, raises on failure."""
    payload = verify_signature(license_obj)

    # Machine binding (optional — issuer may issue floating license by
    # leaving machine_id_hash empty)
    bound_id = (payload.get("machine_id_hash") or "").strip()
    if bound_id and bound_id != get_machine_id():
        raise LicenseError(
            "License terikat ke perangkat lain. Hubungi admin untuk re-issue."
        )

    # Expiry
    expires = payload.get("expires_at")
    if expires:
        try:
            exp_dt = datetime.fromisoformat(expires.replace("Z", "+00:00"))
            now = datetime.now(timezone.utc)
            if now > exp_dt:
                raise LicenseError(f"License sudah expired pada {expires}.")
        except LicenseError:
            raise
        except Exception:
            raise LicenseError(f"License expires_at tidak valid: {expires}")

    # Blacklist
    if check_blacklist:
        revoked = fetch_blacklist(blacklist_url)
        lid = str(payload.get("license_id") or "")
        if lid and lid in revoked:
            raise LicenseError(
                "License Anda telah dicabut oleh admin.\n"
                f"Hubungi admin {ADMIN_NAME} jika menurut Anda ini keliru."
            )

    return payload


def is_licensed() -> tuple[bool, dict | None, str]:
    """Returns (ok, payload, error_message). Used at app startup."""
    obj = load_license()
    if not obj:
        return False, None, "No license installed."
    try:
        payload = validate_license(obj)
        return True, payload, ""
    except LicenseError as e:
        return False, None, str(e)


def days_until_expiry(payload: dict | None) -> int | None:
    """Return number of whole days until license expiry. None if perpetual.
    Negative values mean already expired."""
    if not payload:
        return None
    expires = payload.get("expires_at")
    if not expires:
        return None
    try:
        exp_dt = datetime.fromisoformat(expires.replace("Z", "+00:00"))
    except Exception:
        return None
    now = datetime.now(timezone.utc)
    delta = exp_dt - now
    return delta.days


def is_trial(payload: dict | None) -> bool:
    """A license is considered a trial when it has an expiry date."""
    return bool(payload and payload.get("expires_at"))
