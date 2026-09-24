# Release RunLab Mail (PyMail)

## Prasyarat

- Python **3.12** di `C:\Users\Administrator\AppData\Local\Programs\Python\Python312\python.exe`
- PyInstaller 6.20.0 terinstall di Python312: `py -3.12 -m pip install pyinstaller`
- `wrangler` CLI login sekali: `wrangler login`
- `.env` di `email_client_app/` berisi `R2_BUCKET` + `R2_PUBLIC_URL` (default: `pymail-releases` + `https://update-runlabmail.runlab.my.id`)

## ⚠️ Jangan pakai `python` default

Default `python` di shell mengarah ke venv `hermes-agent` yang **tidak punya PyInstaller**. Selalu pakai Python312 eksplisit.

## Cara release

```bat
cd email_client_app
"C:\Users\Administrator\AppData\Local\Programs\Python\Python312\python.exe" release.py --bump patch --notes "pesan rilis"
```

Atau lewat `release.bat` setelah set PATH/launcher yang pointing ke Python312.

### Flag berguna

- `--bump patch|minor|major` — auto-increment versi
- `<versi>` — set manual, mis. `release.py 1.9.51`
- `--notes "..."` — catatan rilis

## Yang terjadi otomatis

1. `core/version.py` di-bump
2. PyInstaller build `--onedir --windowed` → `dist/PyMail/PyMail.exe`
3. Smoke test: jalanin `.exe` sebentar
4. Zip + SHA256 → `dist/PyMail-1.9.XX.zip`
5. Upload ke Cloudflare R2 + `update_manifest.json`
6. Install lama auto-detect update di launch berikutnya

## Troubleshooting

**`No module named PyInstaller`** → bukan salah PyInstaller. PATH指向 venv yang salah. Pakai Python312 eksplisit.

**Release kepotong / Ctrl-C** → `version.py` udah naik tapi `dist/` belum ada. Run ulang dengan Python312 — `--bump patch` aman karena cuma naik 0.0.1.

**`wrangler not logged in`** → `wrangler login` di terminal yang sama.

**Timeout lama** → normal. PyInstaller 3-5 menit, upload R2 1-3 menit. Total 5-10 menit.