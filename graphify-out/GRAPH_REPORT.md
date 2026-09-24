# Graph Report - email_client_app  (2026-09-24)

## Corpus Check
- 78 files · ~72,225 words
- Verdict: corpus is large enough that graph structure adds value.

## Summary
- 1465 nodes · 2855 edges · 84 communities (70 shown, 14 thin omitted)
- Extraction: 92% EXTRACTED · 8% INFERRED · 0% AMBIGUOUS · INFERRED: 240 edges (avg confidence: 0.59)
- Token cost: 0 input · 0 output

## Graph Freshness
- Built from commit: `bac823a2`
- Run `git rev-parse HEAD` and compare to check if the graph is stale.
- Run `graphify update .` after code changes (no API cost).

## Community Hubs (Navigation)
- Community 0
- Community 1
- Community 2
- Community 3
- Community 4
- Community 5
- Community 6
- Community 7
- Community 8
- Community 9
- Community 10
- Community 11
- Community 12
- Community 13
- Community 14
- Community 15
- Community 16
- Community 17
- Community 18
- Community 19
- Community 20
- Community 21
- Community 22
- Community 23
- Community 24
- Community 25
- Community 26
- Community 27
- Community 28
- Community 29
- Community 30
- Community 31
- Community 32
- Community 33
- Community 34
- Community 35
- Community 36
- Community 37
- Community 38
- Community 39
- Community 40
- Community 41
- Community 42
- Community 43
- Community 44
- Community 45
- Community 46
- Community 47
- Community 48
- Community 49
- account_dialog.py
- Community 51
- Community 52
- build_message
- Community 54
- Community 55
- CategoryManagerDialog
- Community 57
- Community 58
- _exec_with_fts_repair
- QPixmap
- EmailWindow
- Community 62
- Community 63
- Community 64
- Community 65
- Community 66
- Community 67
- Community 68
- Community 69
- init_db
- AccountsListDialog
- ._tree_menu
- RibbonToolbar
- Community 76
- Community 77
- Community 78
- Community 79
- Community 80
- Community 81
- JunkFetchWorker
- Project Map — RunLab Mail (`email_client_app`)
- Release RunLab Mail (PyMail)

## God Nodes (most connected - your core abstractions)
1. `MainWindow` - 146 edges
2. `ComposeDialog` - 86 edges
3. `LicenseManagerDialog` - 55 edges
4. `get_conn()` - 48 edges
5. `AccountDialog` - 43 edges
6. `EmailView` - 31 edges
7. `EmailListWidget` - 28 edges
8. `OutboxFlushWorker` - 25 edges
9. `SyncProgressDialog` - 25 edges
10. `AboutDialog` - 24 edges

## Surprising Connections (you probably didn't know these)
- `LicenseActivationDialog` --uses--> `LicenseError`  [INFERRED]
  ui/license_dialog.py → core/license.py
- `PasswordStorageTests` --uses--> `AccountDialog`  [INFERRED]
  tests/test_password_storage.py → ui/account_dialog.py
- `PasteScreenshotTests` --uses--> `ComposeDialog`  [INFERRED]
  tests/test_paste_screenshot.py → ui/compose_dialog.py
- `JunkUnreadCountTests` --uses--> `MainWindow`  [INFERRED]
  tests/testjunkcounts.py → ui/main_window.py
- `set_mode()` --calls--> `set_value()`  [EXTRACTED]
  ui/theme.py → core/config.py

## Import Cycles
- None detected.

## Communities (84 total, 14 thin omitted)

### Community 0 - "Community 0"
Cohesion: 0.09
Nodes (10): QListWidget, CategoryManagerDialog, QDialog, Manage per-account local color categories., QDialog, Outlook-style Send/Receive progress dialog.  Shows: - Per-account task list w, Register a new sync task (e.g. one per account)., Append a line to the log with a timestamp. (+2 more)

### Community 1 - "Community 1"
Cohesion: 0.06
Nodes (39): _canonical(), days_until_expiry(), fetch_blacklist(), force_refresh_blacklist(), get_machine_id(), get_machine_id_candidates(), _hash_machine_identity(), is_licensed() (+31 more)

### Community 2 - "Community 2"
Cohesion: 0.06
Nodes (56): add_account(), create_category(), delete_account(), delete_category(), delete_draft(), delete_manual_contact(), delete_outbox(), emails_all_have_category() (+48 more)

### Community 3 - "Community 3"
Cohesion: 0.11
Nodes (11): RunLab Mail (codename PyMail) — version & remote URLs.  Bump __version__ on ev, QTextEdit, Non-intrusive notification banner shown at the top of the window.  Appears bel, Main window with 3-panel Outlook-style layout: [ Folders / Accounts ] [ Email l, # NOTE: this only RECEIVES mail. It deliberately does NOT flush the, _DownloadWorker, QDialog, QThread (+3 more)

### Community 4 - "Community 4"
Cohesion: 0.05
Nodes (11): MainWindow, QMainWindow, Manual: triggered by user pressing toolbar Send/Receive on Outbox., Record a per-email send failure so we can show a bounce log., Hit /verify on the Worker to detect admin actions (extend,         revoke) with, Decide whether to start the BLOB → filesystem migration., Called when another RunLab Mail launch is attempted. Surface this window., Manual route to dialog (kept for fallback / future use). (+3 more)

### Community 5 - "Community 5"
Cohesion: 0.07
Nodes (55): check_for_updates(), check_now(), download(), _fetch_manifest(), _friendly_network_error(), _get_allowed_version(), get_app_dir(), get_app_path() (+47 more)

### Community 6 - "Community 6"
Cohesion: 0.17
Nodes (10): build_reply_all_recipients(), format_recipient(), _normalize_semicolons(), parse_recipients(), Utilities for parsing and composing RFC-style recipient lists., Return ``(display_name, address)`` pairs from recipient headers., Format a parsed mailbox while preserving its display name., Build Outlook-style To/Cc fields for Reply All.      The original sender and To (+2 more)

### Community 7 - "Community 7"
Cohesion: 0.05
Nodes (30): create_desktop_shortcut(), is_in_cloud_folder(), Path, r""" Detect when RunLab Mail is running from a cloud-synced folder (OneDrive,, Create a Desktop shortcut pointing at the safely-located .exe.      Tries the, Return (True, name) if path is inside a known cloud-sync folder., Return the recommended local-only install directory., Copy the current .exe to target_exe, then spawn it and exit.      Uses a small (+22 more)

### Community 8 - "Community 8"
Cohesion: 0.12
Nodes (31): admin_delete(), admin_extend(), admin_generate(), admin_headers(), admin_import(), admin_list_users(), admin_push_version(), admin_push_version_single() (+23 more)

### Community 9 - "Community 9"
Cohesion: 0.09
Nodes (17): LicenseError, Exception, NotificationBanner, QFrame, Show the banner with the given message.          Args:             text: Mess, LicenseInfoDialog, License activation dialog. Shown when no valid license exists.  Also includes, Read-only dialog showing the current license info, with an option to     enter/ (+9 more)

### Community 10 - "Community 10"
Cohesion: 0.18
Nodes (6): _PrintPreviewDialog, QDialog, Self-contained print dialog with a Qt-rendered preview.      We render the pre, Printable area (width, height) in logical px (96 dpi) for the         current p, Return body HTML with each <img> sized according to the chosen         image-si, Apply the chosen page margins to the printer (in millimetres).

### Community 11 - "Community 11"
Cohesion: 0.24
Nodes (29): base64Decode(), base64Encode(), canonicalJson(), checkAdmin(), clientPublicIp(), cors(), fetch(), handleAdminDelete() (+21 more)

### Community 12 - "Community 12"
Cohesion: 0.10
Nodes (5): ComposeDialog, Replace the signature region in Qt-serialized HTML with the         ORIGINAL si, Place the caret on the first empty line and focus the editor., Return the QTextTable the caret is currently inside, or None., Insert a marker token as an invisible inline run.          We insert it via HT

### Community 13 - "Community 13"
Cohesion: 0.10
Nodes (11): EmailListWidget, QListWidgetItem, Themed email list, ready for the delegate above., Add a row from an emails-table dict.          Thread metadata (optional) is re, Update the flag indicator for the row with this email_id., Return every email id represented by a row. For a collapsed thread         head, Clear stored thread state before (re)populating the list., Register the child messages (everything below the head) for a         thread so (+3 more)

### Community 14 - "Community 14"
Cohesion: 0.14
Nodes (17): Outlook/eM Client-style email list.  Each row shows:   [Avatar] Sender, _make_avatar_pixmap(), Right-side email viewer widget: header info + body + attachments., Render a circular avatar with initials. Returns transparent QPixmap., apply_theme(), avatar_color_for(), build_qss(), color() (+9 more)

### Community 15 - "Community 15"
Cohesion: 0.17
Nodes (3): LicenseManagerDialog, Context menu shown when multiple rows are selected. Actions apply         to ev, Run `fn(license_id)` across all license_ids on a background thread         and

### Community 16 - "Community 16"
Cohesion: 0.17
Nodes (4): If the given local emails came from IMAP (Junk folder), also         delete the, Remove a stuck/failed email from the Outbox (user-confirmed)., Reopen a queued/stuck Outbox email in the compose dialog so the         user ca, Open the email in a popup window (Outlook-style detail view).

### Community 18 - "Community 18"
Cohesion: 0.14
Nodes (8): QColor, QFont, QPainter, QStyledItemDelegate, EmailItemDelegate, QModelIndex, Single-row layout: sender (fixed width) | subject (fills) | 📎 date., Switch the list density (compact/cozy/comfortable) live.

### Community 19 - "Community 19"
Cohesion: 0.13
Nodes (4): Adjust body height to fit content so container scroll handles all scrolling., Sync each recipient field's completer state_cache to the field's         curren, Add an attachment dict (from database.get_attachments_for_email)         direct, Build an Outlook-style ribbon toolbar with tabs and groups.

### Community 20 - "Community 20"
Cohesion: 0.13
Nodes (21): _append_style(), _clipboard_has_editable_html(), _clipboard_image_to_data_uri(), _constrain_image_widths(), _embed_images_in_html(), _fetch_image(), _inline_table_class_styles(), _looks_like_excel_html() (+13 more)

### Community 21 - "Community 21"
Cohesion: 0.13
Nodes (7): QTabWidget, AccountDialog, QDialog, Account add/edit dialog with POP3 + SMTP test buttons., Return True if a QTextBlock has any non-default formatting., Insert a Tunas signature through one reusable details form., Accounts list dialog — shown from the toolbar "Accounts" button.  Lists every

### Community 22 - "Community 22"
Cohesion: 0.20
Nodes (3): _LinkFreeHTMLParser, Serialize HTML while removing links and unsafe external URL targets., HTMLParser

### Community 23 - "Community 23"
Cohesion: 0.12
Nodes (11): _BulkWorker, _ExtendWorker, _iso_to_ts(), _ListWorker, QThread, Admin License Manager dialog — talks to the Cloudflare Worker.  Lists every re, Apply the same admin action to many license_ids sequentially.      `fn(license, Turn a version string like '1.2.59' into a tuple of ints for correct     numeri (+3 more)

### Community 24 - "Community 24"
Cohesion: 0.24
Nodes (9): _date_prefix(), export_emails(), _fallback_message(), get_last_export_dir(), Path, _safe_name(), set_last_export_dir(), EmailMessage (+1 more)

### Community 25 - "Community 25"
Cohesion: 0.14
Nodes (6): AboutDialog, _CheckWorker, _DownloadWorker, QDialog, QThread, About / Version dialog with manual update check.

### Community 26 - "Community 26"
Cohesion: 0.07
Nodes (35): _collect_folders(), detect_windows_live_mail(), import_eml_file(), import_eml_folder(), import_mbox(), import_pst_via_outlook(), import_vcf(), import_windows_live_mail() (+27 more)

### Community 27 - "Community 27"
Cohesion: 0.15
Nodes (8): EmailView, QListWidgetItem, Write the attachment to a temp file and open it with the OS default         app, Re-apply theme-dependent styling after a light/dark switch., The body had remote images we didn't load → offer to show them., EmailWindow, QMainWindow, Detached email reading window — opens when the user double-clicks an email in t

### Community 28 - "Community 28"
Cohesion: 0.20
Nodes (13): _connect(), _embed_external_images(), _fetch_external_image(), Exception, SMTP sender. Builds MIME messages and dispatches them., Download a single image. Returns (url, bytes_or_None, mime_subtype)., Replace external http(s) <img src> with inline data: URIs.      Runs in the send, # IMPORTANT: compute the Content-ID domain ONCE. make_msgid() with no (+5 more)

### Community 29 - "Community 29"
Cohesion: 0.13
Nodes (4): Trigger a background IMAP Junk fetch if the account has IMAP         enabled. N, Toolbar 'Accounts' button: list view of all accounts., Display a detailed log of failed Outbox sends (bounce log).          errors: l, Kick off any update that was deferred while an account dialog was         open.

### Community 30 - "Community 30"
Cohesion: 0.16
Nodes (6): _iso_to_local(), Convert ISO timestamp (UTC) to local time string., Return users filtered by search text., Recalculate total pages based on current page size and data., Detect locally-issued licenses missing from the Worker and         offer to imp, Sort by the clicked column. Clicking the same column again toggles         asce

### Community 31 - "Community 31"
Cohesion: 0.15
Nodes (13): contacts_for_completer(), export_csv(), extract_contacts(), import_csv(), Address book derived from past emails + a small extra "manual" contacts table f, Return list of {"name": str, "email": str}.      Reads from the contact_cache, Return display strings suitable for a QCompleter list model., Export all known contacts (both auto-extracted and manual) to CSV.      Format (+5 more)

### Community 32 - "Community 32"
Cohesion: 0.09
Nodes (31): _connect(), detect_junk_folder(), fetch_junk(), IMAPError, list_folders(), purge_junk_on_server(), Exception, _quote() (+23 more)

### Community 33 - "Community 33"
Cohesion: 0.17
Nodes (7): Belt-and-braces: force any run whose text contains a marker token         to be, Return a short plain-text excerpt from the signature for         idempotency ch, Called when the From account changes. Swap signature accordingly., Strip baked-in background colors from a signature.          Outlook/webmail si, Insert the signature into a fresh compose body.          Layout:, Insert signature at the TOP of the body, before the quoted         block. This, Return signature HTML with invisible marker tokens embedded INLINE         insi

### Community 34 - "Community 34"
Cohesion: 0.26
Nodes (13): list_issued(), main(), _pull_from_r2(), _push_to_r2(), Revoke / restore licenses. Updates revoked.json in your R2 bucket.  Usage:, Return the wrangler command path. On Windows, .cmd shims need to be     invoked, Load the most recent state from local cache, or default., Fetch current revoked.json from R2 (most authoritative). (+5 more)

### Community 36 - "Community 36"
Cohesion: 0.12
Nodes (13): QCompleter, QThread, Compose Email dialog (To/Cc/Bcc, subject, body, attachments)., # NOTE: these are bare tokens (no angle brackets). Qt's toHtml() escapes, SendWorker, attach_to(), QModelIndex, Autocomplete for comma-separated recipient fields (To, Cc, Bcc).  The default (+5 more)

### Community 37 - "Community 37"
Cohesion: 0.15
Nodes (4): Register a compose dialog so it can be auto-saved before app         restarts f, Build the quoted body for a reply or forward.          Outlook-style separator, Open Drafts only from an actual left mouse press., Reply/Reply-All/Forward triggered from a detached EmailWindow.         kind = '

### Community 38 - "Community 38"
Cohesion: 0.13
Nodes (10): QTextBrowser, _ImageFetchWorker, NetworkAwareTextBrowser, QThread, Resize the widget to fit the full document so the outer scroll         area can, Re-render the current message WITH remote images (the user clicked         'Sho, Return the set of http/https image URLs referenced in the HTML.          Done, Downloads remote images for an email body off the UI thread.      Emits `fetch (+2 more)

### Community 39 - "Community 39"
Cohesion: 0.29
Nodes (3): QWidget, (Re)build the stylesheet from the active theme tokens. Safe to call         aga, ViewBar

### Community 40 - "Community 40"
Cohesion: 0.20
Nodes (3): QLineEdit, _GenerateLicenseDialog, Collects the fields needed to generate a new license.

### Community 41 - "Community 41"
Cohesion: 0.27
Nodes (9): email_exists(), connect(), fetch_new(), POP3Error, Exception, POP3 receiver. Downloads new messages and stores them in the local DB., Fetch new messages for an account.      Args:         account: Account dict f, test_connection() (+1 more)

### Community 43 - "Community 43"
Cohesion: 0.29
Nodes (7): insert_email(), move_outbox_to_sent(), Mark an outbox email as sent. Clears the raw-MIME blob and re-stores     the em, Update contact_cache from a single parsed email's headers., Repopulate contact_cache from scratch by scanning all emails. Used     after ma, rebuild_contact_cache(), _refresh_contact_cache_for()

### Community 44 - "Community 44"
Cohesion: 0.27
Nodes (6): QImage, QMimeData, PasteScreenshotTests, PasteAwareTextEdit, QTextEdit that preserves clipboard screenshots and external images., Resize any image in the current document that's wider than         max_width. U

### Community 45 - "Community 45"
Cohesion: 0.07
Nodes (44): _bootstrap_base(), change_data_dir(), get(), get_data_dir(), get_db_path(), _load(), _migrate_legacy_db_filename(), Path (+36 more)

### Community 47 - "Community 47"
Cohesion: 0.14
Nodes (22): delete(), exists(), hash_bytes(), load(), path_for_hash(), Path, Content-addressed attachment storage.  Files are stored on disk under ~/.runla, Returns the per-user attachment storage folder, creating it on demand. (+14 more)

### Community 48 - "Community 48"
Cohesion: 0.21
Nodes (3): True if the body has any non-default formatting or images., Persist current state as a draft. Returns True if anything saved., Called by MainWindow before app shuts down (e.g. for update).         Always pe

### Community 49 - "Community 49"
Cohesion: 0.31
Nodes (3): _LicenseKeyDialog, QDialog, Shows a generated/re-issued license key with a copy button.

### Community 51 - "Community 51"
Cohesion: 0.22
Nodes (3): Run a Send/Receive cycle for the given accounts with a visible         progress, Outbox flush during a Send/Receive cycle.          Tries to send queued mail., If we don't have a valid license yet, register this machine         with the Wo

### Community 52 - "Community 52"
Cohesion: 0.22
Nodes (4): Called with the Worker's view of our license., Re-register with the Worker so it returns a fresh signed         license with t, A re-fetched license came back from the Worker — update our         in-memory p, If this is a trial license and expiry is within 7 days, show a         persiste

### Community 53 - "build_message"
Cohesion: 0.24
Nodes (6): build_message(), Return HTML with anchors and external URL-bearing attributes removed., Remove web URLs from the text MIME alternative.      Email addresses are intenti, _strip_hyperlinks(), _strip_plaintext_urls(), SMTPLinkSanitizerTests

### Community 54 - "Community 54"
Cohesion: 0.39
Nodes (7): delete_version(), main(), Manage R2 retention for PyMail binaries.  Cloudflare R2 has a built-in lifecyc, Show lifecycle rules currently configured on the bucket., Manually delete a specific PyMail-X.Y.Z.exe from R2., status(), _wrangler_cmd()

### Community 55 - "Community 55"
Cohesion: 0.25
Nodes (8): _build_fts_query(), count_emails(), _fts_search(), list_emails(), List emails. When `search` is set, uses FTS5 if available for fast     full-tex, Run an FTS5 query, returning matching email ids. Returns None when     FTS isn', Convert a user search string into an FTS5 MATCH query.      - Splits on whites, Count without fetching rows — useful for pagination UI.

### Community 56 - "CategoryManagerDialog"
Cohesion: 0.17
Nodes (3): Force-save all open compose windows. Returns count saved., Save any open compose drafts before exiting., Save drafts, then trigger the install. Single point of entry.

### Community 57 - "Community 57"
Cohesion: 0.10
Nodes (15): QFrame, QWidget, Ribbon-style toolbar widget (Outlook-like) for PyQt5.  Usage:     ribbon = Ribbo, Add vertical separator., A single tab in the ribbon containing multiple groups., Add a new group to this tab., Add expanding spacer at the end., Main ribbon toolbar widget. (+7 more)

### Community 58 - "Community 58"
Cohesion: 0.25
Nodes (4): QPixmap, QUrl, Open the system print dialog (with preview) for the current email.          We, Return {img_src: (width_px, height_px)} for every <img> in the         body. Ha

### Community 60 - "QPixmap"
Cohesion: 0.29
Nodes (4): _clean_table_html(), Alice <a@x.com>' -> ('Alice', 'a@x.com'). Plain email -> ('', email)., Set whether remote images are blocked for the NEXT render., Strip rigid widths and nowrap from tables so they wrap and fit the view/page.

### Community 63 - "Community 63"
Cohesion: 0.25
Nodes (7): main, name, private, scripts, deploy, dev, version

### Community 64 - "Community 64"
Cohesion: 0.43
Nodes (6): _canonical(), _load_private(), _log_issued(), main(), Ed25519PrivateKey, Bulk-issue licenses from a CSV file.  Usage:     python admin/bulk_issue.py u

### Community 65 - "Community 65"
Cohesion: 0.43
Nodes (6): _canonical(), _load_private(), _log_issued(), main(), Ed25519PrivateKey, Issue a license key for a user.  Usage:     python admin/issue_license.py --n

### Community 66 - "Community 66"
Cohesion: 0.38
Nodes (5): _delete_key(), main(), _parse_ver(), Prune old RunLab Mail release zips from the R2 bucket.  WHY: each release is ~, Returns (key, deleted_bool). Treats 'not found' as not-deleted.

### Community 67 - "Community 67"
Cohesion: 0.48
Nodes (6): main(), Path, Build RunLab Mail executable package (technical filename: PyMail.exe) with PyIn, read_version(), sha256_of(), zip_dir()

### Community 68 - "Community 68"
Cohesion: 0.29
Nodes (6): delete_email(), list_old_junk(), Delete an email and decrement ref counts for its attachments.     Files in the, Return Junk emails older than the given ISO date for a given account.     Each, _purge_old_junk(), Auto-delete junk older than `junk_purge_days` for the given account.      If I

### Community 69 - "Community 69"
Cohesion: 0.14
Nodes (13): 1. Install Wrangler (already done if you've been releasing PyMail), 2. Convert your private key to a Cloudflare secret, 3. Generate and set the admin token, 4. Deploy, 5. Configure PyMail to talk to it, Admin (require `X-Admin-Token` header), Cost, Deploy a new version (+5 more)

### Community 70 - "init_db"
Cohesion: 0.40
Nodes (3): QDialog, Set the border width and color of the table under the caret., Insert a table at the caret as a native QTextTable so it can be         edited

### Community 73 - "RibbonToolbar"
Cohesion: 0.11
Nodes (13): Built-in signature templates for known organizations.  Templates are simplifie, Render the self-contained Tunas Logistic signature template., Render the Tunas template with user-specific fields filled in.      Icons are, render_tunas(), render_tunas_logistic(), _data_uris_to_cid(), Replace data:image base64 URIs in HTML with cid: references and     return the, Auto-generated by _gen_tunas_icons.py. Do not edit by hand.  Tunas signature i (+5 more)

### Community 76 - "Community 76"
Cohesion: 0.67
Nodes (3): fetch(), main(), One-shot generator: download Tunas signature icons and emit a Python module (cor

### Community 77 - "Community 77"
Cohesion: 0.20
Nodes (9): Architecture, Critical files (DO NOT COMMIT), License management, Local development, Prerequisites, Project layout, Releasing a new version, RunLab Mail (+1 more)

### Community 84 - "JunkFetchWorker"
Cohesion: 0.24
Nodes (3): LicenseCheckWorker, QDialog, SettingsDialog

### Community 85 - "Project Map — RunLab Mail (`email_client_app`)"
Cohesion: 0.22
Nodes (8): External boundaries, High-coupling nodes, Main feature flows, Module ownership, Project Map — RunLab Mail (`email_client_app`), Runtime layers, System context, Tests currently visible

### Community 87 - "Release RunLab Mail (PyMail)"
Cohesion: 0.25
Nodes (7): Cara release, Flag berguna, ⚠️ Jangan pakai `python` default, Prasyarat, Release RunLab Mail (PyMail), Troubleshooting, Yang terjadi otomatis

## Knowledge Gaps
- **36 isolated node(s):** `name`, `version`, `private`, `main`, `deploy` (+31 more)
  These have ≤1 connection - possible missing edges or undocumented components.
- **14 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `MainWindow` connect `Community 4` to `Community 0`, `Community 3`, `Community 7`, `Community 9`, `Community 12`, `Community 13`, `Community 15`, `Community 16`, `Community 17`, `Community 21`, `Community 24`, `Community 25`, `Community 27`, `Community 29`, `Community 37`, `Community 39`, `Community 51`, `Community 52`, `CategoryManagerDialog`, `Community 57`, `_exec_with_fts_repair`, `Community 62`, `._tree_menu`, `Community 78`, `Community 79`, `JunkFetchWorker`?**
  _High betweenness centrality (0.175) - this node is a cross-community bridge._
- **Why does `ComposeDialog` connect `Community 12` to `Community 33`, `Community 35`, `Community 36`, `Community 3`, `init_db`, `Community 6`, `Community 4`, `Community 9`, `Community 37`, `Community 44`, `Community 48`, `Community 16`, `Community 18`, `Community 19`, `JunkFetchWorker`, `Community 57`?**
  _High betweenness centrality (0.140) - this node is a cross-community bridge._
- **Why does `AccountDialog` connect `Community 21` to `Community 35`, `Community 3`, `Community 4`, `RibbonToolbar`, `Community 9`, `Community 44`, `account_dialog.py`, `JunkFetchWorker`, `_exec_with_fts_repair`, `Community 29`?**
  _High betweenness centrality (0.059) - this node is a cross-community bridge._
- **Are the 22 inferred relationships involving `MainWindow` (e.g. with `JunkUnreadCountTests` and `AboutDialog`) actually correct?**
  _`MainWindow` has 22 INFERRED edges - model-reasoned connections that need verification._
- **Are the 12 inferred relationships involving `ComposeDialog` (e.g. with `PasteScreenshotTests` and `PasteAwareTextEdit`) actually correct?**
  _`ComposeDialog` has 12 INFERRED edges - model-reasoned connections that need verification._
- **Are the 8 inferred relationships involving `LicenseManagerDialog` (e.g. with `FetchWorker` and `JunkFetchWorker`) actually correct?**
  _`LicenseManagerDialog` has 8 INFERRED edges - model-reasoned connections that need verification._
- **Are the 12 inferred relationships involving `AccountDialog` (e.g. with `PasswordStorageTests` and `PasteAwareTextEdit`) actually correct?**
  _`AccountDialog` has 12 INFERRED edges - model-reasoned connections that need verification._