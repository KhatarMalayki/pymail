"""
Bulk-issue licenses from a CSV file.

Usage:
    python admin/bulk_issue.py users.csv --days 90
    python admin/bulk_issue.py users.csv               (perpetual)
    python admin/bulk_issue.py users.csv --days 30 --output keys.csv

CSV format:
    name,email
    Pak Budi Santoso,budi@intra.tunasgroup.com
    Mbak Sari Dewi,sari@intra.tunasgroup.com
    ...

Output (when --output is given):
    name,email,license_id,license_key,expires_at
    Pak Budi,budi@x.com,abc123,eyJwYXl...,2026-08-21T00:00:00+00:00

The output CSV makes it easy to mail-merge: each row → 1 email to that
recipient with their license key.
"""
import argparse
import base64
import csv
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


def _log_issued(payloads: list[dict]):
    log = []
    if LOG_FILE.is_file():
        try:
            log = json.loads(LOG_FILE.read_text(encoding="utf-8"))
        except Exception:
            log = []
    log.extend(payloads)
    LOG_FILE.write_text(json.dumps(log, indent=2), encoding="utf-8")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("users_csv", help="CSV with columns: name, email")
    p.add_argument("--days", type=int, default=0,
                   help="Trial duration in days (0 = perpetual)")
    p.add_argument("--output", default="",
                   help="Write generated keys to this CSV "
                        "(default: stdout only)")
    p.add_argument("--note", default="bulk issue", help="Free-form note")
    args = p.parse_args()

    csv_path = Path(args.users_csv)
    if not csv_path.is_file():
        print(f"ERROR: {csv_path} not found")
        sys.exit(1)

    with open(csv_path, "r", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))

    if not rows:
        print("ERROR: no rows in CSV")
        sys.exit(1)

    # Validate headers
    headers = {h.lower() for h in (rows[0].keys() if rows else [])}
    if "email" not in headers:
        print("ERROR: CSV needs at least an 'email' column")
        sys.exit(1)

    expires_iso = ""
    if args.days > 0:
        expires_iso = (
            datetime.now(timezone.utc) + timedelta(days=args.days)
        ).isoformat()

    priv = _load_private()
    issued = []
    output_rows = []

    for row in rows:
        name = (row.get("name") or row.get("Name") or "").strip()
        email = (row.get("email") or row.get("Email") or "").strip()
        if not email or "@" not in email:
            print(f"  skip: invalid email -> {row}")
            continue

        license_id = secrets.token_urlsafe(8)
        payload = {
            "license_id": license_id,
            "name": name,
            "email": email,
            "issued_at": datetime.now(timezone.utc).isoformat(),
            "expires_at": expires_iso,
            "machine_id_hash": "",
            "note": args.note,
        }
        sig = priv.sign(_canonical(payload))
        license_obj = {
            "payload": payload,
            "signature": base64.b64encode(sig).decode("ascii"),
        }
        license_str = base64.b64encode(
            json.dumps(license_obj, separators=(",", ":")).encode("utf-8")
        ).decode("ascii")

        issued.append(payload)
        output_rows.append({
            "name": name,
            "email": email,
            "license_id": license_id,
            "license_key": license_str,
            "expires_at": expires_iso or "(never)",
        })
        print(f"  {license_id}  {name}  {email}")

    _log_issued(issued)

    if args.output:
        with open(args.output, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(
                f, fieldnames=["name", "email", "license_id",
                               "license_key", "expires_at"],
            )
            writer.writeheader()
            writer.writerows(output_rows)
        print()
        print(f"Wrote {len(output_rows)} licenses to {args.output}")
        print("You can mail-merge this CSV to send each user their key.")
    else:
        print()
        print(f"Issued {len(issued)} license(s).")
        print("Use --output keys.csv to dump the license keys for "
              "mail-merge distribution.")


if __name__ == "__main__":
    main()
