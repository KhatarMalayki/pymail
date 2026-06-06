"""
Import emails and contacts from other mail apps into RunLab Mail.

Supported sources:
  - .eml files (single file or a whole folder tree) — Windows Live Mail,
    eM Client "Export to EML", Outlook "Save As .eml", Thunderbird, etc.
  - .mbox files — Thunderbird / eM Client / Apple Mail / generic exports.
  - Windows Live Mail store — auto-detected under %LOCALAPPDATA%.
  - Outlook .pst / .ost — read live through the installed Outlook via COM
    (pywin32). No bundled C library needed; Outlook must be installed.
  - Contacts: .csv (handled in core/contacts.py) and .vcf (here).

Every email path funnels through the SAME ingestion the live fetchers use:
    parse_message(raw_bytes)  ->  database.insert_email(account_id, folder, ...)
so imported mail is stored, indexed, de-duplicated and rendered identically.

All importers accept a `progress_cb(done:int, total:int, label:str)` and are
designed to run on a worker thread (they do no Qt work themselves).
"""
from __future__ import annotations

import email
import mailbox
import os
import re
from email import policy
from pathlib import Path

from . import database
from .mail_parser import parse_message


# Map common source folder names -> RunLab Mail folder ids.
_FOLDER_MAP = {
    "inbox": "inbox",
    "inbox/": "inbox",
    "sent": "sent",
    "sent items": "sent",
    "sent mail": "sent",
    "sent messages": "sent",
    "outbox": "outbox",
    "drafts": "drafts",
    "draft": "drafts",
    "junk": "spam",
    "junk e-mail": "spam",
    "spam": "spam",
    "bulk mail": "spam",
    "deleted": "trash",
    "deleted items": "trash",
    "trash": "trash",
    "bin": "trash",
}


def map_folder(name: str, default: str = "inbox") -> str:
    """Best-effort map an arbitrary source folder name to a RunLab folder id."""
    key = (name or "").strip().lower().strip("/\\")
    if key in _FOLDER_MAP:
        return _FOLDER_MAP[key]
    # Partial matches (e.g. "Karina - Sent Items")
    for token, folder in _FOLDER_MAP.items():
        if token and token in key:
            return folder
    return default


def _synth_uidl(parsed: dict, raw: bytes) -> str:
    """Build a stable de-dup key for an imported message. Prefer Message-ID;
    fall back to a hash of the raw bytes so re-importing the same file twice
    doesn't create duplicates."""
    mid = (parsed.get("message_id") or "").strip()
    if mid:
        return f"imp:{mid}"
    import hashlib
    return "imp:sha:" + hashlib.sha256(raw).hexdigest()[:32]


def _store_raw_email(account_id: int, folder: str, raw: bytes) -> bool:
    """Parse one raw RFC822 message and insert it. Returns True if inserted,
    False if skipped (duplicate or parse failure)."""
    try:
        parsed, attachments = parse_message(raw)
    except Exception:
        return False
    uidl = _synth_uidl(parsed, raw)
    parsed["uidl"] = uidl
    if database.email_exists(account_id, folder, uidl):
        return False
    try:
        database.insert_email(account_id, folder, parsed, attachments)
        return True
    except Exception:
        return False


# ---------------------------------------------------------------------------
# .eml import (single file or a folder tree)
# ---------------------------------------------------------------------------
def import_eml_file(account_id: int, path: str, folder: str = "inbox") -> int:
    """Import a single .eml file. Returns 1 if added, 0 otherwise."""
    raw = Path(path).read_bytes()
    return 1 if _store_raw_email(account_id, folder, raw) else 0


def import_eml_folder(account_id: int, root: str, progress_cb=None,
                      default_folder: str = "inbox") -> dict:
    """Recursively import every .eml under `root`. The immediate parent
    directory name is used to guess the target folder (Inbox/Sent/...).

    Returns {"added": int, "skipped": int, "folders": {folder: count}}.
    """
    root_path = Path(root)
    eml_files = [p for p in root_path.rglob("*.eml") if p.is_file()]
    total = len(eml_files)
    added = skipped = 0
    per_folder: dict[str, int] = {}
    for i, p in enumerate(eml_files, 1):
        # Folder guess from the containing directory relative to root.
        rel_parent = p.parent.name if p.parent != root_path else default_folder
        folder = map_folder(rel_parent, default_folder)
        try:
            raw = p.read_bytes()
        except Exception:
            skipped += 1
            continue
        if _store_raw_email(account_id, folder, raw):
            added += 1
            per_folder[folder] = per_folder.get(folder, 0) + 1
        else:
            skipped += 1
        if progress_cb:
            progress_cb(i, total, f"Importing .eml ({i}/{total})")
    return {"added": added, "skipped": skipped, "folders": per_folder}


# ---------------------------------------------------------------------------
# .mbox import
# ---------------------------------------------------------------------------
def import_mbox(account_id: int, path: str, folder: str = "inbox",
                progress_cb=None) -> dict:
    """Import all messages from a single .mbox file."""
    box = mailbox.mbox(path)
    keys = box.keys()
    total = len(keys)
    added = skipped = 0
    for i, key in enumerate(keys, 1):
        try:
            msg = box.get_message(key)
            raw = msg.as_bytes()
        except Exception:
            skipped += 1
            continue
        if _store_raw_email(account_id, folder, raw):
            added += 1
        else:
            skipped += 1
        if progress_cb:
            progress_cb(i, total, f"Importing mbox ({i}/{total})")
    return {"added": added, "skipped": skipped}


# ---------------------------------------------------------------------------
# Windows Live Mail auto-detect
# ---------------------------------------------------------------------------
def detect_windows_live_mail() -> list[str]:
    """Return candidate Windows Live Mail store roots that exist on disk."""
    roots = []
    local = os.environ.get("LOCALAPPDATA", "")
    appdata = os.environ.get("APPDATA", "")
    candidates = [
        Path(local) / "Microsoft" / "Windows Live Mail",
        Path(appdata) / "Microsoft" / "Windows Live Mail",
    ]
    for c in candidates:
        if c.is_dir() and any(c.rglob("*.eml")):
            roots.append(str(c))
    return roots


def import_windows_live_mail(account_id: int, root: str, progress_cb=None) -> dict:
    """Windows Live Mail stores each message as a .eml under per-folder
    directories — so this is just a folder import with folder mapping."""
    return import_eml_folder(account_id, root, progress_cb=progress_cb)


# ---------------------------------------------------------------------------
# Outlook .pst / .ost via COM (pywin32) — no bundled C library required
# ---------------------------------------------------------------------------
def outlook_available() -> bool:
    """True if Outlook can be driven via COM on this machine."""
    try:
        import win32com.client  # noqa: F401
        import pythoncom  # noqa: F401
    except Exception:
        return False
    return True


def import_pst_via_outlook(account_id: int, pst_path: str, progress_cb=None) -> dict:
    """Open a .pst with the locally-installed Outlook (COM), walk its folders,
    and import each message as RFC822 .eml bytes.

    Requires Microsoft Outlook to be installed. Raises RuntimeError with a
    friendly message if Outlook/COM isn't available.
    """
    try:
        import pythoncom
        import win32com.client
    except Exception as e:
        raise RuntimeError(
            "Microsoft Outlook (with pywin32) is required to read .pst files "
            "directly. Alternatively, export your mail to .eml or .mbox and "
            "use that importer."
        ) from e

    pythoncom.CoInitialize()
    added = skipped = 0
    per_folder: dict[str, int] = {}
    store = None
    app = None
    try:
        app = win32com.client.Dispatch("Outlook.Application")
        ns = app.GetNamespace("MAPI")
        # Explicitly log on to the default MAPI profile. If Outlook has never
        # finished its first-run setup, COM calls otherwise hang on a hidden
        # "Welcome to Outlook" wizard — so log on (no UI) and verify the MAPI
        # session is actually usable before doing real work.
        try:
            ns.Logon("", "", False, False)
        except Exception:
            pass
        try:
            # Touching Stores forces MAPI to connect; if Outlook isn't set up
            # this raises "You are not connected" instead of hanging forever.
            _ = ns.Stores.Count
        except Exception as e:
            raise RuntimeError(
                "Outlook is installed but not ready (it may need to be opened "
                "and set up once first). Open Outlook manually, complete its "
                "first-run setup, then try the import again.\n\n"
                "Alternatively, export your mail to .eml or .mbox and use that "
                "importer instead.\n\n"
                f"(details: {e})"
            ) from e
        ns.AddStore(pst_path)
        # The newly added store is the last one in the Stores collection.
        store = None
        for s in ns.Stores:
            try:
                if os.path.normcase(s.FilePath or "") == os.path.normcase(pst_path):
                    store = s
                    break
            except Exception:
                continue
        if store is None:
            raise RuntimeError("Outlook could not open the selected .pst file.")
        root_folder = store.GetRootFolder()

        # First pass: count messages for progress.
        all_folders = []
        _collect_folders(root_folder, all_folders)
        total = sum(_safe_count(f) for f in all_folders)
        done = 0

        for f in all_folders:
            target = map_folder(f.Name, "inbox")
            items = f.Items
            count = _safe_count(f)
            for idx in range(1, count + 1):
                try:
                    item = items.Item(idx)
                    raw = _outlook_item_to_eml(item)
                except Exception:
                    raw = None
                done += 1
                if raw and _store_raw_email(account_id, target, raw):
                    added += 1
                    per_folder[target] = per_folder.get(target, 0) + 1
                else:
                    skipped += 1
                if progress_cb and (done % 5 == 0 or done == total):
                    progress_cb(done, total, f"Importing Outlook: {f.Name}")
    finally:
        # Detach the PST store so Outlook doesn't keep it mounted.
        try:
            if store is not None and app is not None:
                app.GetNamespace("MAPI").RemoveStore(store.GetRootFolder())
        except Exception:
            pass
        try:
            pythoncom.CoUninitialize()
        except Exception:
            pass
    return {"added": added, "skipped": skipped, "folders": per_folder}


def _collect_folders(folder, out: list):
    """Depth-first collect all MAPIFolders under `folder`."""
    out.append(folder)
    try:
        for sub in folder.Folders:
            _collect_folders(sub, out)
    except Exception:
        pass


def _safe_count(folder) -> int:
    try:
        return folder.Items.Count
    except Exception:
        return 0


def _outlook_item_to_eml(item) -> bytes | None:
    """Convert an Outlook MailItem to RFC822 .eml bytes by saving it to a
    temp file in olMSGFormat... actually olEML isn't exposed everywhere, so
    we save as .msg-free RFC822 via SaveAs with olSaveAsType=olEML when
    available; otherwise fall back to building a minimal MIME message."""
    import tempfile
    # olEML = 7 (saves as MIME .eml). Not all Outlook builds expose it via
    # SaveAs cleanly, but it's the standard value.
    OL_EML = 7
    tmp = None
    try:
        tmp = Path(tempfile.gettempdir()) / f"runlabmail_imp_{abs(hash(item)) & 0xffffffff}.eml"
        item.SaveAs(str(tmp), OL_EML)
        data = tmp.read_bytes()
        return data
    except Exception:
        # Fallback: synthesize a minimal MIME message from common fields.
        try:
            from email.message import EmailMessage
            m = EmailMessage()
            m["Subject"] = getattr(item, "Subject", "") or "(no subject)"
            m["From"] = getattr(item, "SenderEmailAddress", "") or ""
            m["To"] = getattr(item, "To", "") or ""
            m["Cc"] = getattr(item, "CC", "") or ""
            try:
                sent = getattr(item, "SentOn", None)
                if sent:
                    m["Date"] = sent.Format("%a, %d %b %Y %H:%M:%S %z")
            except Exception:
                pass
            body_html = getattr(item, "HTMLBody", "") or ""
            body_text = getattr(item, "Body", "") or ""
            if body_html:
                m.set_content(body_text or " ")
                m.add_alternative(body_html, subtype="html")
            else:
                m.set_content(body_text or " ")
            return m.as_bytes()
        except Exception:
            return None
    finally:
        try:
            if tmp and tmp.exists():
                tmp.unlink()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Contacts: .vcf (vCard) import
# ---------------------------------------------------------------------------
def import_vcf(path: str) -> tuple[int, int]:
    """Import contacts from a .vcf (vCard) file. Returns (added, skipped).

    Handles vCard 2.1/3.0/4.0 FN + EMAIL lines (the common subset every app
    exports). Multiple vCards in one file are supported.
    """
    try:
        text = Path(path).read_text(encoding="utf-8-sig")
    except UnicodeDecodeError:
        text = Path(path).read_text(encoding="cp1252", errors="replace")

    pairs: list[tuple[str, str]] = []
    name = ""
    fn = ""
    n_field = ""
    emails: list[str] = []

    def _flush():
        nonlocal name, fn, n_field, emails
        display = fn or _name_from_n(n_field) or ""
        for e in emails:
            pairs.append((display, e))
        name = fn = n_field = ""
        emails = []

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        upper = line.upper()
        if upper == "BEGIN:VCARD":
            fn = n_field = ""
            emails = []
        elif upper == "END:VCARD":
            _flush()
        elif upper.startswith("FN:"):
            fn = line[3:].strip()
        elif upper.startswith("N:"):
            n_field = line[2:].strip()
        elif upper.startswith("EMAIL"):
            # EMAIL;TYPE=WORK:foo@bar.com  /  EMAIL:foo@bar.com
            val = line.split(":", 1)[1].strip() if ":" in line else ""
            if val and "@" in val:
                emails.append(val)

    added = database.upsert_manual_contacts(pairs) if pairs else 0
    skipped = 0  # rows without an email are simply not collected
    return added, skipped


def _name_from_n(n_field: str) -> str:
    """vCard N: is 'Family;Given;Additional;Prefix;Suffix'. Build 'Given Family'."""
    if not n_field:
        return ""
    parts = n_field.split(";")
    family = parts[0].strip() if len(parts) > 0 else ""
    given = parts[1].strip() if len(parts) > 1 else ""
    return (given + " " + family).strip()
