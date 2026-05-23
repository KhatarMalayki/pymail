"""
Address book derived from past emails + a small extra "manual" contacts
table for entries the user adds explicitly (or imports from CSV).

Scans sender + recipient + cc fields across all stored emails for the
auto-suggest list. Manual contacts are merged on top.
"""
import csv
import io
from email.utils import getaddresses
from . import database


def extract_contacts() -> list[dict]:
    """Return list of {"name": str, "email": str}.

    Reads from the contact_cache table (incrementally maintained by
    database.insert_email) instead of scanning every email row — instant
    for mailboxes with hundreds of thousands of emails.

    Manual contacts are merged on top.
    """
    contacts: dict[str, dict] = {}
    try:
        for c in database.list_cached_contacts():
            email = (c.get("email") or "").strip().lower()
            if email:
                contacts[email] = {
                    "name": (c.get("name") or "").strip(),
                    "email": email,
                }
    except Exception:
        pass

    try:
        for c in database.list_manual_contacts():
            email = (c.get("email") or "").strip().lower()
            if not email:
                continue
            contacts[email] = {
                "name": (c.get("name") or "").strip(),
                "email": email,
            }
    except Exception:
        pass

    return list(contacts.values())


def contacts_for_completer() -> list[str]:
    """Return display strings suitable for a QCompleter list model."""
    items = []
    for c in extract_contacts():
        if c["name"]:
            items.append(f'{c["name"]} <{c["email"]}>')
        else:
            items.append(c["email"])
    return sorted(items, key=lambda s: (s.startswith("<"), s.lower()))


# ---------- CSV export / import ----------

CSV_HEADERS = ["Name", "Email"]


def export_csv(file_path: str) -> int:
    """Export all known contacts (both auto-extracted and manual) to CSV.

    Format: 2 columns "Name", "Email" — works with Outlook, Gmail, Apple
    Contacts, Thunderbird import wizards.
    Returns the number of rows written.
    """
    items = extract_contacts()
    items.sort(key=lambda c: ((c["name"] or "").lower(), c["email"]))
    with open(file_path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_HEADERS)
        writer.writeheader()
        for c in items:
            writer.writerow({
                "Name": c.get("name") or "",
                "Email": c.get("email") or "",
            })
    return len(items)


def import_csv(file_path: str) -> tuple[int, int]:
    """Import contacts from a CSV file. Returns (added, skipped).

    Recognized header schemas (case-insensitive, first match wins):
        - Name, Email                                   (PyMail / generic)
        - First Name, Last Name, E-mail Address         (Outlook export)
        - Name, Primary Email                           (Gmail / Apple)
        - Display Name, Email Address                   (CSV export some apps)
    """
    added = 0
    skipped = 0

    def _norm(s: str) -> str:
        return (s or "").strip().lower().replace("-", "").replace(" ", "")

    # Try utf-8-sig first (handles BOM), fall back to cp1252
    try:
        with open(file_path, "r", encoding="utf-8-sig") as f:
            content = f.read()
    except UnicodeDecodeError:
        with open(file_path, "r", encoding="cp1252") as f:
            content = f.read()

    reader = csv.DictReader(io.StringIO(content))
    if reader.fieldnames is None:
        return 0, 0

    norm_fields = {_norm(h): h for h in reader.fieldnames}

    def _pick(*candidates):
        for c in candidates:
            if c in norm_fields:
                return norm_fields[c]
        return None

    name_field = _pick("name", "displayname", "fullname")
    first_field = _pick("firstname")
    last_field = _pick("lastname", "surname")
    email_field = _pick(
        "email", "emailaddress", "primaryemail",
        "emailaddress1", "emailaddress2", "emailaddress3",
    )

    if not email_field:
        # Try harder: any column with "mail" in the name
        for k, v in norm_fields.items():
            if "mail" in k:
                email_field = v
                break
    if not email_field:
        raise ValueError(
            "CSV doesn't have a recognizable Email column. "
            "Expected one of: Email, E-mail Address, Primary Email."
        )

    rows_to_save = []
    for row in reader:
        email = (row.get(email_field) or "").strip().lower()
        if not email or "@" not in email:
            skipped += 1
            continue

        if name_field:
            name = (row.get(name_field) or "").strip()
        elif first_field or last_field:
            first = (row.get(first_field) if first_field else "") or ""
            last = (row.get(last_field) if last_field else "") or ""
            name = (first.strip() + " " + last.strip()).strip()
        else:
            name = ""

        rows_to_save.append((name, email))

    if rows_to_save:
        added = database.upsert_manual_contacts(rows_to_save)
    return added, skipped
