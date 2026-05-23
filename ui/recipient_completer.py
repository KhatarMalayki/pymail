"""
Autocomplete for comma-separated recipient fields (To, Cc, Bcc).

The default QCompleter completes the entire field text, which is wrong when
the user types multiple recipients separated by commas. We do two things:

    1. Override splitPath() so the completer matches only the substring
       AFTER the last comma — what the user is currently typing.
    2. Hook into the activated signal manually to insert the chosen
       completion at the right spot, replacing only the last segment and
       appending ", " for the next entry.

We deliberately DO NOT override pathFromIndex() because that would cause
double-insertion when the user clicks a suggestion (Qt fires both the
internal pathFromIndex flow AND the activated signal in some cases).
"""
from PyQt5.QtCore import Qt, QStringListModel
from PyQt5.QtWidgets import QCompleter, QLineEdit


class _RecipientCompleter(QCompleter):
    def __init__(self, items: list[str], parent=None):
        super().__init__(items, parent)
        self.setCaseSensitivity(Qt.CaseInsensitive)
        self.setFilterMode(Qt.MatchContains)
        self.setCompletionMode(QCompleter.PopupCompletion)

    def splitPath(self, path: str) -> list[str]:
        """Filter only on the substring after the last comma."""
        last_comma = path.rfind(",")
        if last_comma == -1:
            return [path.strip()]
        return [path[last_comma + 1:].strip()]


def attach_to(line_edit: QLineEdit, items: list[str]) -> _RecipientCompleter:
    """Attach a smart multi-recipient completer to a QLineEdit.

    Returns the completer so the caller can update its model later.
    """
    completer = _RecipientCompleter(items, line_edit)
    line_edit.setCompleter(completer)

    def _on_activated(text: str):
        # Replace just the segment after the last comma with the chosen text,
        # then append ", " so the user can keep typing the next address.
        current = line_edit.text()
        last_comma = current.rfind(",")
        if last_comma == -1:
            new_text = text + ", "
        else:
            new_text = current[: last_comma + 1] + " " + text + ", "
        # Block signals briefly so this programmatic edit doesn't re-trigger
        # the completer popup mid-insertion.
        line_edit.blockSignals(True)
        line_edit.setText(new_text)
        line_edit.blockSignals(False)
        line_edit.setCursorPosition(len(new_text))

    # `activated[str]` is the safe overload that always fires once per pick.
    completer.activated[str].connect(_on_activated)
    return completer


def update_items(completer: _RecipientCompleter, items: list[str]) -> None:
    """Replace the completer's items in place. Call after sending email
    so newly contacted addresses appear next time."""
    completer.setModel(QStringListModel(items, completer))
