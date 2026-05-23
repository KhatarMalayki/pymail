"""
Content-addressed attachment storage.

Files are stored on disk under ~/.pymail/attachments/<sha256-hex>.bin
Each file is keyed by the SHA256 hash of its bytes — identical content is
stored only once, no matter how many emails reference it.

A reference count is tracked in the DB so we know when nobody points to
a blob anymore and can safely delete it.
"""
import hashlib
import os
from pathlib import Path

from . import config


def _store_dir() -> Path:
    """Returns the per-user attachment storage folder, creating it on demand."""
    d = config.get_data_dir() / "attachments"
    d.mkdir(parents=True, exist_ok=True)
    return d


def hash_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def path_for_hash(file_hash: str) -> Path:
    # Two-level fanout (xx/yy) so a single folder doesn't end up with
    # tens of thousands of files (slows Windows Explorer + AV scans).
    return _store_dir() / file_hash[:2] / f"{file_hash}.bin"


def store(data: bytes) -> tuple[str, int]:
    """Save `data` to disk if not already present.

    Returns (sha256_hex, size_bytes). Idempotent — repeated calls with
    the same bytes are O(1) after the first write.
    """
    if not data:
        return "", 0
    digest = hash_bytes(data)
    target = path_for_hash(digest)
    if not target.is_file():
        target.parent.mkdir(parents=True, exist_ok=True)
        # Write atomically via .tmp + rename so a crash mid-write can't
        # leave a corrupt file.
        tmp = target.with_suffix(".bin.tmp")
        tmp.write_bytes(data)
        tmp.replace(target)
    return digest, len(data)


def load(file_hash: str) -> bytes | None:
    """Return the bytes for a hash, or None if missing."""
    if not file_hash:
        return None
    p = path_for_hash(file_hash)
    if not p.is_file():
        return None
    try:
        return p.read_bytes()
    except OSError:
        return None


def exists(file_hash: str) -> bool:
    return path_for_hash(file_hash).is_file()


def delete(file_hash: str) -> bool:
    """Remove a file from the store. Caller is responsible for ensuring
    no references remain (use db.attachment_decref instead in normal flow)."""
    p = path_for_hash(file_hash)
    try:
        p.unlink()
        # Best-effort cleanup of empty fanout dir
        try:
            p.parent.rmdir()
        except OSError:
            pass
        return True
    except FileNotFoundError:
        return True
    except OSError:
        return False


def total_size_bytes() -> int:
    """Sum of all stored attachment files."""
    total = 0
    base = _store_dir()
    for root, _, files in os.walk(base):
        for fn in files:
            try:
                total += (Path(root) / fn).stat().st_size
            except OSError:
                pass
    return total


def total_file_count() -> int:
    base = _store_dir()
    count = 0
    for _, _, files in os.walk(base):
        count += len(files)
    return count
