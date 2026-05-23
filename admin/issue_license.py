"""
Issue a license key for a user.

Usage:
    python admin/issue_license.py --name "Khatar" --email khatar@intra.tunasgroup.com
    python admin/issue_license.py --name "Bob" --email bob@x.com --expires 2027-12-31
    python admin/issue_license.py --name "Carol" --email carol@x.com --machine 5fa9...

The output is a base64 license string. Send it to your user; they paste it
in PyMail's license dialog at first launch.

Each issued license is logged to admin/licenses_issued.json so you have
a record of license_id -> who has it. Use this when you want to revoke.
"""
import argparse
import base64
import json
import secrets
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives import serialization

ROOT = Path(__file__).parent.parent
PRIVATE_FILE = ROOT / ".keys" / "license_private.key"
LOG_FILE = ROOT / "admin" / "licenses_issued.json"


def _load_private() -> Ed25519PrivateKey:
    if not PRIVATE_FILE.is_file():
        print(f"ERROR: private key not found at {PRIVATE_FILE}")
        print("Run: python admin/generate_keys.py")
        sys.exit(1)
    return serialization.load_pem_private_key(
        PRIVATE_FILE.read_bytes(), password=None
    )


def _canonical(payload: dict) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _log_issued(payload: dict):
    log = []
    if LOG_FILE.is_file():
        try:
            log = json.loads(LOG_FILE.read_text(encoding="utf-8"))
        except Exception:
            log = []
    log.append(payload)
    LOG_FILE.write_text(json.dumps(log, indent=2), encoding="utf-8")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--name", required=True, help="Recipient display name")
    p.add_argument("--email", required=True, help="Recipient email (informational)")
    p.add_argument("--expires", default="",
                   help="ISO date YYYY-MM-DD or empty for no expiry")
    p.add_argument("--days", type=int, default=0,
                   help="Alternative to --expires: validity in days from now")
    p.add_argument("--machine", default="",
                   help="Bind to specific machine_id_hash (empty = floating)")
    p.add_argument("--note", default="", help="Free-form note")
    args = p.parse_args()

    expires_iso = ""
    if args.expires:
        expires_iso = (
            datetime.fromisoformat(args.expires)
            .replace(tzinfo=timezone.utc).isoformat()
        )
    elif args.days > 0:
        expires_iso = (
            datetime.now(timezone.utc) + timedelta(days=args.days)
        ).isoformat()

    license_id = secrets.token_urlsafe(8)
    payload = {
        "license_id": license_id,
        "name": args.name,
        "email": args.email,
        "issued_at": datetime.now(timezone.utc).isoformat(),
        "expires_at": expires_iso,
        "machine_id_hash": args.machine.strip(),
        "note": args.note,
    }

    priv = _load_private()
    sig = priv.sign(_canonical(payload))
    license_obj = {
        "payload": payload,
        "signature": base64.b64encode(sig).decode("ascii"),
    }
    license_str = base64.b64encode(
        json.dumps(license_obj, separators=(",", ":")).encode("utf-8")
    ).decode("ascii")

    _log_issued(payload)

    print()
    print("=" * 60)
    print(f"License issued for {args.name} <{args.email}>")
    print(f"  license_id:  {license_id}")
    print(f"  expires_at:  {expires_iso or '(never)'}")
    print(f"  bound to:    {args.machine or '(any machine)'}")
    print(f"  logged at:   {LOG_FILE}")
    print("=" * 60)
    print()
    print("Send this license key to the user (paste at first launch):")
    print()
    # Wrap at 64 chars so it pastes cleanly into emails
    for i in range(0, len(license_str), 64):
        print(license_str[i:i + 64])
    print()


if __name__ == "__main__":
    main()
