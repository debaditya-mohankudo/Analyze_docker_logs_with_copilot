"""Shared Textual building blocks, vendored from SeniorDevAgent's tui/widgets.py
(the house TUI style — see task:7323b8ef, epic:d28f09dc "tui design").

Vendored rather than imported cross-repo: this repo and SeniorDevAgent are
separate packages with no shared dependency today, and find_bug_ollama.py /
tui/app.py already established the pattern of each consumer holding its own
copy rather than a shared install. Keep this file's contents identical to
SeniorDevAgent's tui/widgets.py when updating either.
"""
from typing import Any

from rich.markup import escape as escape_markup
from textual.widget import Widget
from textual.widgets import RichLog

# Sentinel a picker screen dismisses with when a REQUIRED field's escape
# can't just mean "cancel back to nothing" — the caller falls back to
# manual text entry instead.
FALLBACK_TO_TEXT = object()


def bordered(widget: Widget, title: str) -> Widget:
    """Set `widget.border_title` and return it, so every bordered picker/
    content box in every screen gets its purpose labeled the same way,
    inline in a `yield` or `with` statement instead of a separate
    query_one(...).border_title = ... follow-up line."""
    widget.border_title = title
    return widget


def step_prefix(index: int, total: int) -> str:
    """"[2/6] " style progress counter for a multi-step intake flow."""
    return f"[{index + 1}/{total}] "


# Event-kind -> (icon, rich markup color) for EventFeed.write_event.
_EVENT_STYLES: dict[str, tuple[str, str]] = {
    "tool_call": ("▶", "bold cyan"),
    "tool_done": ("✓", "green"),
    "tool_crashed": ("✗", "bold red"),
    "context_log": ("", "dim"),
}


class EventFeed(RichLog):
    """RichLog specialized for a live progress-event feed — color/icon-codes
    each event kind so a run can be scanned at a glance, and escapes every
    interpolated field via rich.markup.escape first.

    Caller supplies already-formatted, already-escaped text per event; kind
    controls the icon/color prefix. Falls back to plain text for an
    unrecognized kind rather than raising.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        kwargs.setdefault("highlight", False)
        kwargs.setdefault("markup", True)
        kwargs.setdefault("wrap", True)
        super().__init__(*args, **kwargs)

    @staticmethod
    def escape(text: str) -> str:
        return escape_markup(text)

    def write_event(self, kind: str, text: str) -> None:
        icon, color = _EVENT_STYLES.get(kind, ("", ""))
        prefix = f"[{color}]{icon}[/] " if icon else (f"[{color}]" if color else "")
        suffix = "[/]" if color and not icon else ""
        self.write(f"{prefix}{text}{suffix}")
