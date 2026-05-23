# PyMail License Worker

Cloudflare Worker that handles license registration, verification, and
admin operations for PyMail.

## Setup (one-time)

### 1. Install Wrangler (already done if you've been releasing PyMail)

```cmd
npm install -g wrangler
wrangler login
```

### 2. Convert your private key to a Cloudflare secret

The Ed25519 private key in `.keys/license_private.key` (PEM format) needs
to go into the Worker as a base64-encoded PKCS8 secret. Run from the
project root:

```cmd
python -c "import base64, pathlib; pem=pathlib.Path('.keys/license_private.key').read_bytes(); print(base64.b64encode(pem.split(b'-----BEGIN PRIVATE KEY-----')[1].split(b'-----END PRIVATE KEY-----')[0].strip()).decode())"
```

It prints a long base64 string. Copy it.

Then upload it as a secret:

```cmd
cd worker
wrangler secret put PRIVATE_KEY_PKCS8
```

When prompted, paste the base64 string.

### 3. Generate and set the admin token

```cmd
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

Copy the output (e.g. `abc123_xyz...`), then:

```cmd
wrangler secret put ADMIN_TOKEN
```

Paste it. Save the token in your password manager — PyMail's License
Manager dialog will prompt for it.

### 4. Deploy

```cmd
wrangler deploy
```

Output will say:
```
Published pymail-license
  https://pymail-license.<your-username>.workers.dev
```

Copy that URL. It's your license server.

### 5. Configure PyMail to talk to it

Edit `core/version.py` and add:

```python
LICENSE_API_URL = "https://pymail-license.<your-username>.workers.dev"
```

(Or set it via `pymail_update.json` next to the .exe for runtime
override — same pattern as the manifest URL.)

## Endpoints

### Public

- `POST /register` — auto-trial on first launch
- `POST /verify` — periodic license refresh

### Admin (require `X-Admin-Token` header)

- `POST /admin/list` — list all users
- `POST /admin/extend` — extend/shrink trial
- `POST /admin/revoke` — kill switch
- `POST /admin/restore` — un-revoke

## Local development

```cmd
wrangler dev
```

Local URL `http://localhost:8787` — useful for testing PyMail against a
local Worker before deploying.

## Deploy a new version

```cmd
wrangler deploy
```

Updates the live Worker. Existing user license keys remain valid (same
private key signing).

## Cost

100,000 free requests/day. PyMail typical traffic (300 users * ~3 calls
per day each) = 900 requests/day, well within free tier.
