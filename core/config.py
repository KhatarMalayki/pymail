"""
Application config (key/value JSON file).

Stored at %USERPROFILE%/.pymail/config.json. Holds settings the user can
change from the Settings dialog (e.g. data folder location).
"""
import json
import os
import shutil
from pathlib import Path

DEFAULT_BASE = Path(os.path.expanduser("~")) / ".pymail"
DEFAULT_BASE.mkdir(parents=True, exist_ok=True)
CONFIG_FILE = DEFAULT_BASE / "config.json"


def _load() -> dict:
    if not CONFIG_FILE.is_file():
        return {}
    try:
        return json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save(cfg: dict) -> None:
    try:
        CONFIG_FILE.write_text(
            json.dumps(cfg, indent=2), encoding="utf-8"
        )
    except Exception:
        pass


def get(key: str, default=None):
    return _load().get(key, default)


def set_value(key: str, value) -> None:
    cfg = _load()
    cfg[key] = value
    _save(cfg)


# ---------- Convenience: data folder ----------

def get_data_dir() -> Path:
    """Folder where pymail.db lives. Defaults to ~/.pymail."""
    custom = get("data_dir")
    if custom:
        p = Path(custom)
        p.mkdir(parents=True, exist_ok=True)
        return p
    return DEFAULT_BASE


def get_db_path() -> Path:
    return get_data_dir() / "pymail.db"


def change_data_dir(new_dir: Path, copy_existing: bool = True) -> Path:
    """Move the data folder to a new location. Returns the new DB path.

    Steps:
        1. Create the target directory
        2. Optionally copy the existing DB to the new location
        3. Update config.json
        4. Caller is expected to restart the app (sqlite handles need to
           be released first)
    """
    new_dir = Path(new_dir)
    new_dir.mkdir(parents=True, exist_ok=True)

    if copy_existing:
        old_db = get_db_path()
        new_db = new_dir / "pymail.db"
        if old_db.is_file() and old_db.resolve() != new_db.resolve():
            shutil.copy2(old_db, new_db)

    set_value("data_dir", str(new_dir))
    return new_dir / "pymail.db"
