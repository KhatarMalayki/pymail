"""
Build PyMail.exe with PyInstaller, plus auto-generate update_manifest.json.

Usage:
    python build.py                              # build with placeholder URL
    python build.py --debug                      # show console window
    python build.py --url https://your.com/...   # set download URL in manifest

Outputs:
    dist/PyMail.exe                  - the built executable
    dist/PyMail-{version}.exe        - versioned copy (upload this)
    dist/update_manifest.json        - manifest ready to upload

Release flow:
    1. Bump core/version.py (__version__)
    2. python build.py --url https://your-host.com/files/PyMail-1.0.1.exe
    3. Upload dist/PyMail-1.0.1.exe AND dist/update_manifest.json to your host
    4. Existing installs auto-upgrade on next launch
"""
import argparse
import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parent
APP_NAME = "PyMail"


def read_version() -> str:
    for line in (ROOT / "core" / "version.py").read_text(encoding="utf-8").splitlines():
        if line.startswith("__version__"):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    return "0.0.0"


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--debug", action="store_true", help="Show console window")
    parser.add_argument("--url", default="", help="Download URL for the manifest")
    parser.add_argument("--notes", default="", help="Release notes")
    parser.add_argument("--mandatory", action="store_true", help="Mark update as mandatory")
    args = parser.parse_args()

    version = read_version()
    print(f"Building {APP_NAME} v{version} ...")

    # Clean previous artifacts
    for d in ("build", "dist"):
        p = ROOT / d
        if p.exists():
            shutil.rmtree(p)
    for f in ROOT.glob("*.spec"):
        f.unlink()

    pyi_args = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm", "--clean", "--onefile",
        "--name", APP_NAME,
        "main.py",
    ]
    if not args.debug:
        pyi_args.append("--windowed")

    icon = ROOT / "resources" / "pymail.ico"
    if icon.is_file():
        pyi_args += ["--icon", str(icon)]

    print(" ".join(pyi_args))
    result = subprocess.run(pyi_args, cwd=ROOT)
    if result.returncode != 0:
        print("Build failed.")
        sys.exit(result.returncode)

    out = ROOT / "dist" / f"{APP_NAME}.exe"
    if not out.is_file():
        print("Build finished but output not found.")
        sys.exit(1)

    # Versioned copy + checksum
    versioned = out.with_name(f"{APP_NAME}-{version}.exe")
    shutil.copy2(out, versioned)
    sha = sha256_of(versioned)
    size_mb = versioned.stat().st_size / 1024 / 1024

    # Build manifest
    manifest = {
        "version": version,
        "url": args.url or f"https://REPLACE-ME.example.com/PyMail-{version}.exe",
        "sha256": sha,
        "notes": args.notes or f"PyMail {version}",
        "mandatory": args.mandatory,
    }
    manifest_path = ROOT / "dist" / "update_manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    print()
    print("=" * 60)
    print(f"Built:    {versioned.name}  ({size_mb:.1f} MB)")
    print(f"SHA256:   {sha}")
    print(f"Manifest: {manifest_path}")
    print("=" * 60)
    if not args.url:
        print("⚠  Manifest URL is a placeholder. Either:")
        print("     - Edit dist/update_manifest.json and set 'url'")
        print("     - Or rebuild with: python build.py --url https://...")
    print()
    print("To release this version:")
    print(f"  1. Upload dist/{versioned.name} to your hosting")
    print( "  2. Upload dist/update_manifest.json to the URL configured")
    print( "     in core/version.py (DEFAULT_MANIFEST_URL)")
    print( "  3. Existing installs will auto-upgrade on next launch.")


if __name__ == "__main__":
    main()
