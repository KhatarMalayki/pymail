"""
Application config (key/value JSON file).

Stored at %USERPROFILE%/.runlabmail/config.json by default (with legacy
compatibility for older %USERPROFILE%/.pymail installs). Holds settings the
user can change from the Settings dialog (e.g. data folder location).
"""
import json
import os
import shutil
from pathlib import Path

LEGACY_BASE = Path(os.path.expanduser("~")) / ".pymail"
DEFAULT_BASE = Path(os.path.expanduser("~")) / ".runlabmail"
DB_FILENAME = "runlabmail.db"
LEGACY_DB_FILENAME = "pymail.db"


def _bootstrap_base() -> None:
    DEFAULT_BASE.mkdir(parents=True, exist_ok=True)

    legacy_cfg = LEGACY_BASE / "config.json"
    new_cfg = DEFAULT_BASE / "config.json"
    if not new_cfg.is_file() and legacy_cfg.is_file():
        try:
            shutil.copy2(legacy_cfg, new_cfg)
        except Exception:
            pass

    legacy_secrets = LEGACY_BASE / "secrets.dat"
    new_secrets = DEFAULT_BASE / "secrets.dat"
    if not new_secrets.is_file() and legacy_secrets.is_file():
        try:
            shutil.copy2(legacy_secrets, new_secrets)
        except Exception:
            pass

    try:
        cfg = json.loads(new_cfg.read_text(encoding="utf-8")) if new_cfg.is_file() else {}
    except Exception:
        cfg = {}

    if not cfg.get("data_dir") and (
        (LEGACY_BASE / DB_FILENAME).is_file() or (LEGACY_BASE / LEGACY_DB_FILENAME).is_file()
    ):
        cfg["data_dir"] = str(LEGACY_BASE)
        try:
            new_cfg.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
        except Exception:
            pass


_bootstrap_base()
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
    """Folder where the app database lives. Defaults to ~/.runlabmail."""
    custom = get("data_dir")
    if custom:
        p = Path(custom)
        p.mkdir(parents=True, exist_ok=True)
        return p
    return DEFAULT_BASE


def _migrate_legacy_db_filename(data_dir: Path) -> Path:
    preferred = data_dir / DB_FILENAME
    legacy = data_dir / LEGACY_DB_FILENAME

    if preferred.is_file() or not legacy.is_file():
        return preferred if preferred.is_file() else legacy

    try:
        legacy.replace(preferred)
        return preferred
    except Exception:
        try:
            shutil.copy2(legacy, preferred)
            return preferred
        except Exception:
            return legacy


def get_db_path() -> Path:
    data_dir = get_data_dir()
    preferred = data_dir / DB_FILENAME
    legacy = data_dir / LEGACY_DB_FILENAME
    if preferred.is_file():
        return preferred
    if legacy.is_file():
        return _migrate_legacy_db_filename(data_dir)
    return preferred


def change_data_dir(new_dir: Path, copy_existing: bool=True) -> Path:
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
        new_db = new_dir / DB_FILENAME
        if old_db.is_file() and old_db.resolve() != new_db.resolve():
            shutil.copy2(old_db, new_db)

    set_value("data_dir", str(new_dir))
    return new_dir / DB_FILENAME
