"""
One-shot release script: build + publish to Cloudflare R2.

Uses the existing `wrangler` CLI authentication on your machine. Login once
with `wrangler login`, then every release is just:

    python release.py 1.0.10
    python release.py 1.0.10 --notes "Fix bug X"
    python release.py --bump patch         (auto-increment patch version)

What this does:
    1. Bumps core/version.py
    2. Builds dist/PyMail.exe (PyInstaller, single-file)
    3. Computes SHA256
    4. Generates dist/update_manifest.json
    5. Uploads PyMail-{version}.exe and update_manifest.json to R2
    6. Existing PyMail installs auto-detect the new version on next launch

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
from pathlib import Path

ROOT = Path(__file__).parent
APP_NAME = "PyMail"

# Defaults (override via .env)
DEFAULT_R2_BUCKET = "pymail-releases"
DEFAULT_R2_PUBLIC_URL = "https://pub-fbab08d87bad4965bfcade6e58bd1fa6.r2.dev"

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
                shutil.rmtree(p)
        for f in ROOT.glob("*.spec"):
            f.unlink()

        pyi_args = [
            sys.executable, "-m", "PyInstaller",
            "--noconfirm", "--clean", "--onefile",
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

    out = ROOT / "dist" / f"{APP_NAME}.exe"
    if not out.is_file():
        print("ERROR: build output not found.")
        sys.exit(1)

    versioned = out.with_name(f"{APP_NAME}-{version}.exe")
    shutil.copy2(out, versioned)
    sha = sha256_of(versioned)
    size_mb = versioned.stat().st_size / 1024 / 1024
    print(f"Built: {versioned.name} ({size_mb:.1f} MB)")
    print(f"SHA256: {sha}")

    download_url = f"{R2_PUBLIC_URL}/{APP_NAME}-{version}.exe"
    manifest = {
        "version": version,
        "url": download_url,
        "sha256": sha,
        "notes": args.notes or f"RunLab Mail {version}",
        "mandatory": args.mandatory,
    }
    manifest_path = ROOT / "dist" / "update_manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    print()
    print("Uploading to Cloudflare R2...")
    # Order matters: upload .exe FIRST so manifest never points to a missing file
    upload_to_r2(versioned, f"{APP_NAME}-{version}.exe", "application/octet-stream")
    upload_to_r2(manifest_path, "update_manifest.json", "application/json")

    # Note: old PyMail-*.exe binaries are auto-deleted after 90 days by the
    # R2 lifecycle rule "auto-cleanup-old-binaries" (set up once during
    # R2 migration). Manual delete: python admin/cleanup_releases.py delete <ver>

    print()
    print("=" * 60)
    print(f"[OK] Released {APP_NAME} {version}")
    print(f"  Manifest: {R2_PUBLIC_URL}/update_manifest.json")
    print(f"  Binary:   {download_url}")
    print()
    print("Existing installs will auto-update on next launch.")
    print("=" * 60)


if __name__ == "__main__":
    main()
