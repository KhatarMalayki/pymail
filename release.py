"""
One-shot release script: build + publish to Cloudflare R2.

Uses the existing `wrangler` CLI authentication on your machine. Login once
with `wrangler login`, then every release is just:

    python release.py 1.0.10
    python release.py 1.0.10 --notes "Fix bug X"
    python release.py --bump patch         (auto-increment patch version)

What this does:
    1. Bumps core/version.py
    2. Builds dist/PyMail/ (PyInstaller, one-dir)
    3. Computes SHA256
    4. Generates dist/update_manifest.json
    5. Uploads PyMail-{version}.zip and update_manifest.json to R2
    6. Existing RunLab Mail installs auto-detect the new version on next launch

Configuration:
    R2_BUCKET and R2_PUBLIC_URL are read from .env (or defaults below).
"""
import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
import zipfile
from pathlib import Path

ROOT = Path(__file__).parent
APP_NAME = "PyMail"

# Defaults (override via .env)
DEFAULT_R2_BUCKET = "pymail-releases"
DEFAULT_R2_PUBLIC_URL = "https://update-runlabmail.runlab.my.id"

# Read .env so secrets/config are not hardcoded
env_file = ROOT / ".env"
if env_file.is_file():
    for line in env_file.read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.strip().startswith("#"):
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

R2_BUCKET = os.environ.get("R2_BUCKET", DEFAULT_R2_BUCKET)
R2_PUBLIC_URL = os.environ.get("R2_PUBLIC_URL", DEFAULT_R2_PUBLIC_URL).rstrip("/")

# Make sure npm global bin is in PATH so wrangler is reachable on Windows
_npm_bin = os.path.join(os.environ.get("APPDATA", ""), "npm")
if _npm_bin and _npm_bin not in os.environ.get("PATH", ""):
    os.environ["PATH"] = _npm_bin + os.pathsep + os.environ.get("PATH", "")


def _wrangler_cmd():
    """Return the wrangler command path. On Windows, .cmd shims need to be
    invoked explicitly because subprocess can't find shell shims via PATH
    without shell=True."""
    if sys.platform == "win32":
        candidate = Path(os.environ.get("APPDATA", "")) / "npm" / "wrangler.cmd"
        if candidate.is_file():
            return str(candidate)
    return "wrangler"


def run(cmd, **kw):
    print(f">>> {' '.join(str(c) for c in cmd)}")
    return subprocess.run(cmd, check=True, **kw)


def _rmtree_retry(path: Path, retries: int=6, delay: float=0.6) -> None:
    """Best-effort rmtree for Windows file locks (AV/indexer/transient)."""
    if not path.exists():
        return
    last_err = None
    for _ in range(retries):
        try:
            shutil.rmtree(path)
            return
        except PermissionError as e:
            last_err = e
            time.sleep(delay)
    if last_err is not None:
        raise last_err


def _smoke_test_exe(exe_path: Path, seconds: int=4) -> None:
    """Start the built EXE briefly to catch bootstrap/runtime corruption.

    If the process exits immediately, release is aborted.
    """
    print(f">>> Smoke testing {exe_path.name} for {seconds}s...")
    proc = subprocess.Popen([str(exe_path)])
    try:
        time.sleep(seconds)
        code = proc.poll()
        if code is not None:
            raise RuntimeError(
                f"Smoke test failed: {exe_path.name} exited early with code {code}."
            )
    finally:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill()


def _zip_dir(src_dir: Path, out_zip: Path) -> None:
    if out_zip.exists():
        out_zip.unlink()
    with zipfile.ZipFile(out_zip, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for p in src_dir.rglob("*"):
            if p.is_file():
                zf.write(p, p.relative_to(src_dir.parent))


def read_version() -> str:
    text = (ROOT / "core" / "version.py").read_text(encoding="utf-8")
    m = re.search(r'__version__\s*=\s*["\']([^"\']+)["\']', text)
    return m.group(1) if m else "0.0.0"


def write_version(new_version: str) -> None:
    p = ROOT / "core" / "version.py"
    text = p.read_text(encoding="utf-8")
    text = re.sub(
        r'(__version__\s*=\s*)["\'][^"\']+["\']',
        f'\\1"{new_version}"',
        text,
    )
    p.write_text(text, encoding="utf-8")


def bump(current: str, kind: str) -> str:
    parts = [int(x) for x in current.split(".")]
    while len(parts) < 3:
        parts.append(0)
    if kind == "major":
        parts = [parts[0] + 1, 0, 0]
    elif kind == "minor":
        parts = [parts[0], parts[1] + 1, 0]
    elif kind == "patch":
        parts = [parts[0], parts[1], parts[2] + 1]
    return ".".join(str(x) for x in parts)


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def check_wrangler() -> None:
    wrangler = _wrangler_cmd()
    try:
        subprocess.run([wrangler, "--version"],
                       check=True, capture_output=True)
    except (FileNotFoundError, subprocess.CalledProcessError):
        print("ERROR: Cloudflare Wrangler CLI is not installed or not in PATH.")
        print()
        print("Install it once:")
        print("    npm install -g wrangler")
        print()
        print("Then login once (opens browser):")
        print("    wrangler login")
        sys.exit(1)
    try:
        subprocess.run([wrangler, "whoami"],
                       check=True, capture_output=True)
    except subprocess.CalledProcessError:
        print("ERROR: wrangler is installed but not logged in.")
        print("Login: wrangler login")
        sys.exit(1)


def upload_to_r2(local_path: Path, remote_key: str, content_type: str):
    cmd = [
        _wrangler_cmd(), "r2", "object", "put",
        f"{R2_BUCKET}/{remote_key}",
        "--file", str(local_path),
        "--content-type", content_type,
        "--remote",
    ]
    run(cmd)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "version", nargs="?",
        help="Explicit version, e.g. 1.0.10. Or use --bump",
    )
    parser.add_argument(
        "--bump", choices=("major", "minor", "patch"),
        help="Auto-increment from current version",
    )
    parser.add_argument("--notes", default="", help="Release notes")
    parser.add_argument("--mandatory", action="store_true", help="Mark update mandatory")
    parser.add_argument("--skip-build", action="store_true", help="Reuse existing dist/")
    args = parser.parse_args()

    check_wrangler()

    current = read_version()
    if args.bump:
        version = bump(current, args.bump)
    elif args.version:
        version = args.version.lstrip("v")
    else:
        print(f"Current version: {current}")
        print("Usage:")
        print("    python release.py 1.0.10               # explicit version")
        print("    python release.py --bump patch         # auto bump")
        sys.exit(1)

    print(f"Releasing {APP_NAME} {current} -> {version}")
    print(f"Target R2 bucket: {R2_BUCKET}")
    print(f"Public URL:       {R2_PUBLIC_URL}")
    print()

    write_version(version)
    print(f"Updated core/version.py to {version}")

    if not args.skip_build:
        for d in ("build", "dist"):
            p = ROOT / d
            if p.exists():
                _rmtree_retry(p)
        for f in ROOT.glob("*.spec"):
            f.unlink()

        pyi_args = [
            sys.executable, "-m", "PyInstaller",
            "--noconfirm", "--clean", "--onedir",
            "--windowed",
            "--name", APP_NAME,
            "main.py",
        ]
        icon = ROOT / "resources" / "pymail.ico"
        if icon.is_file():
            pyi_args += ["--icon", str(icon)]
            # Also bundle resources/ folder so the app can load the icon
            # at runtime for the window title bar
            pyi_args += ["--add-data", f"{ROOT / 'resources'}{os.pathsep}resources"]
        run(pyi_args, cwd=ROOT)

    out = ROOT / "dist" / APP_NAME / f"{APP_NAME}.exe"
    if not out.is_file():
        print("ERROR: build output not found.")
        sys.exit(1)

    _smoke_test_exe(out)

    package_zip = ROOT / "dist" / f"{APP_NAME}-{version}.zip"
    _zip_dir(out.parent, package_zip)
    sha = sha256_of(package_zip)
    size_mb = package_zip.stat().st_size / 1024 / 1024
    print(f"Built: {package_zip.name} ({size_mb:.1f} MB)")
    print(f"SHA256: {sha}")

    download_url = f"{R2_PUBLIC_URL}/{APP_NAME}-{version}.zip"
    manifest = {
        "version": version,
        "url": download_url,
        "sha256": sha,
        "package_type": "zip_onedir",
        "entry_exe": f"{APP_NAME}.exe",
        "notes": args.notes or f"RunLab Mail {version}",
        "mandatory": args.mandatory,
    }
    manifest_path = ROOT / "dist" / "update_manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    print()
    print("Uploading to Cloudflare R2...")
    # Order matters: upload package FIRST so manifest never points to a missing file
    upload_to_r2(package_zip, f"{APP_NAME}-{version}.zip", "application/zip")
    upload_to_r2(manifest_path, "update_manifest.json", "application/json")

    # ---- Keep R2 storage bounded: retain only the last KEEP_RELEASES zips ----
    # Each release is ~45 MB; on a free 10 GB plan they pile up fast. We track
    # what we've published in a local ledger and delete anything older than the
    # newest KEEP_RELEASES. (The R2 lifecycle rule is a slower 14-day backstop.)
    try:
        _prune_old_releases(version)
    except Exception as e:
        print(f"  (retention prune skipped: {e})")

    print()
    print("=" * 60)
    print(f"[OK] Released {APP_NAME} {version}")
    print(f"  Manifest: {R2_PUBLIC_URL}/update_manifest.json")
    print(f"  Binary:   {download_url}")
    print()
    print("Existing installs will auto-update on next launch.")
    print("=" * 60)


# How many recent release zips to keep on R2 (older ones are deleted after
# each successful publish). 3 = current + 2 previous for quick rollback.
KEEP_RELEASES = 3
RELEASE_LEDGER = ROOT / "admin" / "released_versions.json"


def _prune_old_releases(current_version: str) -> None:
    """Delete release zips older than the newest KEEP_RELEASES, using a local
    ledger of versions we've published so we never guess at object keys."""
    ledger = []
    if RELEASE_LEDGER.is_file():
        try:
            ledger = json.loads(RELEASE_LEDGER.read_text(encoding="utf-8"))
        except Exception:
            ledger = []
    if current_version not in ledger:
        ledger.append(current_version)

    # Sort newest-first by numeric version tuple.
    def _vt(v):
        out = []
        for c in str(v).split("."):
            try:
                out.append(int(c))
            except ValueError:
                out.append(0)
        return tuple(out)

    ledger_sorted = sorted(set(ledger), key=_vt, reverse=True)
    keep = ledger_sorted[:KEEP_RELEASES]
    drop = ledger_sorted[KEEP_RELEASES:]

    for v in drop:
        key = f"{APP_NAME}-{v}.zip"
        print(f"  Retention: deleting old release {key}")
        try:
            run([
                _wrangler_cmd(), "r2", "object", "delete",
                f"{R2_BUCKET}/{key}", "--remote",
            ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception:
            pass  # already gone / transient — lifecycle rule will catch it

    # Ledger keeps only what we still retain on R2.
    RELEASE_LEDGER.parent.mkdir(parents=True, exist_ok=True)
    RELEASE_LEDGER.write_text(json.dumps(keep, indent=2), encoding="utf-8")
    print(f"  Retention: keeping {keep}")


if __name__ == "__main__":
    main()
