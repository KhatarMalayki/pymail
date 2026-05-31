"""
Prune old RunLab Mail release zips from the R2 bucket.

WHY: each release is ~45 MB. Wrangler has no "list objects" command, and the
R2 lifecycle rule only expires objects by AGE (90 days) — so when you release
often, dozens of versions pile up long before the rule ever deletes them.
This script deletes a range of PyMail-X.Y.Z.zip versions while ALWAYS keeping
a protected set (the current/live versions).

Deleting a non-existent key is a harmless no-op, so it's safe to sweep a wide
version range. Deletes run in parallel to keep the sweep fast.

Usage:
    # Delete every version from 1.2.0 up to 1.6.99, KEEP 1.7.0:
    python admin/prune_r2.py --from 1.2.0 --to 1.6.99 --keep 1.7.0

    # Preview only (delete nothing):
    python admin/prune_r2.py --from 1.2.0 --to 1.6.99 --keep 1.7.0 --dry-run

Notes:
    - Never deletes update_manifest.json or revoked.json (only PyMail-*.zip/.exe).
    - --keep can be repeated or comma-separated.
    - --max-patch caps the patch number swept per minor (default 99).
    - --workers sets parallelism (default 8).
"""
import argparse
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).parent.parent
APP_NAME = "PyMail"

env_file = ROOT / ".env"
if env_file.is_file():
    for line in env_file.read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.strip().startswith("#"):
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

R2_BUCKET = os.environ.get("R2_BUCKET", "pymail-releases")

_npm_bin = os.path.join(os.environ.get("APPDATA", ""), "npm")
if _npm_bin and _npm_bin not in os.environ.get("PATH", ""):
    os.environ["PATH"] = _npm_bin + os.pathsep + os.environ.get("PATH", "")


def _wrangler_cmd():
    if sys.platform == "win32":
        candidate = Path(os.environ.get("APPDATA", "")) / "npm" / "wrangler.cmd"
        if candidate.is_file():
            return str(candidate)
    return "wrangler"


WRANGLER = _wrangler_cmd()


def _parse_ver(v: str) -> tuple:
    parts = []
    for chunk in v.strip().lstrip("v").split("."):
        try:
            parts.append(int(chunk))
        except ValueError:
            parts.append(0)
    while len(parts) < 3:
        parts.append(0)
    return tuple(parts[:3])


def _delete_key(key: str) -> tuple:
    """Returns (key, deleted_bool). Treats 'not found' as not-deleted."""
    try:
        r = subprocess.run(
            [WRANGLER, "r2", "object", "delete",
             f"{R2_BUCKET}/{key}", "--remote"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=60,
        )
        # wrangler returns 0 even for missing keys; we can't tell a real
        # delete from a no-op, so we just report on exit code.
        return key, (r.returncode == 0)
    except Exception:
        return key, False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="lo", required=True)
    ap.add_argument("--to", dest="hi", required=True)
    ap.add_argument("--keep", action="append", default=[])
    ap.add_argument("--max-patch", type=int, default=99)
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    lo, hi = _parse_ver(args.lo), _parse_ver(args.hi)
    keep = set()
    for k in args.keep:
        for part in k.split(","):
            if part.strip():
                keep.add(_parse_ver(part))

    # Build the candidate key list.
    keys = []
    for major in range(lo[0], hi[0] + 1):
        for minor in range(0, 100):
            for patch in range(0, args.max_patch + 1):
                t = (major, minor, patch)
                if not (lo <= t <= hi) or t in keep:
                    continue
                ver = ".".join(str(x) for x in t)
                keys.append(f"{APP_NAME}-{ver}.zip")

    print(f"Bucket: {R2_BUCKET}")
    print(f"Range:  {lo} .. {hi}  (keep: {sorted(keep)})")
    print(f"Candidate keys: {len(keys)}  | workers: {args.workers}")
    print(f"Mode:   {'DRY RUN' if args.dry_run else 'DELETE'}")
    print()

    if args.dry_run:
        for k in keys[:20]:
            print(f"[dry-run] {k}")
        print(f"... ({len(keys)} total)")
        return

    done = 0
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = [ex.submit(_delete_key, k) for k in keys]
        for fut in as_completed(futs):
            key, ok = fut.result()
            done += 1
            if done % 25 == 0:
                print(f"  ...processed {done}/{len(keys)}")
    print()
    print(f"Done. Swept {len(keys)} version slots in parallel.")
    print("Run `python release.py` again to confirm the live version is intact,")
    print("or check the bucket size in the Cloudflare dashboard.")


if __name__ == "__main__":
    main()
