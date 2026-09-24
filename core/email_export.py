import re
from datetime import datetime
from email.message import EmailMessage
from email.utils import parseaddr
from pathlib import Path

from . import database

_last_export_dir = ""


def get_last_export_dir() -> str:
    global _last_export_dir
    return _last_export_dir or str(Path.home())


def set_last_export_dir(path: str):
    global _last_export_dir
    _last_export_dir = str(path or "")


def _safe_name(value: str) -> str:
    value = re.sub(r'[<>:"/\\|?*\x00-\x1f]', " ", value or "")
    return re.sub(r"\s+", " ", value).strip(" .") or "Unknown"


def _date_prefix(value: str) -> str:
    try:
        return datetime.fromisoformat(
            (value or "").replace("Z", "+00:00")
        ).strftime("%m-%Y")
    except (TypeError, ValueError):
        return datetime.now().strftime("%m-%Y")


def _fallback_message(row: dict) -> bytes:
    message = EmailMessage()
    for header, key in (
        ("From", "sender"), ("To", "recipients"), ("Cc", "cc"),
        ("Bcc", "bcc"), ("Subject", "subject"), ("Date", "date_sent"),
        ("Message-ID", "message_id"),
    ):
        if row.get(key):
            message[header] = row[key]
    message.set_content(row.get("body_plain") or "")
    if row.get("body_html"):
        message.add_alternative(row["body_html"], subtype="html")
    for attachment in database.get_attachments_for_email(row["id"]):
        mime_type = attachment.get("mime_type") or "application/octet-stream"
        main_type, _, sub_type = mime_type.partition("/")
        message.add_attachment(
            attachment.get("data") or b"",
            maintype=main_type or "application",
            subtype=sub_type or "octet-stream",
            filename=attachment.get("filename") or "attachment",
        )
    return message.as_bytes()


def export_emails(email_ids: list[int], destination: str) -> list[Path]:
    output_dir = Path(destination)
    output_dir.mkdir(parents=True, exist_ok=True)
    set_last_export_dir(str(output_dir))
    exported = []
    for email_id in email_ids:
        row = database.get_email(email_id)
        if not row:
            continue
        sender_name, sender_address = parseaddr(row.get("sender") or "")
        sender = sender_name or sender_address or "Unknown"
        date_val = row.get("date_received") or row.get("date_sent")
        base = f"{_date_prefix(date_val)}-{_safe_name(sender)}"
        path = output_dir / f"{base}.eml"
        counter = 2
        while path.exists():
            path = output_dir / f"{base}-{counter}.eml"
            counter += 1
        raw = row.get("raw_message")
        path.write_bytes(bytes(raw) if raw else _fallback_message(row))
        exported.append(path)
    return exported
