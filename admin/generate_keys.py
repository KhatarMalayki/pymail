"""
Generate Ed25519 keypair for license signing. Run ONCE.

Outputs:
    .keys/license_private.key  -> KEEP SECRET, never commit, never embed in .exe
    core/license_pubkey.py     -> public key, embedded in the .exe (safe to ship)

After running once, you must NEVER run this again unless you want to
invalidate every license you've already issued.
"""
import base64
from pathlib import Path
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives import serialization

ROOT = Path(__file__).parent.parent
KEYS_DIR = ROOT / ".keys"
PRIVATE_FILE = KEYS_DIR / "license_private.key"
PUBKEY_FILE = ROOT / "core" / "license_pubkey.py"


def main():
    if PRIVATE_FILE.exists():
        print(f"ERROR: {PRIVATE_FILE} already exists.")
        print("Re-generating would invalidate every license already issued.")
        print("If you really mean to do this, delete the file first.")
        return

    KEYS_DIR.mkdir(parents=True, exist_ok=True)
    priv = Ed25519PrivateKey.generate()

    priv_pem = priv.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    PRIVATE_FILE.write_bytes(priv_pem)

    pub = priv.public_key()
    pub_raw = pub.public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    pub_b64 = base64.b64encode(pub_raw).decode()

    PUBKEY_FILE.write_text(
        f'"""Auto-generated. Public key only — safe to commit."""\n'
        f'LICENSE_PUBLIC_KEY_B64 = "{pub_b64}"\n',
        encoding="utf-8",
    )

    print(f"OK. Private key: {PRIVATE_FILE}")
    print(f"OK. Public key embedded: {PUBKEY_FILE}")
    print()
    print("IMPORTANT:")
    print(f"  - Add '.keys/' to .gitignore (already done if you used the template)")
    print(f"  - Backup {PRIVATE_FILE} somewhere safe (password manager, encrypted USB, etc.)")
    print(f"  - If you lose the private key, you can NEVER issue new licenses for")
    print(f"    this version of the app without rebuilding with a new pubkey.")


if __name__ == "__main__":
    main()
