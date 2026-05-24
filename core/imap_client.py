"""
IMAP fetcher for the Junk folder (read-only).

Used by the hybrid POP3+IMAP flow:
    - POP3 still owns INBOX (existing behavior)
    - IMAP is used only to discover and fetch the server-side Junk folder,
      because POP3 protocol cannot list folders other than INBOX.

Design choices:
    - Read-only access: SELECT (not EXAMINE). We never DELETE or MOVE on
      the server. If a user wants to clean Junk on the server, they use
      Carbonio webmail. This avoids the risk of bugs nuking server email.
    - UIDs are namespaced as `imap-junk:<uid>` in the local DB so they
      cannot collide with POP3 UIDLs.
    - Junk emails land in the same local folder ("spam") as POP3-marked
      junk, so the UI doesn't change.

Common server folder names tried in order when auto-detecting:
    Junk, Spam, INBOX/Junk, INBOX/Spam, INBOX.Junk, INBOX.Spam,
    Junk Email, Junk E-Mail, Bulk Mail, Bulk
"""
import imaplib
import socket
from . import database
from .mail_parser import parse_message


# Common Junk folder names across providers.
# Order matters: try the most likely first.
JUNK_FOLDER_CANDIDATES = [
    "Junk",
    "Spam",
    "INBOX/Junk",
    "INBOX/Spam",
    "INBOX.Junk",
    "INBOX.Spam",
    "Junk Email",
    "Junk E-Mail",
    "Bulk Mail",
    "Bulk",
    "[Gmail]/Spam",  # Gmail
]


# Cap how many junk messages to download per sync. Junk should be small,
# but defensively limit to avoid huge syncs on a poorly-maintained server.
MAX_JUNK_FETCH = 200

# Local folder we store junk into (matches the existing UI label "Junk")
LOCAL_FOLDER = "spam"

# Prefix for IMAP UIDs in the local DB so they never collide with POP3 UIDLs
IMAP_UID_PREFIX = "imap-junk:"


class IMAPError(Exception):
    pass


def _connect(account: dict) -> imaplib.IMAP4:
    host = account.get("imap_host") or ""
    if not host:
        raise IMAPError("IMAP host not configured.")
    port = int(account.get("imap_port") or (993 if account.get("imap_ssl", 1) else 143))
    use_ssl = bool(account.get("imap_ssl", 1))
    timeout = 30
    try:
        if use_ssl:
            conn = imaplib.IMAP4_SSL(host, port, timeout=timeout)
        else:
            conn = imaplib.IMAP4(host, port, timeout=timeout)
        # IMAP authentication uses the same creds as POP3 by convention
        user = account.get("imap_user") or account.get("pop3_user") or account.get("email")
        pwd = account.get("imap_password") or account.get("pop3_password")
        conn.login(user, pwd)
        return conn
    except (imaplib.IMAP4.error, socket.error, socket.timeout) as e:
        raise IMAPError(f"IMAP connection failed: {e}") from e


def list_folders(account: dict) -> list[str]:
    """Return all folder names visible on the IMAP server. Used by the
    UI auto-detect button."""
    conn = _connect(account)
    folders = []
    try:
        typ, data = conn.list()
        if typ != "OK" or not data:
            return []
        for item in data:
            if item is None:
                continue
            line = item.decode("utf-8", errors="replace") if isinstance(item, bytes) else str(item)
            # Format: (\HasNoChildren) "/" "INBOX/Junk"
            # Folder name is the last quoted token, or the last whitespace token.
            if '"' in line:
                # Take everything after the last quoted delimiter
                parts = line.rsplit('"', 2)  # ['(\\HasNoChildren) "/" ', 'INBOX/Junk', '']
                if len(parts) >= 2:
                    folders.append(parts[-2])
            else:
                tokens = line.split()
                if tokens:
                    folders.append(tokens[-1])
        return folders
    finally:
        try:
            conn.logout()
        except Exception:
            pass


def detect_junk_folder(account: dict) -> str | None:
    """Try common Junk folder names; return the first one that exists.
    Returns None if no Junk folder is found."""
    folders = list_folders(account)
    if not folders:
        return None
    # Case-insensitive lookup
    lower_to_real = {f.lower(): f for f in folders}
    for candidate in JUNK_FOLDER_CANDIDATES:
        real = lower_to_real.get(candidate.lower())
        if real:
            return real
    # Heuristic fallback: any folder whose name contains "junk" or "spam"
    for f in folders:
        low = f.lower()
        if "junk" in low or "spam" in low:
            return f
    return None


def test_connection(account: dict) -> tuple[bool, str]:
    """Test the IMAP credentials and try to find the Junk folder."""
    try:
        folders = list_folders(account)
        if not folders:
            return False, "Connected, but server returned no folders."
        junk = detect_junk_folder(account)
        if junk:
            return True, f"OK. Junk folder found: '{junk}'.\nServer has {len(folders)} folder(s) total."
        return True, (
            "OK. Connected, but no Junk/Spam folder was found.\n"
            f"Server folders: {', '.join(folders[:10])}"
            + ("..." if len(folders) > 10 else "")
        )
    except Exception as e:
        return False, str(e)


def fetch_junk(account: dict, progress_cb=None, log_cb=None) -> int:
    """Fetch new messages from the configured (or auto-detected) Junk
    folder on the IMAP server. Read-only — never modifies the server.

    Returns: number of new junk emails saved locally.
    Raises IMAPError on connection / auth issues.
    """
    if not account.get("imap_enabled"):
        return 0
    if not account.get("imap_host"):
        return 0

    folder_name = account.get("junk_folder_name") or ""
    if not folder_name:
        # Auto-detect on first run
        folder_name = detect_junk_folder(account)
        if not folder_name:
            if log_cb:
                log_cb("[IMAP Junk] No Junk/Spam folder found on server.")
            return 0

    if log_cb:
        log_cb(f"[IMAP Junk] Connecting to {account.get('imap_host')}...")

    conn = _connect(account)
    new_count = 0
    try:
        # Read-only SELECT (uses EXAMINE so seen flags don't change on server)
        typ, data = conn.select(_quote(folder_name), readonly=True)
        if typ != "OK":
            if log_cb:
                log_cb(f"[IMAP Junk] Cannot open folder '{folder_name}': {data}")
            return 0

        # SEARCH ALL gets every UID currently in the folder
        typ, data = conn.uid("SEARCH", None, "ALL")
        if typ != "OK" or not data or not data[0]:
            if log_cb:
                log_cb(f"[IMAP Junk] Folder '{folder_name}' is empty.")
            return 0

        uids = data[0].split()
        if log_cb:
            log_cb(f"[IMAP Junk] '{folder_name}' has {len(uids)} message(s).")

        # Newest first, capped
        uids = list(reversed(uids))[:MAX_JUNK_FETCH]
        total = len(uids)

        for idx, raw_uid in enumerate(uids, start=1):
            if progress_cb:
                progress_cb(idx, total)

            uid = raw_uid.decode("ascii", errors="replace")
            local_uid = f"{IMAP_UID_PREFIX}{uid}"

            if database.email_exists(account["id"], LOCAL_FOLDER, local_uid):
                continue

            try:
                typ, fetched = conn.uid("FETCH", uid, "(RFC822)")
                if typ != "OK" or not fetched or fetched[0] is None:
                    continue
                # fetched[0] is a tuple ('UID 1234 (RFC822 {12345}', b'<raw bytes>')
                raw = b""
                for part in fetched:
                    if isinstance(part, tuple) and len(part) >= 2:
                        raw = part[1]
                        break
                if not raw:
                    continue
                parsed, attachments = parse_message(raw, uidl=local_uid)
                database.insert_email(account["id"], LOCAL_FOLDER, parsed, attachments)
                new_count += 1
            except Exception as e:
                if log_cb:
                    log_cb(f"[IMAP Junk] UID {uid} failed: {e}")
                continue

        if log_cb:
            log_cb(f"[IMAP Junk] Saved {new_count} new junk message(s).")
        return new_count
    finally:
        try:
            conn.close()
        except Exception:
            pass
        try:
            conn.logout()
        except Exception:
            pass


def _quote(name: str) -> str:
    """IMAP mailbox names with spaces or special chars must be quoted."""
    # imaplib expects bytes-or-str; quote if needed
    if " " in name or '"' in name or "/" in name:
        # Escape any embedded quotes
        safe = name.replace('"', '\\"')
        return f'"{safe}"'
    return name
