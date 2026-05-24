"""
MIME message parsing helpers.
"""
import email
from email import policy
from email.header import decode_header, make_header
from email.utils import parsedate_to_datetime
from datetime import datetime


def _decode_header(value):
    if not value:
        return ""
    try:
        return str(make_header(decode_header(value)))
    except Exception:
        return str(value)


def parse_message(raw_bytes: bytes, uidl: str = None) -> dict:
    """Parse raw MIME bytes into structured dict + attachments list."""
    msg = email.message_from_bytes(raw_bytes, policy=policy.default)

    parsed = {
        "uidl": uidl,
        "message_id": _decode_header(msg.get("Message-ID")),
        "in_reply_to": _decode_header(msg.get("In-Reply-To")),
        "references": _decode_header(msg.get("References")),
        "from": _decode_header(msg.get("From")),
        "to": _decode_header(msg.get("To")),
        "cc": _decode_header(msg.get("Cc")),
        "bcc": _decode_header(msg.get("Bcc")),
        "subject": _decode_header(msg.get("Subject")) or "(no subject)",
        "date_received": datetime.utcnow().isoformat(),
        "date_sent": None,
        "body_plain": "",
        "body_html": "",
        "raw_size": len(raw_bytes),
    }

    date_hdr = msg.get("Date")
    if date_hdr:
        try:
            parsed["date_sent"] = parsedate_to_datetime(date_hdr).isoformat()
            # Use sent date as received date for sorting consistency
            parsed["date_received"] = parsed["date_sent"]
        except Exception:
            pass

    attachments = []
    plain_parts, html_parts = [], []

    if msg.is_multipart():
        for part in msg.walk():
            ctype = part.get_content_type()
            disp = (part.get("Content-Disposition") or "").lower()

            if part.is_multipart():
                continue

            filename = part.get_filename()
            if filename:
                filename = _decode_header(filename)

            is_attachment = "attachment" in disp or (filename and ctype not in ("text/plain", "text/html"))

            if is_attachment:
                try:
                    data = part.get_payload(decode=True) or b""
                except Exception:
                    data = b""
                attachments.append({
                    "filename": filename or "attachment.bin",
                    "mime_type": ctype,
                    "size": len(data),
                    "data": data,
                })
            elif ctype == "text/plain":
                try:
                    plain_parts.append(part.get_content())
                except Exception:
                    payload = part.get_payload(decode=True)
                    if payload:
                        plain_parts.append(payload.decode(errors="replace"))
            elif ctype == "text/html":
                try:
                    html_parts.append(part.get_content())
                except Exception:
                    payload = part.get_payload(decode=True)
                    if payload:
                        html_parts.append(payload.decode(errors="replace"))
    else:
        ctype = msg.get_content_type()
        try:
            content = msg.get_content()
        except Exception:
            payload = msg.get_payload(decode=True)
            content = payload.decode(errors="replace") if payload else ""
        if ctype == "text/html":
            html_parts.append(content)
        else:
            plain_parts.append(content)

    parsed["body_plain"] = "\n".join(p for p in plain_parts if p).strip()
    parsed["body_html"] = "\n".join(p for p in html_parts if p).strip()

    return parsed, attachments
