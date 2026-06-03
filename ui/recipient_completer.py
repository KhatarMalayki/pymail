"""
Autocomplete for comma-separated recipient fields (To, Cc, Bcc).

The default QCompleter completes the entire field text, which is wrong when
the user types multiple recipients separated by commas — picking a suggestion
would wipe out every recipient typed before the last comma, so the field
could only ever end up with a single address. We fix this in two places:

    1. Override splitPath() so the completer matches only the substring
       AFTER the last comma — what the user is currently typing.
    2. Override pathFromIndex() so the text QLineEdit writes back keeps
       everything before the last comma intact, replaces only the segment
       being typed with the chosen address, and appends ", " so the user
       can keep adding recipients.

QLineEdit sets its text to completer.pathFromIndex(index) when a suggestion
is selected, so reconstructing the full field there is what actually makes
multi-recipient autocomplete work. We do NOT also hook the activated signal,
which would double-insert the chosen address.
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

    def pathFromIndex(self, index) -> str:
        """Build the text QLineEdit writes back when a suggestion is picked.

        Keep everything before the last comma, swap the segment currently
        being typed for the chosen address, and append ", " for the next one.
        """
        completion = super().pathFromIndex(index)
        widget = self.widget()
        if not isinstance(widget, QLineEdit):
            return completion
        current = widget.text()
        suffix = completion + ", "
        # Qt calls pathFromIndex more than once per selection; on the later
        # call(s) the chosen address is already the trailing segment, so
        # splicing again would duplicate it. Stay idempotent.
        if current.endswith(suffix):
            return current
        last_comma = current.rfind(",")
        if last_comma == -1:
            return suffix
        return current[: last_comma + 1] + " " + suffix


def attach_to(line_edit: QLineEdit, items: list[str]) -> _RecipientCompleter:
    """Attach a smart multi-recipient completer to a QLineEdit.

    Returns the completer so the caller can update its model later.
    """
    completer = _RecipientCompleter(items, line_edit)
    line_edit.setCompleter(completer)
    return completer


def update_items(completer: _RecipientCompleter, items: list[str]) -> None:
    """Replace the completer's items in place. Call after sending email
    so newly contacted addresses appear next time."""
    completer.setModel(QStringListModel(items, completer))
