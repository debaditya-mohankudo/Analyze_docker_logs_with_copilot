"""tui.py – Terminal UI for Docker Log Analyzer.

Textual app that lets a developer pick local or remote (SSH) Docker before
running the 8 most useful analysis prompts, without needing VSCode Copilot.

Calls the same tool_* functions the MCP server exposes (see tools.py) —
no separate code path, no LLM involved.

Visual language matches SeniorDevAgent's tui/app.py ConceptBrowser: no
mouse-clickable buttons anywhere — every action is a key binding surfaced
in the Footer, like a CLI app. Same .hint-bar / .modal-box / .detail-box
CSS classes, plus the same widget conventions (border_title via bordered(),
step_prefix counters, EventFeed color-coded result output) vendored into
tui_widgets.py — see task:7323b8ef.
"""

import getpass
import json

from textual.app import App, ComposeResult
from textual.containers import Container, VerticalScroll
from textual.screen import Screen
from textual.widgets import Footer, Header, Input, Label, ListItem, ListView, Static

from . import tools
from .config import settings
from .tui_widgets import EventFeed, bordered, step_prefix

# Total steps in the connect -> menu -> (optional container-name) -> result
# flow, for step_prefix's "[n/TOTAL_STEPS]" counters.
TOTAL_STEPS = 3

# ── The 8 most useful prompts, mapped to existing stateless tool_* functions ──

PROMPTS: list[tuple[str, str, str | None]] = [
    ("List running containers", "tool_list_containers", None),
    ("Full system health report (all containers)", "tool_analyze_patterns", None),
    ("Error rate spikes (last 24h)", "tool_analyze_error_spikes", None),
    ("Cross-container error correlation", "tool_analyze_correlations", None),
    ("Map service dependencies & cascade candidates", "tool_map_service_dependencies", None),
    ("Rank root-cause candidates", "tool_analyze_root_causes", None),
    ("Classify errors by category", "tool_classify_errors", None),
    ("Last errors for a container", "tool_get_last_errors", "container_name"),
]


class ConnectScreen(Screen):
    """First screen: choose local or remote (SSH) Docker daemon.

    No buttons — press `l` for local, or `r` to type a remote host then
    Enter. The input isn't auto-focused: while it's unfocused, `l`/`r` are
    plain key bindings surfaced in the Footer; `r` moves focus into the
    input so the same letter never gets typed into the field.
    """

    BINDINGS = [
        ("l", "use_local", "Local Docker"),
        ("r", "focus_remote", "Remote (SSH)"),
    ]
    # Textual auto-focuses the first focusable widget (the Input) on mount
    # by default (Screen.AUTO_FOCUS=None means "inherit App.AUTO_FOCUS",
    # which is "*" — not "disabled"). "" is what actually turns it off, so
    # the screen starts with nothing focused and `l`/`r` reach the bindings
    # instead of being typed into the field.
    AUTO_FOCUS = ""

    def compose(self) -> ComposeResult:
        yield Header()
        with bordered(Container(classes="modal-box"), f"{step_prefix(0, TOTAL_STEPS)}Connect"):
            yield Label("Docker Log Analyzer", classes="title")
            yield Static(
                "Press [b]l[/b] for local Docker, or [b]r[/b] to enter a remote host (then Enter to submit)",
                classes="hint-bar",
            )
            yield Input(
                placeholder="user@ip  (remote via SSH — or just ip, uses current OS user)",
                id="remote-host",
            )
            yield Static("", id="connect-error")
        yield Footer()

    def action_focus_remote(self) -> None:
        self.query_one("#remote-host", Input).focus()

    def action_use_local(self) -> None:
        settings.docker_host = ""
        self.app.push_screen(MenuScreen())

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id != "remote-host":
            return
        raw = event.value.strip()
        error = self.query_one("#connect-error", Static)
        if not raw:
            error.update("[red]Enter a remote IP (or user@ip), or press l for local.[/red]")
            return

        user_host = raw if "@" in raw else f"{getpass.getuser()}@{raw}"
        settings.docker_host = f"ssh://{user_host}"
        self.app.push_screen(MenuScreen())


class ContainerNameScreen(Screen):
    """Collect a container name before running a container-scoped tool. Enter to run."""

    BINDINGS = [("escape", "app.pop_screen", "Back to flows")]

    def __init__(self, label: str, tool_name: str) -> None:
        super().__init__()
        self._label = label
        self._tool_name = tool_name

    def compose(self) -> ComposeResult:
        yield Header()
        with bordered(Container(classes="modal-box"), f"{step_prefix(1, TOTAL_STEPS)}{self._label}"):
            yield Label(self._label, classes="title")
            yield Static("Type a container name, press Enter to run", classes="hint-bar")
            yield Input(placeholder="container name", id="container-name")
            yield Static("", id="connect-error")
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#container-name", Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        name = event.value.strip()
        if not name:
            self.query_one("#connect-error", Static).update("[red]Container name required.[/red]")
            return
        self.app.push_screen(ResultScreen(self._label, self._tool_name, {"container_name": name}))


class MenuScreen(Screen):
    """Second screen: pick one of the 8 most useful docker prompts."""

    BINDINGS = [("escape", "app.pop_screen", "Back")]

    def compose(self) -> ComposeResult:
        yield Header()
        target = settings.docker_host or "local (unix socket)"
        with bordered(Container(classes="detail-box"), f"{step_prefix(1, TOTAL_STEPS)}Pick a prompt"):
            yield Static(f"Connected to: {target}", classes="hint-bar")
            yield Label("Pick a prompt to run:", classes="title")
            yield bordered(
                ListView(
                    *[
                        ListItem(Label(f"{i + 1}. {label}"), id=f"prompt-{i}", classes="gc-item")
                        for i, (label, _fn, _arg) in enumerate(PROMPTS)
                    ],
                    id="prompt-list",
                ),
                "Prompts",
            )
        yield Footer()

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        index = int(event.item.id.removeprefix("prompt-"))
        label, tool_name, required_arg = PROMPTS[index]
        if required_arg:
            self.app.push_screen(ContainerNameScreen(label, tool_name))
        else:
            self.app.push_screen(ResultScreen(label, tool_name, {}))


class ResultScreen(Screen):
    """Runs the selected tool_* function in a worker thread and shows the JSON result."""

    BINDINGS = [("escape", "app.pop_screen", "Back to flows")]

    def __init__(self, label: str, tool_name: str, kwargs: dict) -> None:
        super().__init__()
        self._label = label
        self._tool_name = tool_name
        self._kwargs = kwargs

    def compose(self) -> ComposeResult:
        yield Header()
        with bordered(VerticalScroll(classes="detail-box"), f"{step_prefix(2, TOTAL_STEPS)}{self._label}"):
            yield Label(self._label, id="result-title", classes="title")
            yield EventFeed(id="result-feed")
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#result-feed", EventFeed).write_event(
            "tool_call", EventFeed.escape(f"Running {self._tool_name}...")
        )
        self.run_worker(self._run_tool(), exclusive=True)

    async def _run_tool(self) -> None:
        import asyncio

        feed = self.query_one("#result-feed", EventFeed)
        fn = getattr(tools, self._tool_name)
        try:
            result = await asyncio.to_thread(fn, **self._kwargs)
            text = json.dumps(result, indent=2, default=str)
            feed.write_event("tool_done", EventFeed.escape(text))
        except Exception as exc:  # noqa: BLE001 - surface any failure in the UI
            text = json.dumps({"status": "error", "error": str(exc)}, indent=2)
            feed.write_event("tool_crashed", EventFeed.escape(text))


class DockerTUIApp(App):
    """Textual entry point for the Docker Log Analyzer TUI.

    Shared visual language matches SeniorDevAgent's ConceptBrowser app —
    same .hint-bar / .modal-box / .detail-box / .gc-item classes, the same
    bordered()/step_prefix()/EventFeed helpers (vendored in tui_widgets.py),
    no mouse-clickable buttons anywhere.
    """

    CSS = """
    .hint-bar { height: auto; padding: 0 1; color: $text-muted; background: $panel; border-bottom: solid $accent; }
    .modal-box { width: auto; height: auto; max-width: 80; padding: 1 2; margin: 2 4; border: round $accent; background: $panel; }
    .detail-box { padding: 1 2; margin: 1 2; border: round $accent; background: $panel; }
    .title { text-style: bold; margin-bottom: 1; }
    .gc-item { border-bottom: dashed $accent 50%; }
    .gc-item:last-of-type { border-bottom: none; }
    #connect-error { margin-top: 1; }
    ListView { height: auto; max-height: 16; border: round $accent; }
    #result-feed { height: 1fr; border: round $accent; }
    """
    BINDINGS = [("q", "quit", "Quit"), ("escape", "pop_screen", "Back")]

    def on_mount(self) -> None:
        self.push_screen(ConnectScreen())


def run() -> None:
    DockerTUIApp().run()


if __name__ == "__main__":
    run()
