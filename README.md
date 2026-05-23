# PyMail

Desktop email client (POP3 + SMTP) for internal Tunas Group rollout.
Built with Python + PyQt5, packaged as a single Windows .exe.

Features include auto-update from Cloudflare R2, license management
via Cloudflare Worker, content-addressed attachment storage, full-text
search, signature templates, and conversation threading.

## Architecture

```
┌──────────────────┐     ┌──────────────────────┐     ┌─────────────────┐
│  PyMail.exe      │     │  Cloudflare Worker   │     │  Cloudflare R2  │
│  (user laptop)   │◄───►│  pymail-license      │◄───►│  pymail-releases│
│                  │     │                      │     │                 │
│  POP3/SMTP       │     │  /register           │     │  PyMail-X.exe   │
│  to mail server  │     │  /verify             │     │  manifest.json  │
│                  │     │  /admin/list,extend, │     │  revoked.json   │
│                  │     │   revoke,delete      │     │  users.json     │
└──────────────────┘     └──────────────────────┘     └─────────────────┘
```

- **PyMail.exe**: Single-file binary, ships with embedded Ed25519 public
  key. Stores user data at `~/.pymail/pymail.db` and attachments at
  `~/.pymail/attachments/<sha256>.bin` (deduped).
- **Cloudflare Worker**: License registry. Holds the Ed25519 private key
  as a Worker Secret; signs license payloads. Free tier covers 300+ users.
- **Cloudflare R2**: Public bucket for the `.exe` releases, manifest, and
  revoked-license list. Free tier covers ~570 MB of binaries.

## Prerequisites

- Python 3.12+
- Node.js 20+ (for `wrangler` CLI)
- A Cloudflare account (free)
- A GitHub account (private repo for source code)

## Local development

```cmd
pip install -r requirements.txt
python main.py
```

The app starts unactivated. Set up a POP3+SMTP account in
**Accounts → Add Account**. After the first successful Send/Receive,
the app calls `/register` on the configured License Worker and gets a
30-day trial.

## Releasing a new version

```cmd
release.bat patch "Fix bug X"        # 1.2.3 → 1.2.4
release.bat minor "New feature Y"    # 1.2.3 → 1.3.0
release.bat major "Big rewrite"      # 1.2.3 → 2.0.0
```

The script bumps `core/version.py`, builds the .exe with PyInstaller,
hashes it, generates `update_manifest.json`, and uploads both to R2.
Existing PyMail installs auto-update on next launch.

## License management

```cmd
# Issue 1 license
python admin/issue_license.py --name "Pak Budi" --email budi@x.com --days 30

# Bulk-issue from CSV (columns: name, email)
python admin/bulk_issue.py users.csv --days 90 --output keys.csv

# Revoke / restore
python admin/revoke_license.py revoke <id> "reason"
python admin/revoke_license.py restore <id>
python admin/revoke_license.py list
```

The same operations are available in-app via the **License Manager**
toolbar button (admin-only).

## Worker setup (one-time)

```cmd
cd worker
wrangler login
wrangler secret put PRIVATE_KEY_PKCS8        # paste base64 PKCS8 of .keys/license_private.key
wrangler secret put ADMIN_TOKEN               # paste random token (save in password manager)
wrangler deploy
```

Then put the Worker URL in `core/version.py` (`LICENSE_API_URL`).

See `worker/README.md` for full details.

## Project layout

```
email_client_app/
├── main.py                       Entry point
├── release.py / release.bat      Build + upload to R2
├── build.py                      Build only (no upload)
├── core/
│   ├── version.py               Version + remote URLs
│   ├── database.py              SQLite schema + queries
│   ├── attachment_store.py      Content-addressed file storage
│   ├── pop3_client.py           POP3 receive
│   ├── smtp_client.py           SMTP send
│   ├── mail_parser.py           MIME parsing
│   ├── license.py               Local license verification
│   ├── license_client.py        Worker API client
│   ├── updater.py               Auto-update logic
│   ├── contacts.py              Address book
│   ├── config.py                User config (data folder, admin token)
│   └── ...
├── ui/
│   ├── main_window.py           3-panel layout
│   ├── compose_dialog.py        Email composer (rich text)
│   ├── account_dialog.py        Account setup
│   ├── license_manager_dialog.py Admin: live user registry
│   ├── theme.py                 Outlook-inspired QSS
│   └── ...
├── admin/
│   ├── generate_keys.py         Ed25519 keypair (run once)
│   ├── issue_license.py         Single license issuer
│   ├── bulk_issue.py            CSV bulk issuer
│   ├── revoke_license.py        CLI revoke / restore
│   └── cleanup_releases.py      R2 lifecycle helpers
├── worker/
│   ├── src/index.js             Cloudflare Worker (license server)
│   ├── wrangler.toml            Worker config
│   └── README.md                Worker setup guide
├── resources/
│   └── pymail.ico               App icon
└── .env.example                 Copy to .env (R2 config)
```

## Critical files (DO NOT COMMIT)

These are in `.gitignore`:

- `.keys/license_private.key` — Ed25519 signing key. If you lose it, you
  can never issue new licenses for already-deployed binaries.
- `.env` — local config (R2 URLs)
- `admin/licenses_issued.json` — user PII log
- `dist/`, `build/`, `__pycache__/`, `*.spec` — build artifacts

**Backup `.keys/` to a password manager / encrypted USB.** It is the
only secret that can't be regenerated without invalidating every
existing license.
