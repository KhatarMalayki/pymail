"""
MIME message parsing helpers.
"""
import email
import mimetypes
import os
from email import policy
from email.header import decode_header, make_header
from email.utils import parsedate_to_datetime
from datetime import datetime, timezone

_TNEF_MIME_TYPES = {
    "application/ms-tnef",
    "application/vnd.ms-tnef",
}
_TNEF_FILENAMES = {"winmail.dat", "win.dat"}
_MAX_TNEF_ATTACHMENTS = 100
_MAX_TNEF_EXTRACTED_BYTES = 100 * 1024 * 1024


def _decode_header(value):
    if not value:
        return ""
    try:
        return str(make_header(decode_header(value)))
    except Exception:
        return str(value)


def _is_tnef_attachment(filename: str | None, mime_type: str) -> bool:
    """Return True for Microsoft TNEF containers such as winmail.dat."""
    safe_name = os.path.basename((filename or "").replace("\\", "/")).lower()
    return (
        (mime_type or "").lower() in _TNEF_MIME_TYPES
        or safe_name in _TNEF_FILENAMES
    )


def _safe_attachment_name(value: str | None, index: int) -> str:
    """Discard paths/control characters supplied by an embedded attachment."""
    name = os.path.basename(str(value or "").replace("\\", "/")).strip()
    name = "".join(ch for ch in name if ch >= " " and ch != "\x7f")
    return name or f"tnef-attachment-{index}.bin"


def _extract_tnef_attachments(data: bytes) -> list[dict]:
    """Decode attachments encapsulated in a Microsoft TNEF payload.

    Any import/parsing/limit failure returns an empty list. The caller then
    retains the original winmail.dat, so malformed or unsupported messages
    never cause user data to disappear.
    """
    if not data:
        return []
    try:
        from tnefparse import TNEF

        decoded = TNEF(data)
    except Exception:
        return []

    extracted = []
    total_size = 0
    for index, item in enumerate(decoded.attachments[:_MAX_TNEF_ATTACHMENTS], 1):
        try:
            payload = bytes(item.data or b"")
            filename = _safe_attachment_name(
                item.long_filename() or item.name, index
            )
        except Exception:
            continue
        total_size += len(payload)
        if total_size > _MAX_TNEF_EXTRACTED_BYTES:
            return []
        mime_type = (
            mimetypes.guess_type(filename)[0] or "application/octet-stream"
        )
        extracted.append({
            "filename": filename,
            "mime_type": mime_type,
            "size": len(payload),
            "data": payload,
        })
    return extracted


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
        "date_received": datetime.now(timezone.utc).isoformat(),
        "date_sent": None,
        "body_plain": "",
        "body_html": "",
        "raw_size": len(raw_bytes),
    }

    date_hdr = msg.get("Date")
    if date_hdr:
        try:
            dt = parsedate_to_datetime(date_hdr)
            # Normalize to UTC so ISO strings sort chronologically as text.
            # Without this, messages with different timezone offsets (e.g.
            # +07:00 vs +00:00) sort by wall-clock text, not real instant —
            # so a 16:00 mail could appear below a 13:00 one. Naive datetimes
            # (no tz in the header) are assumed to already be UTC.
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            dt = dt.astimezone(timezone.utc)
            parsed["date_sent"] = dt.isoformat()
            # Use sent date as received date for sorting consistency
            parsed["date_received"] = parsed["date_sent"]
        except Exception:
            pass

    attachments = []
    plain_parts, html_parts = [], []
    # Inline images keyed by Content-ID, e.g. {"abc123@example": (mime, bytes)}
    # These get inlined into the HTML body as data: URIs so they render
    # without needing a separate attachment store lookup.
    inline_images: dict[str, tuple[str, bytes]] = {}

    if msg.is_multipart():
        for part in msg.walk():
            ctype = part.get_content_type()
            disp_raw = part.get("Content-Disposition") or ""
            disp = disp_raw.lower()

            if part.is_multipart():
                continue

            filename = part.get_filename()
            if filename:
                filename = _decode_header(filename)

            # Detect inline images (Gmail/Outlook signatures, embedded
            # banners, CAUTION-prefix logos from corporate gateways, etc.)
            cid_raw = part.get("Content-ID") or ""
            cid = cid_raw.strip().strip("<>").strip()
            is_inline_disp = "inline" in disp
            is_image = ctype.startswith("image/")

            if is_image and (cid or is_inline_disp):
                # Inline image — capture for HTML inlining, do NOT add as
                # a user-visible attachment.
                try:
                    data = part.get_payload(decode=True) or b""
                except Exception:
                    data = b""
                if data:
                    if cid:
                        inline_images[cid] = (ctype, data)
                    # Also key by filename so HTML referencing
                    # "src=image001.jpg" (Outlook style) resolves.
                    if filename:
                        inline_images[filename.lower()] = (ctype, data)
                continue

            # Treat as attachment if explicitly disposed that way OR if it
            # has a filename and is not part of the body.
            is_attachment = (
                "attachment" in disp
                or (filename and ctype not in ("text/plain", "text/html"))
            )

            if is_attachment:
                try:
                    data = part.get_payload(decode=True) or b""
                except Exception:
                    data = b""
                if _is_tnef_attachment(filename, ctype):
                    decoded_attachments = _extract_tnef_attachments(data)
                    if decoded_attachments:
                        # Outlook hides the TNEF wrapper and shows its contents.
                        # Match that behavior only after successful extraction.
                        attachments.extend(decoded_attachments)
                    else:
                        attachments.append({
                            "filename": filename or "winmail.dat",
                            "mime_type": ctype,
                            "size": len(data),
                            "data": data,
                        })
                else:
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

    # Resolve inline image references in the HTML body. Both Outlook-style
    # cid: refs ("cid:image001.jpg@01D...") and bare filename refs
    # ("src=image001.jpg") are mapped back to their bytes and emitted as
    # data: URIs so the message renders self-contained.
    if parsed["body_html"] and inline_images:
        parsed["body_html"] = _inline_cid_images(parsed["body_html"], inline_images)

    return parsed, attachments


def _inline_cid_images(html: str, inline_images: dict) -> str:
    """Rewrite <img src="cid:xxx"> (and bare filename matches) to embedded
    data: URIs. inline_images is {key: (mime_type, raw_bytes)} where key
    is either the Content-ID (no brackets) or the filename (lowercased)."""
    import re
    import base64

    def _build_data_uri(mime: str, data: bytes) -> str:
        b64 = base64.b64encode(data).decode("ascii")
        return f"data:{mime};base64,{b64}"

    def _replace(match: re.Match) -> str:
        prefix, src, suffix = match.group(1), match.group(2), match.group(3)
        original = match.group(0)
        # cid: form
        if src.lower().startswith("cid:"):
            cid = src[4:].strip().strip("<>").strip()
            entry = inline_images.get(cid)
            if not entry:
                # Some senders quote the cid with @ portion missing — try
                # a permissive lookup by partial match.
                for key, val in inline_images.items():
                    if key.startswith(cid) or cid.startswith(key):
                        entry = val
                        break
            if entry:
                mime, data = entry
                return f"{prefix}{_build_data_uri(mime, data)}{suffix}"
            return original

        # Bare filename form (Outlook sometimes emits src="image001.jpg"
        # for attached inline images instead of a cid:)
        bare = src.split("/")[-1].split("?")[0].lower().strip()
        entry = inline_images.get(bare)
        if entry:
            mime, data = entry
            return f"{prefix}{_build_data_uri(mime, data)}{suffix}"
        return original

    pattern = re.compile(
        r'(<img\b[^>]*\bsrc\s*=\s*[\'"])([^\'"]+)([\'"])',
        re.IGNORECASE,
    )
    return pattern.sub(_replace, html)
