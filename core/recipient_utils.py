"""Utilities for parsing and composing RFC-style recipient lists."""

from email.utils import formataddr, getaddresses


def _normalize_semicolons(value: str) -> str:
    """Turn Outlook-style separators into RFC commas, except in quotes."""
    out = []
    quote = None
    escaped = False
    for char in value or "":
        if escaped:
            out.append(char)
            escaped = False
            continue
        if char == "\\" and quote:
            out.append(char)
            escaped = True
            continue
        if char == '"':
            if quote == '"':
                quote = None
            elif quote is None:
                quote = '"'
            out.append(char)
            continue
        out.append("," if char == ";" and quote is None else char)
    return "".join(out)


def parse_recipients(*values: str) -> list[tuple[str, str]]:
    """Return ``(display_name, address)`` pairs from recipient headers."""
    headers = [_normalize_semicolons(value) for value in values if value]
    # Python 3.12 defaults to strict parsing, where one non-standard mailbox
    # copied from Outlook can invalidate an otherwise usable entire header.
    # Compose fields are user input, so use the tolerant parser and let SMTP
    # report genuinely rejected addresses during the Outbox flush.
    try:
        parsed = getaddresses(headers, strict=False)
    except TypeError:  # Python < 3.12 has no ``strict`` keyword.
        parsed = getaddresses(headers)
    return [
        (name.strip(), address.strip().strip("'").strip())
        for name, address in parsed
        if address and address.strip().strip("'").strip()
    ]


def format_recipient(name: str, address: str) -> str:
    """Format a parsed mailbox while preserving its display name."""
    return formataddr((name, address)) if name else address


def build_reply_all_recipients(
    sender: str,
    original_to: str,
    original_cc: str,
    own_address: str,
) -> tuple[str, str]:
    """Build Outlook-style To/Cc fields for Reply All.

    The original sender and To recipients take precedence over Cc. The
    current account and duplicates are removed case-insensitively.
    """
    own_key = (own_address or "").strip().casefold()
    seen = {own_key} if own_key else set()

    def unique(values: list[tuple[str, str]]) -> list[str]:
        result = []
        for name, address in values:
            key = address.casefold()
            if key in seen:
                continue
            seen.add(key)
            result.append(format_recipient(name, address))
        return result

    to_values = unique(parse_recipients(sender, original_to))
    cc_values = unique(parse_recipients(original_cc))
    return ", ".join(to_values), ", ".join(cc_values)
