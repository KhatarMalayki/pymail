"""
SQLite storage for accounts, emails, and attachments.
"""
import os
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime

from . import config


# DB_PATH is read at every connection so changes via Settings take effect
# on next launch (after restart). It can also be re-read by callers.
DB_PATH = str(config.get_db_path())


SCHEMA = """
CREATE TABLE IF NOT EXISTS accounts (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT NOT NULL,
    email           TEXT NOT NULL,
    pop3_host       TEXT,
    pop3_port       INTEGER DEFAULT 995,
    pop3_ssl        INTEGER DEFAULT 1,
    pop3_user       TEXT,
    pop3_password   TEXT,
    leave_on_server INTEGER DEFAULT 1,
    smtp_host       TEXT,
    smtp_port       INTEGER DEFAULT 465,
    smtp_security   TEXT DEFAULT 'SSL',
    smtp_user       TEXT,
    smtp_password   TEXT,
    signature       TEXT DEFAULT '',
    created_at      TEXT
);

CREATE TABLE IF NOT EXISTS emails (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id      INTEGER NOT NULL,
    folder          TEXT NOT NULL,
    uidl            TEXT,
    message_id      TEXT,
    sender          TEXT,
    recipients      TEXT,
    cc              TEXT,
    bcc             TEXT,
    subject         TEXT,
    date_received   TEXT,
    date_sent       TEXT,
    body_plain      TEXT,
    body_html       TEXT,
    is_read         INTEGER DEFAULT 0,
    has_attachments INTEGER DEFAULT 0,
    raw_size        INTEGER DEFAULT 0,
    UNIQUE(account_id, folder, uidl),
    FOREIGN KEY (account_id) REFERENCES accounts(id) ON DELETE CASCADE
);

-- Content-addressed attachment metadata. The actual bytes live on disk
-- in the attachment store, keyed by file_hash. ref_count tracks how many
-- email rows point at a blob so we know when to delete it.
CREATE TABLE IF NOT EXISTS attachment_blobs (
    file_hash   TEXT PRIMARY KEY,
    size        INTEGER NOT NULL,
    ref_count   INTEGER NOT NULL DEFAULT 0
);

-- Per-email attachment references. Multiple emails can share the same
-- file_hash → de-duplication.
CREATE TABLE IF NOT EXISTS attachments (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    email_id    INTEGER NOT NULL,
    filename    TEXT,
    mime_type   TEXT,
    size        INTEGER,
    file_hash   TEXT,            -- new: pointer into attachment_blobs
    data        BLOB,             -- legacy column (NULL after migration)
    FOREIGN KEY (email_id) REFERENCES emails(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS manual_contacts (
    email       TEXT PRIMARY KEY,
    name        TEXT,
    created_at  TEXT
);

-- Cached contact list (sender/recipient/cc) for instant compose autocomplete
-- on large mailboxes. Refreshed incrementally as emails are inserted.
CREATE TABLE IF NOT EXISTS contact_cache (
    email       TEXT PRIMARY KEY,
    name        TEXT,
    last_seen   TEXT
);
"""

# Indexes are created AFTER schema migrations have run, so legacy DBs
# (which add file_hash via ALTER TABLE in _migrate) have the column
# present before the index is created.
INDEXES = """
CREATE INDEX IF NOT EXISTS idx_emails_account_folder
    ON emails(account_id, folder, date_received DESC);
CREATE INDEX IF NOT EXISTS idx_attachments_email
    ON attachments(email_id);
CREATE INDEX IF NOT EXISTS idx_attachments_hash
    ON attachments(file_hash);
"""


# FTS5 virtual table created separately (uses CREATE VIRTUAL TABLE which
# can't be in a CREATE TABLE IF NOT EXISTS chain reliably across versions).
FTS_SCHEMA = """
CREATE VIRTUAL TABLE IF NOT EXISTS emails_fts USING fts5(
    subject, sender, body_plain,
    content=emails, content_rowid=id,
    tokenize='unicode61 remove_diacritics 2'
);

CREATE TRIGGER IF NOT EXISTS emails_ai AFTER INSERT ON emails BEGIN
    INSERT INTO emails_fts(rowid, subject, sender, body_plain)
    VALUES (new.id, new.subject, new.sender, new.body_plain);
END;
CREATE TRIGGER IF NOT EXISTS emails_ad AFTER DELETE ON emails BEGIN
    INSERT INTO emails_fts(emails_fts, rowid, subject, sender, body_plain)
    VALUES ('delete', old.id, old.subject, old.sender, old.body_plain);
END;
CREATE TRIGGER IF NOT EXISTS emails_au AFTER UPDATE ON emails BEGIN
    INSERT INTO emails_fts(emails_fts, rowid, subject, sender, body_plain)
    VALUES ('delete', old.id, old.subject, old.sender, old.body_plain);
    INSERT INTO emails_fts(rowid, subject, sender, body_plain)
    VALUES (new.id, new.subject, new.sender, new.body_plain);
END;
"""


def _migrate(conn):
    """Idempotent schema migrations (DDL only — fast). Data migrations
    that may take time (BLOB → filesystem) are handled lazily by
    run_pending_migrations()."""
    # accounts.signature
    cur = conn.execute("PRAGMA table_info(accounts)")
    cols = {row[1] for row in cur.fetchall()}
    if "signature" not in cols:
        conn.execute("ALTER TABLE accounts ADD COLUMN signature TEXT DEFAULT ''")

    # attachments.file_hash (legacy DBs only had `data` BLOB)
    cur = conn.execute("PRAGMA table_info(attachments)")
    att_cols = {row[1] for row in cur.fetchall()}
    if "file_hash" not in att_cols:
        conn.execute("ALTER TABLE attachments ADD COLUMN file_hash TEXT")


def _migrate_blobs_to_store(conn, batch_size: int = 25):
    """Move attachments.data → on-disk store. One batch per call so the
    UI doesn't freeze for minutes on a multi-GB DB."""
    from . import attachment_store
    rows = conn.execute(
        "SELECT id, data FROM attachments "
        "WHERE file_hash IS NULL AND data IS NOT NULL "
        f"LIMIT {batch_size}"
    ).fetchall()
    for row in rows:
        att_id = row["id"]
        data = row["data"]
        if not data:
            conn.execute("UPDATE attachments SET data=NULL WHERE id=?", (att_id,))
            continue
        try:
            file_hash, size = attachment_store.store(bytes(data))
        except Exception:
            continue
        conn.execute(
            "INSERT INTO attachment_blobs(file_hash, size, ref_count) "
            "VALUES (?, ?, 1) "
            "ON CONFLICT(file_hash) DO UPDATE SET ref_count = ref_count + 1",
            (file_hash, size),
        )
        conn.execute(
            "UPDATE attachments SET file_hash=?, size=?, data=NULL WHERE id=?",
            (file_hash, size, att_id),
        )


_lock = threading.Lock()


@contextmanager
def get_conn():
    """Thread-safe connection context manager."""
    with _lock:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()


def init_db():
    with get_conn() as conn:
        conn.executescript(SCHEMA)
        # Schema migrations FIRST so subsequent code can rely on the new
        # columns (file_hash, signature, etc.) being present.
        _migrate(conn)
        # Indexes AFTER migration so legacy DBs that just got `file_hash`
        # ALTERed in can have idx_attachments_hash created on it.
        conn.executescript(INDEXES)
        # FTS may fail on very old SQLite — gracefully degrade if so
        try:
            conn.executescript(FTS_SCHEMA)
            # Backfill FTS from existing rows on first creation
            row = conn.execute(
                "SELECT COUNT(*) FROM emails_fts"
            ).fetchone()
            existing_emails = conn.execute(
                "SELECT COUNT(*) FROM emails"
            ).fetchone()[0]
            if row[0] == 0 and existing_emails > 0:
                conn.execute(
                    "INSERT INTO emails_fts(rowid, subject, sender, body_plain) "
                    "SELECT id, subject, sender, body_plain FROM emails"
                )
        except Exception:
            pass  # FTS not available — search will fall back to LIKE


def run_pending_migrations(progress_cb=None) -> int:
    """Run one batch of legacy-blob migration. Call repeatedly until 0.
    Returns the number of attachments migrated this batch."""
    with get_conn() as conn:
        before = conn.execute(
            "SELECT COUNT(*) FROM attachments WHERE file_hash IS NULL "
            "AND data IS NOT NULL"
        ).fetchone()[0]
        if before == 0:
            return 0
        _migrate_blobs_to_store(conn, batch_size=25)
        after = conn.execute(
            "SELECT COUNT(*) FROM attachments WHERE file_hash IS NULL "
            "AND data IS NOT NULL"
        ).fetchone()[0]
        migrated = before - after
    if progress_cb:
        progress_cb(migrated, after)
    return migrated


def pending_migration_count() -> int:
    with get_conn() as conn:
        return conn.execute(
            "SELECT COUNT(*) FROM attachments WHERE file_hash IS NULL "
            "AND data IS NOT NULL"
        ).fetchone()[0]


def move_to_spam(email_id: int):
    with get_conn() as conn:
        conn.execute("UPDATE emails SET folder='spam' WHERE id=?", (email_id,))


def move_to_inbox(email_id: int):
    with get_conn() as conn:
        conn.execute("UPDATE emails SET folder='inbox' WHERE id=?", (email_id,))


# ---------- Manual contacts (CSV import target) ----------

def list_manual_contacts():
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT email, name FROM manual_contacts ORDER BY name, email"
        ).fetchall()
        return [dict(r) for r in rows]


def upsert_manual_contacts(items: list[tuple[str, str]]) -> int:
    """Insert or update (name, email) pairs. Returns count touched."""
    now = datetime.utcnow().isoformat()
    count = 0
    with get_conn() as conn:
        for name, email in items:
            email = (email or "").strip().lower()
            if not email:
                continue
            conn.execute(
                "INSERT INTO manual_contacts(email, name, created_at) "
                "VALUES (?, ?, ?) "
                "ON CONFLICT(email) DO UPDATE SET name=excluded.name",
                (email, (name or "").strip(), now),
            )
            count += 1
    return count


def delete_manual_contact(email: str):
    with get_conn() as conn:
        conn.execute("DELETE FROM manual_contacts WHERE email=?", (email.lower(),))


# ---------- Accounts ----------

def add_account(data: dict) -> int:
    cols = [
        "name", "email",
        "pop3_host", "pop3_port", "pop3_ssl", "pop3_user", "pop3_password",
        "leave_on_server",
        "smtp_host", "smtp_port", "smtp_security", "smtp_user", "smtp_password",
        "signature",
        "created_at",
    ]
    data = {**data, "created_at": datetime.utcnow().isoformat()}
    placeholders = ",".join(["?"] * len(cols))
    values = [data.get(c) for c in cols]

    with get_conn() as conn:
        cur = conn.execute(
            f"INSERT INTO accounts ({','.join(cols)}) VALUES ({placeholders})",
            values,
        )
        return cur.lastrowid


def update_account(account_id: int, data: dict):
    cols = [
        "name", "email",
        "pop3_host", "pop3_port", "pop3_ssl", "pop3_user", "pop3_password",
        "leave_on_server",
        "smtp_host", "smtp_port", "smtp_security", "smtp_user", "smtp_password",
        "signature",
    ]
    sets = ",".join([f"{c}=?" for c in cols])
    values = [data.get(c) for c in cols] + [account_id]
    with get_conn() as conn:
        conn.execute(f"UPDATE accounts SET {sets} WHERE id=?", values)


def delete_account(account_id: int):
    with get_conn() as conn:
        conn.execute("DELETE FROM accounts WHERE id=?", (account_id,))


def list_accounts():
    with get_conn() as conn:
        return [dict(r) for r in conn.execute("SELECT * FROM accounts ORDER BY id")]


def get_account(account_id: int):
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM accounts WHERE id=?", (account_id,)).fetchone()
        return dict(row) if row else None


# ---------- Emails ----------

def email_exists(account_id: int, folder: str, uidl: str) -> bool:
    if not uidl:
        return False
    with get_conn() as conn:
        row = conn.execute(
            "SELECT 1 FROM emails WHERE account_id=? AND folder=? AND uidl=?",
            (account_id, folder, uidl),
        ).fetchone()
        return row is not None


def insert_email(account_id: int, folder: str, parsed: dict, attachments: list) -> int:
    from . import attachment_store
    with get_conn() as conn:
        cur = conn.execute("""
            INSERT INTO emails(
                account_id, folder, uidl, message_id, sender, recipients,
                cc, bcc, subject, date_received, date_sent,
                body_plain, body_html, is_read, has_attachments, raw_size
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            account_id, folder,
            parsed.get("uidl"), parsed.get("message_id"),
            parsed.get("from"), parsed.get("to"),
            parsed.get("cc"), parsed.get("bcc"),
            parsed.get("subject"),
            parsed.get("date_received") or datetime.utcnow().isoformat(),
            parsed.get("date_sent"),
            parsed.get("body_plain"), parsed.get("body_html"),
            1 if folder == "sent" else 0,
            1 if attachments else 0,
            parsed.get("raw_size", 0),
        ))
        email_id = cur.lastrowid
        for a in attachments:
            data = a.get("data") or b""
            file_hash, size = attachment_store.store(bytes(data))
            if file_hash:
                conn.execute(
                    "INSERT INTO attachment_blobs(file_hash, size, ref_count) "
                    "VALUES (?, ?, 1) "
                    "ON CONFLICT(file_hash) DO UPDATE "
                    "  SET ref_count = ref_count + 1",
                    (file_hash, size),
                )
            conn.execute("""
                INSERT INTO attachments(
                    email_id, filename, mime_type, size, file_hash, data
                ) VALUES (?,?,?,?,?,NULL)
            """, (
                email_id,
                a["filename"],
                a["mime_type"],
                size,
                file_hash or None,
            ))
        # Refresh contact_cache for this email's senders/recipients
        _refresh_contact_cache_for(conn, parsed)
        return email_id


def _refresh_contact_cache_for(conn, parsed: dict):
    """Update contact_cache from a single parsed email's headers."""
    from email.utils import getaddresses
    now = datetime.utcnow().isoformat()
    for field in ("from", "to", "cc"):
        value = parsed.get(field) or ""
        if not value:
            continue
        try:
            pairs = getaddresses([value])
        except Exception:
            continue
        for name, email in pairs:
            email = (email or "").strip().lower()
            if not email or "@" not in email:
                continue
            name = (name or "").strip()
            conn.execute(
                "INSERT INTO contact_cache(email, name, last_seen) "
                "VALUES (?, ?, ?) "
                "ON CONFLICT(email) DO UPDATE SET "
                "  name = CASE WHEN excluded.name != '' THEN excluded.name "
                "              ELSE contact_cache.name END, "
                "  last_seen = excluded.last_seen",
                (email, name, now),
            )


def list_cached_contacts() -> list[dict]:
    """Fast O(1) read of contact list from cache (no scanning emails)."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT email, name FROM contact_cache "
            "ORDER BY name COLLATE NOCASE, email"
        ).fetchall()
        return [dict(r) for r in rows]


def rebuild_contact_cache():
    """Repopulate contact_cache from scratch by scanning all emails. Used
    after major data changes (import, restore from backup)."""
    from email.utils import getaddresses
    with get_conn() as conn:
        conn.execute("DELETE FROM contact_cache")
        rows = conn.execute(
            "SELECT date_received, sender, recipients, cc FROM emails"
        ).fetchall()
        for row in rows:
            parsed = {
                "from": row["sender"],
                "to": row["recipients"],
                "cc": row["cc"],
                "date_received": row["date_received"],
            }
            _refresh_contact_cache_for(conn, parsed)


def list_emails(account_id: int, folder: str, search: str = "",
                sort_field: str = "date_received", sort_desc: bool = True,
                limit: int | None = None, offset: int = 0):
    """List emails. When `search` is set, uses FTS5 if available for fast
    full-text search across subject/sender/body; falls back to LIKE.

    `limit` and `offset` enable pagination so giant folders open instantly.
    """
    valid_fields = {"date_received", "sender", "subject", "raw_size"}
    if sort_field not in valid_fields:
        sort_field = "date_received"
    direction = "DESC" if sort_desc else "ASC"

    with get_conn() as conn:
        if search:
            # Try FTS5 first for any non-trivial query
            fts_results = _fts_search(conn, search)
            if fts_results is not None:
                if not fts_results:
                    return []
                placeholders = ",".join("?" * len(fts_results))
                sql = (
                    "SELECT id, sender, recipients, subject, date_received, "
                    "is_read, has_attachments, raw_size, "
                    "substr(coalesce(body_plain,''),1,200) AS preview "
                    "FROM emails "
                    f"WHERE account_id=? AND folder=? AND id IN ({placeholders}) "
                    f"ORDER BY {sort_field} {direction}, id DESC"
                )
                params = [account_id, folder] + fts_results
                if limit is not None:
                    sql += " LIMIT ? OFFSET ?"
                    params.extend([limit, offset])
                return [dict(r) for r in conn.execute(sql, params)]

            # Fallback: LIKE scan
            sql = (
                "SELECT id, sender, recipients, subject, date_received, "
                "is_read, has_attachments, raw_size, "
                "substr(coalesce(body_plain,''),1,200) AS preview "
                "FROM emails "
                "WHERE account_id=? AND folder=? "
                "AND (subject LIKE ? OR sender LIKE ? OR body_plain LIKE ?) "
                f"ORDER BY {sort_field} {direction}, id DESC"
            )
            like = f"%{search}%"
            params = [account_id, folder, like, like, like]
            if limit is not None:
                sql += " LIMIT ? OFFSET ?"
                params.extend([limit, offset])
            return [dict(r) for r in conn.execute(sql, params)]

        # No search — straight pagination over the index
        sql = (
            "SELECT id, sender, recipients, subject, date_received, "
            "is_read, has_attachments, raw_size, "
            "substr(coalesce(body_plain,''),1,200) AS preview "
            "FROM emails "
            "WHERE account_id=? AND folder=? "
            f"ORDER BY {sort_field} {direction}, id DESC"
        )
        params = [account_id, folder]
        if limit is not None:
            sql += " LIMIT ? OFFSET ?"
            params.extend([limit, offset])
        return [dict(r) for r in conn.execute(sql, params)]


def _fts_search(conn, query: str) -> list[int] | None:
    """Run an FTS5 query, returning matching email ids. Returns None when
    FTS isn't available so callers can fall back to LIKE."""
    fts_query = _build_fts_query(query)
    if not fts_query:
        return None
    try:
        rows = conn.execute(
            "SELECT rowid FROM emails_fts WHERE emails_fts MATCH ?",
            (fts_query,),
        ).fetchall()
        return [r[0] for r in rows]
    except Exception:
        return None


def _build_fts_query(raw: str) -> str:
    """Convert a user search string into an FTS5 MATCH query.

    - Splits on whitespace
    - Each word becomes a prefix term ("budi*") so partial matches work
      while typing
    - Special chars are stripped to avoid syntax errors
    """
    import re
    cleaned = re.sub(r'[^\w\s]', ' ', raw, flags=re.UNICODE)
    words = [w for w in cleaned.split() if len(w) >= 2]
    if not words:
        return ""
    return " ".join(f"{w}*" for w in words)


def count_emails(account_id: int, folder: str, search: str = "") -> int:
    """Count without fetching rows — useful for pagination UI."""
    with get_conn() as conn:
        if search:
            fts_ids = _fts_search(conn, search)
            if fts_ids is not None:
                if not fts_ids:
                    return 0
                placeholders = ",".join("?" * len(fts_ids))
                row = conn.execute(
                    f"SELECT COUNT(*) FROM emails "
                    f"WHERE account_id=? AND folder=? AND id IN ({placeholders})",
                    [account_id, folder] + fts_ids,
                ).fetchone()
                return row[0]
            row = conn.execute(
                "SELECT COUNT(*) FROM emails WHERE account_id=? AND folder=? "
                "AND (subject LIKE ? OR sender LIKE ? OR body_plain LIKE ?)",
                (account_id, folder, f"%{search}%", f"%{search}%", f"%{search}%"),
            ).fetchone()
            return row[0]
        row = conn.execute(
            "SELECT COUNT(*) FROM emails WHERE account_id=? AND folder=?",
            (account_id, folder),
        ).fetchone()
        return row[0]


def get_email(email_id: int):
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM emails WHERE id=?", (email_id,)).fetchone()
        if not row:
            return None
        email = dict(row)
        atts = conn.execute(
            "SELECT id, filename, mime_type, size FROM attachments WHERE email_id=?",
            (email_id,),
        ).fetchall()
        email["attachments"] = [dict(a) for a in atts]
        return email


# ---------- Drafts ----------

def save_draft(account_id: int, draft_id: int | None, fields: dict) -> int:
    """Insert or update a draft. Returns the draft email_id."""
    from datetime import datetime
    now = datetime.utcnow().isoformat()
    with get_conn() as conn:
        if draft_id:
            row = conn.execute(
                "SELECT id FROM emails WHERE id=? AND folder='drafts'",
                (draft_id,),
            ).fetchone()
            if row:
                conn.execute("""
                    UPDATE emails SET
                        recipients=?, cc=?, bcc=?, subject=?,
                        body_plain=?, date_received=?
                    WHERE id=?
                """, (
                    fields.get("to") or "",
                    fields.get("cc") or "",
                    fields.get("bcc") or "",
                    fields.get("subject") or "",
                    fields.get("body") or "",
                    now,
                    draft_id,
                ))
                return draft_id

        cur = conn.execute("""
            INSERT INTO emails(
                account_id, folder, uidl, message_id, sender, recipients,
                cc, bcc, subject, date_received, date_sent,
                body_plain, body_html, is_read, has_attachments, raw_size
            ) VALUES (?, 'drafts', NULL, NULL, ?, ?, ?, ?, ?, ?, NULL, ?, NULL, 1, 0, 0)
        """, (
            account_id,
            fields.get("from") or "",
            fields.get("to") or "",
            fields.get("cc") or "",
            fields.get("bcc") or "",
            fields.get("subject") or "",
            now,
            fields.get("body") or "",
        ))
        return cur.lastrowid


def get_draft(draft_id: int):
    return get_email(draft_id)


def list_drafts_for_account(account_id: int):
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT id, recipients, subject, date_received, body_plain
            FROM emails WHERE account_id=? AND folder='drafts'
            ORDER BY date_received DESC
        """, (account_id,)).fetchall()
        return [dict(r) for r in rows]


def delete_draft(draft_id: int):
    with get_conn() as conn:
        conn.execute(
            "DELETE FROM emails WHERE id=? AND folder='drafts'",
            (draft_id,),
        )


# ---------- Outbox ----------

def queue_outbox(account_id: int, fields: dict, raw_bytes: bytes) -> int:
    """Insert an email into the outbox folder, ready to be sent.

    The raw MIME bytes are stored in body_html (re-purposed) so the worker
    can re-send the exact same wire bytes on retry. We also store the
    parsed fields for display in the email list.
    """
    import base64
    from datetime import datetime
    raw_b64 = base64.b64encode(raw_bytes).decode("ascii")
    with get_conn() as conn:
        cur = conn.execute("""
            INSERT INTO emails(
                account_id, folder, uidl, message_id, sender, recipients,
                cc, bcc, subject, date_received, date_sent,
                body_plain, body_html, is_read, has_attachments, raw_size
            ) VALUES (?, 'outbox', NULL, NULL, ?, ?, ?, ?, ?, ?, NULL, ?, ?, 1, 0, ?)
        """, (
            account_id,
            fields.get("from") or "",
            fields.get("to") or "",
            fields.get("cc") or "",
            fields.get("bcc") or "",
            fields.get("subject") or "(no subject)",
            datetime.utcnow().isoformat(),
            fields.get("body") or "",
            raw_b64,  # store raw MIME (base64) in body_html column for re-send
            len(raw_bytes),
        ))
        return cur.lastrowid


def list_outbox(account_id: int):
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT id, sender, recipients, subject, date_received,
                   body_plain, body_html, raw_size
            FROM emails WHERE account_id=? AND folder='outbox'
            ORDER BY date_received ASC
        """, (account_id,)).fetchall()
        return [dict(r) for r in rows]


def get_outbox_raw(email_id: int) -> bytes | None:
    """Get the raw MIME bytes of a queued outbox email."""
    import base64
    with get_conn() as conn:
        row = conn.execute(
            "SELECT body_html FROM emails WHERE id=? AND folder='outbox'",
            (email_id,),
        ).fetchone()
        if not row or not row["body_html"]:
            return None
        try:
            return base64.b64decode(row["body_html"])
        except Exception:
            return None


def move_outbox_to_sent(email_id: int, new_raw: bytes | None = None):
    """Mark an outbox email as sent. Clears the raw-MIME blob and re-stores
    the email cleanly under the 'sent' folder. Optionally accepts a fresh
    raw payload to re-parse (in case the original lacked Date header)."""
    from .mail_parser import parse_message
    import base64
    raw = new_raw
    if raw is None:
        with get_conn() as conn:
            row = conn.execute(
                "SELECT body_html FROM emails WHERE id=? AND folder='outbox'",
                (email_id,),
            ).fetchone()
            if row and row["body_html"]:
                try:
                    raw = base64.b64decode(row["body_html"])
                except Exception:
                    raw = None
    if raw:
        try:
            parsed, atts = parse_message(raw)
            with get_conn() as conn:
                acc = conn.execute(
                    "SELECT account_id FROM emails WHERE id=?", (email_id,)
                ).fetchone()
                if not acc:
                    return
            insert_email(acc["account_id"], "sent", parsed, atts)
        except Exception:
            pass
    # Remove the outbox row regardless of whether parse succeeded
    with get_conn() as conn:
        conn.execute(
            "DELETE FROM emails WHERE id=? AND folder='outbox'", (email_id,)
        )


def delete_outbox(email_id: int):
    with get_conn() as conn:
        conn.execute(
            "DELETE FROM emails WHERE id=? AND folder='outbox'", (email_id,)
        )


def get_attachment(attachment_id: int):
    from . import attachment_store
    with get_conn() as conn:
        row = conn.execute(
            "SELECT filename, mime_type, file_hash, data "
            "FROM attachments WHERE id=?",
            (attachment_id,),
        ).fetchone()
        if not row:
            return None
        d = dict(row)
        # Prefer disk-backed blob
        if d.get("file_hash"):
            data = attachment_store.load(d["file_hash"])
            d["data"] = data or b""
        # Legacy: blob still in DB
        elif d.get("data") is None:
            d["data"] = b""
        return d


def get_attachments_for_email(email_id: int):
    """Return all attachments of an email with full binary data (loaded
    from the content store as needed)."""
    from . import attachment_store
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT id, filename, mime_type, size, file_hash, data "
            "FROM attachments WHERE email_id=?",
            (email_id,),
        ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            if d.get("file_hash"):
                d["data"] = attachment_store.load(d["file_hash"]) or b""
            elif d.get("data") is None:
                d["data"] = b""
            out.append(d)
        return out


def mark_read(email_id: int, read: bool = True):
    with get_conn() as conn:
        conn.execute("UPDATE emails SET is_read=? WHERE id=?", (1 if read else 0, email_id))


def move_to_trash(email_id: int):
    with get_conn() as conn:
        conn.execute("UPDATE emails SET folder='trash' WHERE id=?", (email_id,))


def delete_email(email_id: int):
    """Delete an email and decrement ref counts for its attachments.
    Files in the content store are removed when ref_count hits 0."""
    from . import attachment_store
    with get_conn() as conn:
        # Find all attachment hashes referenced by this email
        hashes = [
            r["file_hash"]
            for r in conn.execute(
                "SELECT file_hash FROM attachments "
                "WHERE email_id=? AND file_hash IS NOT NULL",
                (email_id,),
            ).fetchall()
            if r["file_hash"]
        ]
        # Cascade delete the rows (FK ON DELETE CASCADE)
        conn.execute("DELETE FROM emails WHERE id=?", (email_id,))
        # Decrement refs and prune files when count reaches 0
        for h in hashes:
            row = conn.execute(
                "UPDATE attachment_blobs SET ref_count = ref_count - 1 "
                "WHERE file_hash=? RETURNING ref_count",
                (h,),
            ).fetchone()
            if row and row["ref_count"] <= 0:
                conn.execute(
                    "DELETE FROM attachment_blobs WHERE file_hash=?", (h,)
                )
                attachment_store.delete(h)


def folder_counts(account_id: int):
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT folder,
                   COUNT(*) AS total,
                   SUM(CASE WHEN is_read=0 THEN 1 ELSE 0 END) AS unread
            FROM emails WHERE account_id=? GROUP BY folder
        """, (account_id,)).fetchall()
        return {r["folder"]: {"total": r["total"], "unread": r["unread"] or 0} for r in rows}
