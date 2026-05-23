"""
POP3 receiver. Downloads new messages and stores them in the local DB.
"""
import poplib
import socket
from . import database
from .mail_parser import parse_message


# Some servers limit POP3 message size; raise default to 50 MB
poplib._MAXLINE = 100_000_000


class POP3Error(Exception):
    pass


def connect(account: dict) -> poplib.POP3:
    host = account["pop3_host"]
    port = int(account["pop3_port"] or (995 if account["pop3_ssl"] else 110))
    timeout = 30
    try:
        if account["pop3_ssl"]:
            conn = poplib.POP3_SSL(host, port, timeout=timeout)
        else:
            conn = poplib.POP3(host, port, timeout=timeout)
        conn.user(account["pop3_user"] or account["email"])
        conn.pass_(account["pop3_password"])
        return conn
    except (poplib.error_proto, socket.error, socket.timeout) as e:
        raise POP3Error(f"POP3 connection failed: {e}") from e


def test_connection(account: dict) -> tuple[bool, str]:
    try:
        conn = connect(account)
        try:
            count, size = conn.stat()
            return True, f"OK. Server has {count} message(s), {size} bytes."
        finally:
            try:
                conn.quit()
            except Exception:
                pass
    except Exception as e:
        return False, str(e)


def fetch_new(account: dict, progress_cb=None, log_cb=None) -> int:
    """Fetch new messages for an account. Returns number of new emails saved."""
    if log_cb:
        log_cb(f"Connecting to {account['pop3_host']}:{account['pop3_port']}...")

    conn = connect(account)
    new_count = 0
    try:
        # UIDL: list of (msg_num, unique_id) so we can dedupe
        resp, lines, _ = conn.uidl()
        uidls = []
        for line in lines:
            parts = line.decode(errors="replace").split()
            if len(parts) >= 2:
                uidls.append((int(parts[0]), parts[1]))

        total = len(uidls)
        if log_cb:
            log_cb(f"Server has {total} message(s).")

        leave_on_server = bool(account.get("leave_on_server", 1))

        for idx, (msg_num, uidl) in enumerate(uidls, start=1):
            if progress_cb:
                progress_cb(idx, total)

            if database.email_exists(account["id"], "inbox", uidl):
                if not leave_on_server:
                    conn.dele(msg_num)
                continue

            try:
                resp, msg_lines, octets = conn.retr(msg_num)
                raw = b"\r\n".join(msg_lines)
                parsed, attachments = parse_message(raw, uidl=uidl)
                database.insert_email(account["id"], "inbox", parsed, attachments)
                new_count += 1
                if log_cb:
                    log_cb(f"  + {parsed.get('subject', '(no subject)')[:60]}")

                if not leave_on_server:
                    conn.dele(msg_num)
            except Exception as e:
                if log_cb:
                    log_cb(f"  ! Failed message {msg_num}: {e}")
                continue

        return new_count
    finally:
        try:
            conn.quit()
        except Exception:
            pass
