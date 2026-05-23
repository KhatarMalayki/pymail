"""
Manage R2 retention for PyMail binaries.

Cloudflare R2 has a built-in lifecycle rule (set up once during R2 migration)
that auto-deletes PyMail-*.exe files older than 90 days. So normally you
don't need to run this script.

This script is for occasional manual operations:

    python admin/cleanup_releases.py status   # Show lifecycle rule
    python admin/cleanup_releases.py delete <version>   # Delete a specific old release
                                                        # e.g. delete 1.0.0

The lifecycle rule never touches:
    - update_manifest.json (the live pointer)
    - revoked.json (license blacklist)

Configure the rule (run once if migrating to a new bucket):
    wrangler r2 bucket lifecycle add <bucket> auto-cleanup-old-binaries PyMail- --expire-days 90 --force
"""
import argparse
import os
import subprocess
import sys
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

# Make sure wrangler is reachable on Windows
_npm_bin = os.path.join(os.environ.get("APPDATA", ""), "npm")
if _npm_bin and _npm_bin not in os.environ.get("PATH", ""):
    os.environ["PATH"] = _npm_bin + os.pathsep + os.environ.get("PATH", "")


def _wrangler_cmd():
    if sys.platform == "win32":
        candidate = Path(os.environ.get("APPDATA", "")) / "npm" / "wrangler.cmd"
        if candidate.is_file():
            return str(candidate)
    return "wrangler"


def status():
    """Show lifecycle rules currently configured on the bucket."""
    subprocess.run(
        [_wrangler_cmd(), "r2", "bucket", "lifecycle", "list", R2_BUCKET]
    )


def delete_version(version: str):
    """Manually delete a specific PyMail-X.Y.Z.exe from R2."""
    version = version.lstrip("v")
    key = f"PyMail-{version}.exe"
    print(f"Deleting {R2_BUCKET}/{key}...")
    try:
        subprocess.run(
            [_wrangler_cmd(), "r2", "object", "delete",
             f"{R2_BUCKET}/{key}", "--remote"],
            check=True,
        )
        print(f"OK. {key} deleted.")
    except subprocess.CalledProcessError as e:
        print(f"ERROR: {e}")
        sys.exit(1)


def main():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status", help="Show R2 lifecycle rules")
    p_del = sub.add_parser("delete", help="Manually delete a specific version")
    p_del.add_argument("version", help="e.g. 1.0.0")
    args = parser.parse_args()

    if args.cmd == "status":
        status()
    elif args.cmd == "delete":
        delete_version(args.version)


if __name__ == "__main__":
    main()
