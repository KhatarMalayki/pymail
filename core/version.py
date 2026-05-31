"""
RunLab Mail (codename PyMail) — version & remote URLs.

Bump __version__ on every release before building a new .exe.
Or just run `release.py` / `release.bat` which handles everything.
"""

__version__ = "1.8.0"

# Cloudflare R2 hosting (public bucket, no CDN cache by default)
DEFAULT_MANIFEST_URLS = [
    "https://update-runlabmail.runlab.my.id/update_manifest.json",
]
DEFAULT_MANIFEST_URL = DEFAULT_MANIFEST_URLS[0]

# Cloudflare Worker for license auto-registration + admin API
LICENSE_API_URL = "https://pymail-license.khatarmalayki21.workers.dev"
