"""
RunLab Mail (codename PyMail) — version & remote URLs.

Bump __version__ on every release before building a new .exe.
Or just run `release.py` / `release.bat` which handles everything.
"""

__version__ = "1.2.6"

# Cloudflare R2 hosting (public bucket, no CDN cache by default)
DEFAULT_MANIFEST_URL = (
    "https://pub-fbab08d87bad4965bfcade6e58bd1fa6.r2.dev"
    "/update_manifest.json"
)

# Cloudflare Worker for license auto-registration + admin API
LICENSE_API_URL = "https://pymail-license.khatarmalayki21.workers.dev"
