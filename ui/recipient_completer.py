"""
Autocomplete for comma-separated recipient fields (To, Cc, Bcc).

The default QCompleter completes the entire field text, which is wrong when
the user types multiple recipients separated by commas. We do two things:

    1. Override splitPath() so the completer matches only the substring
       AFTER the last comma — what the user is currently typing.
    2. Hook into the activated signal manually to insert the chosen
       completion at the right spot, replacing only the last segment and
       appending ", " for the next entry.

We override pathFromIndex() so Qt itself handles inserting the properly
concatenated string. To avoid double-insertion (which happens if pathFromIndex
reads the widget's text while QCompleter is natively modifying it during
popup navigation), we read the base text from a cache that only updates on
actual user typing (`textEdited`).
"""
from PyQt5.QtCore import Qt, QStringListModel, QModelIndex
from PyQt5.QtWidgets import QCompleter, QLineEdit


class _RecipientCompleter(QCompleter):
    def __init__(self, items: list[str], parent=None):
        super().__init__(items, parent)
        self.setCaseSensitivity(Qt.CaseInsensitive)
        self.setFilterMode(Qt.MatchContains)
        self.setCompletionMode(QCompleter.PopupCompletion)
        self.state_cache = {"user_text": ""}

    def splitPath(self, path: str) -> list[str]:
        """Filter only on the substring after the last comma."""
        last_comma = path.rfind(",")
        if last_comma == -1:
            return [path.strip()]
        return [path[last_comma + 1:].strip()]

    def pathFromIndex(self, index: QModelIndex) -> str:
        completion = super().pathFromIndex(index)
        current = self.state_cache.get("user_text", "")
        last_comma = current.rfind(",")
        if last_comma == -1:
            return completion + ", "
        else:
            return current[: last_comma + 1] + " " + completion + ", "


def attach_to(line_edit: QLineEdit, items: list[str]) -> _RecipientCompleter:
    """Attach a smart multi-recipient completer to a QLineEdit.

    Returns the completer so the caller can update its model later.
    """
    completer = _RecipientCompleter(items, line_edit)
    line_edit.setCompleter(completer)

    # Initialize cache with current text (e.g. if pre-filled on reply)
    completer.state_cache["user_text"] = line_edit.text()

    def _on_text_edited(text: str):
        completer.state_cache["user_text"] = text

    line_edit.textEdited.connect(_on_text_edited)

    def _on_activated(text: str):
        # text here is the FULL string returned by pathFromIndex
        completer.state_cache["user_text"] = text
        # QCompleter's native insertion leaves the cursor at the end, but
        # we explicitly set it just to be safe.
        line_edit.setCursorPosition(len(text))

    completer.activated[str].connect(_on_activated)
    return completer


def update_items(completer: _RecipientCompleter, items: list[str]) -> None:
    """Replace the completer's items in place. Call after sending email
    so newly contacted addresses appear next time."""
    completer.setModel(QStringListModel(items, completer))
