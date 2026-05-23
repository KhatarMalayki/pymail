"""
Revoke / restore licenses. Updates revoked.json in your R2 bucket.

Usage:
    python admin/revoke_license.py list
    python admin/revoke_license.py revoke <license_id> ["reason"]
    python admin/revoke_license.py restore <license_id>

Reads/writes revoked.json in the R2 bucket using your existing `wrangler`
CLI auth — no extra credentials.

The blacklist URL the app polls (configured in core/license.py):
    https://<r2-public-url>/revoked.json
"""
import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).parent.parent

# Load .env
env_file = ROOT / ".env"
if env_file.is_file():
    for line in env_file.read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.strip().startswith("#"):
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

R2_BUCKET = os.environ.get("R2_BUCKET", "pymail-releases")
LOCAL_FILE = ROOT / "admin" / "revoked.json"
ISSUED_LOG = ROOT / "admin" / "licenses_issued.json"

# Make sure wrangler is reachable on Windows
_npm_bin = os.path.join(os.environ.get("APPDATA", ""), "npm")
if _npm_bin and _npm_bin not in os.environ.get("PATH", ""):
    os.environ["PATH"] = _npm_bin + os.pathsep + os.environ.get("PATH", "")


def _wrangler_cmd():
    """Return the wrangler command path. On Windows, .cmd shims need to be
    invoked explicitly because subprocess can't find shell shims via PATH."""
    if sys.platform == "win32":
        candidate = Path(os.environ.get("APPDATA", "")) / "npm" / "wrangler.cmd"
        if candidate.is_file():
            return str(candidate)
    return "wrangler"


def _read_local() -> dict:
    """Load the most recent state from local cache, or default."""
    if LOCAL_FILE.is_file():
        try:
            return json.loads(LOCAL_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"revoked": [], "updated_at": "", "notes": {}}


def _pull_from_r2() -> dict:
    """Fetch current revoked.json from R2 (most authoritative)."""
    try:
        result = subprocess.run(
            [_wrangler_cmd(), "r2", "object", "get",
             f"{R2_BUCKET}/revoked.json", "--remote", "--pipe"],
            capture_output=True, text=True, check=True,
        )
        return json.loads(result.stdout)
    except subprocess.CalledProcessError:
        # Object may not exist yet, that's fine
        return {"revoked": [], "updated_at": "", "notes": {}}
    except Exception:
        return _read_local()


def _push_to_r2(data: dict):
    """Write data to local file, then upload to R2."""
    LOCAL_FILE.parent.mkdir(parents=True, exist_ok=True)
    LOCAL_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")
    subprocess.run(
        [_wrangler_cmd(), "r2", "object", "put",
         f"{R2_BUCKET}/revoked.json",
         "--file", str(LOCAL_FILE),
         "--content-type", "application/json",
         "--remote"],
        check=True,
    )


def list_issued():
    if not ISSUED_LOG.is_file():
        print("(no licenses issued yet)")
        return
    data = json.loads(ISSUED_LOG.read_text(encoding="utf-8"))
    revoked = set(_pull_from_r2().get("revoked", []))
    print(f"{'license_id':<14} {'status':<10} {'name':<25} {'email':<35} {'expires':<25}")
    print("-" * 110)
    for p in data:
        status = "REVOKED" if p["license_id"] in revoked else "active"
        print(
            f"{p['license_id']:<14} "
            f"{status:<10} "
            f"{(p.get('name') or '-')[:24]:<25} "
            f"{(p.get('email') or '-')[:34]:<35} "
            f"{(p.get('expires_at') or '(never)')[:24]:<25}"
        )


def revoke(license_id: str, reason: str = ""):
    data = _pull_from_r2()
    revoked = data.get("revoked", [])
    if license_id in revoked:
        print(f"License {license_id} already revoked.")
        return
    revoked.append(license_id)
    data["revoked"] = revoked
    data["updated_at"] = datetime.now(timezone.utc).isoformat()
    notes = data.setdefault("notes", {})
    notes[license_id] = reason or "(no reason)"
    _push_to_r2(data)
    print(f"OK. Revoked {license_id}.")
    print("Effect window:")
    print("  - Users with cached blacklist (max 5 min old): up to 5 minutes")
    print("  - Users on next app launch: immediate")
    print("  - Users who click 'Refresh license status' in About dialog: immediate")


def restore(license_id: str):
    data = _pull_from_r2()
    revoked = data.get("revoked", [])
    if license_id not in revoked:
        print(f"License {license_id} is not currently revoked.")
        return
    revoked.remove(license_id)
    data["revoked"] = revoked
    data["updated_at"] = datetime.now(timezone.utc).isoformat()
    notes = data.get("notes") or {}
    notes.pop(license_id, None)
    data["notes"] = notes
    _push_to_r2(data)
    print(f"OK. Restored {license_id}.")


def main():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list")
    p_rev = sub.add_parser("revoke")
    p_rev.add_argument("license_id")
    p_rev.add_argument("reason", nargs="?", default="")
    p_res = sub.add_parser("restore")
    p_res.add_argument("license_id")
    args = parser.parse_args()

    if args.cmd == "list":
        list_issued()
    elif args.cmd == "revoke":
        revoke(args.license_id, args.reason)
    elif args.cmd == "restore":
        restore(args.license_id)


if __name__ == "__main__":
    main()
