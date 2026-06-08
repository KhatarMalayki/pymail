"""
SMTP sender. Builds MIME messages and dispatches them.
"""
import smtplib
import socket
import ssl
import os
import re
import base64
import mimetypes
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from email.message import EmailMessage
from email.utils import formatdate, make_msgid, parseaddr


_DATA_URI_RE = re.compile(
    r'(<img\b[^>]*\bsrc\s*=\s*[\'"])'  # group 1: <img ... src="
    r'data:image/([^;\'"]+);base64,'   # group 2: subtype (png/jpeg/gif)
    r'([A-Za-z0-9+/=\s]+?)'            # group 3: base64 data (lazy)
    r'([\'"])',                         # group 4: closing quote
    re.IGNORECASE,
)

# External http(s) <img src> references. At send time we download these and
# inline them as data: URIs so they later become Content-ID parts. This is the
# safety net for signatures (e.g. the Tunas template) whose images failed to
# embed at insert time — on some machines a corporate proxy/firewall blocks
# raw.githubusercontent.com, GitHub rate-limits, or the user was offline when
# inserting the template. Without this, external URLs reach the recipient
# untouched and clients like Outlook (which block remote images by default)
# show broken-image icons.
_EXTERNAL_IMG_RE = re.compile(
    r'(<img\b[^>]*\bsrc\s*=\s*[\'"])'  # group 1: <img ... src="
    r'(https?://[^\'"]+)'              # group 2: the external URL
    r'([\'"])',                         # group 3: closing quote
    re.IGNORECASE,
)

_IMG_FETCH_TIMEOUT = 8          # seconds per image
_IMG_MAX_BYTES = 5 * 1024 * 1024  # 5 MB cap per image
_IMG_MAX_COUNT = 20             # don't download more than this per message
_IMG_USER_AGENT = "Mozilla/5.0 (RunLabMail; signature send)"


def _fetch_external_image(url: str) -> tuple[str, bytes | None, str]:
    """Download a single image. Returns (url, bytes_or_None, mime_subtype)."""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": _IMG_USER_AGENT})
        with urllib.request.urlopen(req, timeout=_IMG_FETCH_TIMEOUT) as resp:
            ctype = resp.headers.get("Content-Type", "image/png").split(";")[0].strip()
            data = resp.read(_IMG_MAX_BYTES + 1)
            if len(data) > _IMG_MAX_BYTES:
                return url, None, "png"
        subtype = ctype.split("/", 1)[1] if "/" in ctype else "png"
        return url, data, subtype.lower()
    except Exception:
        return url, None, "png"


def _embed_external_images(html: str) -> str:
    """Replace external http(s) <img src> with inline data: URIs.

    Runs in the send path so that any signature image that wasn't embedded at
    insert time still reaches the recipient inline. Images that can't be
    downloaded are left as their original URL (best effort — no worse than
    before). The returned HTML's new data: URIs are converted to Content-ID
    parts downstream by _data_uris_to_cid().
    """
    urls = []
    seen = set()
    for m in _EXTERNAL_IMG_RE.finditer(html):
        u = m.group(2)
        if u in seen:
            continue
        seen.add(u)
        urls.append(u)
    if not urls:
        return html

    urls = urls[:_IMG_MAX_COUNT]
    results: dict[str, tuple[bytes | None, str]] = {}
    with ThreadPoolExecutor(max_workers=4) as ex:
        futures = [ex.submit(_fetch_external_image, u) for u in urls]
        for fut in as_completed(futures):
            url, data, subtype = fut.result()
            results[url] = (data, subtype)

    def _replace(match: re.Match) -> str:
        prefix, url, suffix = match.groups()
        data, subtype = results.get(url, (None, "png"))
        if not data:
            return match.group(0)  # leave external URL as-is (best effort)
        b64 = base64.b64encode(data).decode("ascii")
        return f'{prefix}data:image/{subtype};base64,{b64}{suffix}'

    return _EXTERNAL_IMG_RE.sub(_replace, html)



def _data_uris_to_cid(html: str) -> tuple[str, list[tuple[str, str, bytes]]]:
    """Replace data:image base64 URIs in HTML with cid: references and
    return the corresponding image payloads ready to be attached as
    related parts.

    Why: Gmail and Outlook render `data:` URIs inconsistently. Some clients
    show them inline, some show them as separate "broken image" placeholders
    or even strip them. The robust cross-client approach is to use proper
    Content-ID references with the image as a related multipart attachment
    (this is how Outlook and Apple Mail send signatures with logos).

    Returns:
        (rewritten_html, [(cid_no_brackets, mime_subtype, raw_bytes), ...])
    """
    images: list[tuple[str, str, bytes]] = []
    # IMPORTANT: compute the Content-ID domain ONCE. make_msgid() with no
    # domain calls socket.getfqdn(), which can do a ~5s blocking DNS reverse
    # lookup on Windows. Calling it once per image (signatures often have
    # several logos) froze the UI for ~30s. A fixed local domain is fine —
    # Content-IDs only need to be unique within the message, not resolvable.
    cid_domain = "runlabmail.local"

    def _replace(match: re.Match) -> str:
        prefix, subtype, b64data, suffix = match.groups()
        try:
            data = base64.b64decode(b64data, validate=False)
        except Exception:
            return match.group(0)  # leave as-is
        if not data:
            return match.group(0)
        cid = make_msgid(domain=cid_domain)[1:-1]  # strip the < >
        images.append((cid, subtype.lower(), data))
        return f'{prefix}cid:{cid}{suffix}'

    new_html = _DATA_URI_RE.sub(_replace, html)
    return new_html, images


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
        # Safety net: download any remaining external http(s) images and
        # inline them as data: URIs first. This catches signature images
        # (e.g. the Tunas template) that failed to embed at insert time
        # because a proxy/firewall blocked the host, GitHub rate-limited,
        # or the user was offline. Without this, external URLs reach the
        # recipient untouched and clients like Outlook show broken icons.
        body_html = _embed_external_images(body_html)
        # Convert any data:image base64 URIs to proper Content-ID
        # multipart/related parts. Without this, Gmail/Outlook often
        # render data: URIs as ugly broken-attachment placeholders or
        # separate attachments at the bottom of the message.
        body_html_cid, related_images = _data_uris_to_cid(body_html)
        if related_images:
            msg.add_alternative(body_html_cid, subtype="html")
            html_part = msg.get_payload()[-1]
            for cid, mime_subtype, img_bytes in related_images:
                html_part.add_related(
                    img_bytes,
                    maintype="image",
                    subtype=mime_subtype,
                    cid=cid,
                )
        else:
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
    """Send the email and return the raw bytes of the sent message.

    Uses explicit envelope recipients (To + Cc + Bcc) and sendmail()
    so that BCC recipients receive the email and partial failures are
    reported per-recipient instead of silently swallowed.

    Returns the raw bytes of the sent message (Bcc header stripped).
    Raises SMTPError with details of any refused recipients.
    """
    from email.utils import getaddresses

    # Collect ALL envelope recipients (To + Cc + Bcc)
    all_recipient_headers = []
    for hdr in ("To", "Cc", "Bcc"):
        vals = msg.get_all(hdr, [])
        all_recipient_headers.extend(vals)
    all_recipients = [
        addr for _name, addr in getaddresses(all_recipient_headers) if addr
    ]

    if not all_recipients:
        raise SMTPError("No recipients specified")

    # Determine sender envelope address
    from_header = msg.get("From", "")
    _from_name, from_addr = getaddresses([from_header])[0] if from_header else ("", "")
    if not from_addr:
        from_addr = account.get("email", "")

    # Build a copy WITHOUT the Bcc header for the wire (standard practice)
    import copy
    msg_copy = copy.copy(msg)
    if msg_copy["Bcc"]:
        del msg_copy["Bcc"]

    server = _connect(account)
    refused = {}
    try:
        refused = server.sendmail(from_addr, all_recipients, msg_copy.as_bytes())
    except smtplib.SMTPRecipientsRefused as e:
        raise SMTPError(
            "All recipients refused:\n"
            + "\n".join(f"  {addr}: {code} {errmsg.decode(errors='replace')}"
                        for addr, (code, errmsg) in e.recipients.items())
        ) from e
    except smtplib.SMTPSenderRefused as e:
        raise SMTPError(f"Sender refused: {e.smtp_error.decode(errors='replace')}") from e
    except smtplib.SMTPDataError as e:
        raise SMTPError(f"Data error: {e.smtp_error.decode(errors='replace')}") from e
    except smtplib.SMTPException as e:
        raise SMTPError(f"Send failed: {e}") from e
    finally:
        try:
            server.quit()
        except Exception:
            pass

    # If some (but not all) recipients were refused, raise with details
    if refused:
        details = "\n".join(
            f"  {addr}: {code} {errmsg.decode(errors='replace')}"
            for addr, (code, errmsg) in refused.items()
        )
        raise SMTPError(
            f"Email sent to {len(all_recipients) - len(refused)} recipient(s), "
            f"but {len(refused)} recipient(s) were refused:\n{details}"
        )

    return msg_copy.as_bytes()
