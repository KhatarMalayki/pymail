"""
SMTP sender. Builds MIME messages and dispatches them.
"""
import smtplib
import socket
import ssl
import os
import mimetypes
from email.message import EmailMessage
from email.utils import formatdate, make_msgid, parseaddr


class SMTPError(Exception):
    pass


def _connect(account: dict) -> smtplib.SMTP:
    host = account["smtp_host"]
    port = int(account["smtp_port"] or 465)
    security = (account.get("smtp_security") or "SSL").upper()
    timeout = 30
    context = ssl.create_default_context()
    where = f"{host}:{port} ({security})"

    try:
        if security == "SSL":
            server = smtplib.SMTP_SSL(host, port, timeout=timeout, context=context)
        else:
            server = smtplib.SMTP(host, port, timeout=timeout)
            server.ehlo()
            if security == "STARTTLS":
                server.starttls(context=context)
                server.ehlo()
        if account.get("smtp_user") and account.get("smtp_password"):
            server.login(account["smtp_user"], account["smtp_password"])
        return server
    except (smtplib.SMTPException, socket.error, socket.timeout, ssl.SSLError) as e:
        raise SMTPError(f"SMTP connection to {where} failed: {e}") from e


def test_connection(account: dict) -> tuple[bool, str]:
    try:
        server = _connect(account)
        try:
            return True, "SMTP login successful."
        finally:
            try:
                server.quit()
            except Exception:
                pass
    except Exception as e:
        return False, str(e)


def build_message(
    from_addr: str,
    to_list: list,
    cc_list: list,
    bcc_list: list,
    subject: str,
    body_text: str,
    body_html: str = None,
    attachments: list = None,
) -> EmailMessage:
    msg = EmailMessage()
    msg["From"] = from_addr
    msg["To"] = ", ".join(to_list)
    if cc_list:
        msg["Cc"] = ", ".join(cc_list)
    if bcc_list:
        msg["Bcc"] = ", ".join(bcc_list)
    msg["Subject"] = subject
    msg["Date"] = formatdate(localtime=True)
    domain = parseaddr(from_addr)[1].split("@")[-1] or "localhost"
    msg["Message-ID"] = make_msgid(domain=domain)

    msg.set_content(body_text or "")
    if body_html:
        msg.add_alternative(body_html, subtype="html")

    for att in attachments or []:
        # Two formats supported:
        #   - str path to a file on disk (user-picked attachment)
        #   - dict with keys: filename, mime_type, data (bytes) — used when
        #     forwarding an email and re-attaching the original payload
        if isinstance(att, dict):
            data = att.get("data")
            if not data:
                continue
            ctype = att.get("mime_type") or "application/octet-stream"
            try:
                maintype, subtype = ctype.split("/", 1)
            except ValueError:
                maintype, subtype = "application", "octet-stream"
            filename = att.get("filename") or "attachment.bin"
            msg.add_attachment(
                bytes(data),
                maintype=maintype,
                subtype=subtype,
                filename=filename,
            )
            continue

        path = att
        if not os.path.isfile(path):
            continue
        ctype, encoding = mimetypes.guess_type(path)
        if ctype is None or encoding is not None:
            ctype = "application/octet-stream"
        maintype, subtype = ctype.split("/", 1)
        with open(path, "rb") as f:
            data = f.read()
        msg.add_attachment(
            data,
            maintype=maintype,
            subtype=subtype,
            filename=os.path.basename(path),
        )
    return msg


def send_email(account: dict, msg: EmailMessage) -> bytes:
    """Send the email and return the raw bytes of the sent message."""
    server = _connect(account)
    try:
        server.send_message(msg)
    except smtplib.SMTPException as e:
        raise SMTPError(f"Send failed: {e}") from e
    finally:
        try:
            server.quit()
        except Exception:
            pass
    return msg.as_bytes()
