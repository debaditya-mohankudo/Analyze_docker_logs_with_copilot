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

from typing import Callable

from textual.app import App, ComposeResult
from textual.containers import Container, Horizontal, VerticalScroll
from textual.screen import Screen
from textual.widget import Widget
from textual.widgets import Footer, Header, Input, Label, ListItem, ListView, Static

if __package__ in (None, ""):
    # Allows `python3 docker_log_analyzer/tui.py` directly, not just
    # `python -m docker_log_analyzer.tui` / the `docker-log-analyzer-tui`
    # console script — relative imports need a package context that a
    # bare script invocation doesn't have, so fall back to absolute
    # imports with the repo root added to sys.path.
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from docker_log_analyzer import tools
    from docker_log_analyzer.config import settings
    from docker_log_analyzer.logger import logger
    from docker_log_analyzer.tui_widgets import EventFeed, bordered, step_prefix
else:
    from . import tools
    from .config import settings
    from .logger import logger
    from .tui_widgets import EventFeed, bordered, step_prefix

# Total steps in the connect -> window -> menu -> (optional container-name)
# -> result flow, for step_prefix's "[n/TOTAL_STEPS]" counters and the
# breadcrumb bar (see breadcrumb_bar()).
TOTAL_STEPS = 4
STEP_NAMES = ["Connect", "Window", "Menu", "Result"]

# Time-window choices offered by WindowScreen — how far back log-fetching
# tools look (settings.log_lookback_minutes). 24h was the old hardcoded
# default and is far too much for a local dev setup; these are practical
# choices for a live/recent-activity check.
WINDOW_CHOICES: list[tuple[str, int]] = [
    ("5 min", 5),
    ("10 min", 10),
    ("30 min", 30),
]

# ── The 8 most useful prompts, mapped to existing stateless tool_* functions.
# category/icon group them into MenuScreen's sectioned grid — list order is
# still the "prompt-i" index used by ContainerNameScreen/ResultScreen and
# the [key] number shown on each card, category is a display grouping only.

PROMPTS: list[tuple[str, str, str | None, str, str]] = [
    ("List running containers", "tool_list_containers", None, "OVERVIEW", "□"),
    ("Full system health report (all containers)", "tool_analyze_patterns", None, "HEALTH & ROOT CAUSE", "◎"),
    ("Error rate spikes", "tool_analyze_error_spikes", None, "ANOMALIES & DEPENDENCIES", "▲"),
    ("Cross-container error correlation", "tool_analyze_correlations", None, "ANOMALIES & DEPENDENCIES", "⇄"),
    ("Map service dependencies & cascade candidates", "tool_map_service_dependencies", None, "ANOMALIES & DEPENDENCIES", "◆"),
    ("Rank root-cause candidates", "tool_analyze_root_causes", None, "HEALTH & ROOT CAUSE", "★"),
    ("Classify errors by category", "tool_classify_errors", None, "HEALTH & ROOT CAUSE", "▦"),
    ("Last errors for a container", "tool_get_last_errors", "container_name", "CONTAINER LOOKUP", "⚑"),
]

# Category display order — PROMPTS entries are grouped into these buckets
# (preserving each entry's original list position/key number within its
# bucket), not re-sorted arbitrarily.
PROMPT_CATEGORIES = ["OVERVIEW", "HEALTH & ROOT CAUSE", "ANOMALIES & DEPENDENCIES", "CONTAINER LOOKUP"]


def action_logger(component: str) -> Callable[..., None]:
    """Factory producing a logger.info() call pre-tagged with `component`'s
    name ("tui: <component> — <msg>"), so every action_*/on_* handler across
    every screen logs through one call shape instead of hand-formatting the
    "tui: X — " prefix at each call site. `msg` may be a %-style format
    string with `*args` substitutions, same as calling logger.info directly
    (lazy formatting — args aren't interpolated unless the log line fires)."""
    def _log(msg: str, *args) -> None:
        logger.info(f"tui: {component} — {msg}", *args)
    return _log


def breadcrumb_bar(current_index: int) -> Horizontal:
    """"1 . Connect > 2 . Window > 3 . Menu > 4 . Result" stepper bar, current
    step highlighted — yielded at the top of every screen's compose(), above
    the screen's own bordered [n/TOTAL_STEPS] box."""
    chips: list[Widget] = []
    for i, name in enumerate(STEP_NAMES):
        classes = "breadcrumb-chip active" if i == current_index else "breadcrumb-chip"
        chips.append(Static(f"{i + 1} · {name}", classes=classes))
        if i < len(STEP_NAMES) - 1:
            chips.append(Static("›", classes="breadcrumb-sep"))
    return Horizontal(*chips, classes="breadcrumb-bar")


class ClickableCard(Container):
    """A Container that also responds to a mouse click, running the same
    action its key binding already triggers — every card in this app (daemon
    choice, window choice) is reachable by key OR click, never click-only,
    matching the design doc's "every screen is now clickable as well as
    keyboard-driven" while keeping the "no mouse-only Button widgets"
    invariant (test_no_buttons_anywhere): this is a plain Container with a
    click handler, not a textual.widgets.Button.
    """

    can_focus = True

    def __init__(self, *children: Widget, on_activate: Callable[[], None], **kwargs) -> None:
        super().__init__(*children, **kwargs)
        self._on_activate = on_activate

    def on_click(self) -> None:
        self._on_activate()


def _summarize_list_containers(result: dict) -> tuple[str, list[tuple[str, int]]] | None:
    """tool_list_containers -> (headline, [(tile_label, count), ...]).

    Tiles reflect Docker's real container.state.status values only (running/
    restarting/exited/paused/...) — there is no "HEALTHY" tile, since this
    tool doesn't report health-check state, only container status.
    """
    if result.get("status") != "success" or "containers" not in result:
        return None
    counts: dict[str, int] = {}
    for c in result["containers"]:
        status = c.get("status", "unknown")
        counts[status] = counts.get(status, 0) + 1
    if not counts:
        return "No containers running.", []
    headline = ", ".join(f"{n} {s}" for s, n in sorted(counts.items(), key=lambda kv: -kv[1]))
    tiles = [(s.upper(), n) for s, n in sorted(counts.items(), key=lambda kv: -kv[1])]
    return headline, tiles


# tool_name -> summarizer function. Only tool_list_containers has one for
# now (see task:76e0b4e6) — tools without an entry here fall back to raw
# JSON only, no stat tiles/headline.
RESULT_SUMMARIZERS = {
    "tool_list_containers": _summarize_list_containers,
}


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
        ("escape", "back_or_quit", "Quit"),
    ]
    # ConnectScreen is the root/first screen ever pushed (see DockerTUIApp.
    # on_mount) — without its own escape binding, the app-level
    # ("escape", "pop_screen", "Back") binding applies instead, popping this
    # screen off the stack down to Textual's implicit empty default screen
    # (nothing composed on it), which reads as a crash even though the
    # process is still alive. Never let escape pop past the root screen.
    # Textual auto-focuses the first focusable widget (the Input) on mount
    # by default (Screen.AUTO_FOCUS=None means "inherit App.AUTO_FOCUS",
    # which is "*" — not "disabled"). "" is what actually turns it off, so
    # the screen starts with nothing focused and `l`/`r` reach the bindings
    # instead of being typed into the field.
    AUTO_FOCUS = ""
    _log = staticmethod(action_logger("ConnectScreen"))

    def compose(self) -> ComposeResult:
        yield Header()
        yield breadcrumb_bar(0)
        with bordered(Container(classes="connect-box"), f"{step_prefix(0, TOTAL_STEPS)}Connect"):
            yield Label("Choose Docker daemon", classes="title")
            yield Static("Pick where the analysis tools should look for containers.", classes="hint-bar")
            with Horizontal(classes="daemon-choices"):
                with ClickableCard(classes="daemon-card", on_activate=self.action_use_local):
                    yield Static("□", classes="daemon-icon")
                    yield Label("[L] Local Docker", classes="daemon-label")
                    yield Static("unix:///var/run/docker.sock", classes="daemon-hint")
                with ClickableCard(classes="daemon-card", on_activate=self.action_focus_remote):
                    yield Static("⇄", classes="daemon-icon")
                    yield Label("[R] Remote (SSH)", classes="daemon-label")
                    yield Static("user@ip — or just ip", classes="daemon-hint")
            with Container(id="remote-section", classes="-hidden"):
                yield Static("", classes="section-divider")
                yield Label("REMOTE HOST", classes="section-label")
                with Horizontal(classes="remote-input-row"):
                    yield Input(
                        placeholder="user@ip  (uses current OS user if omitted)",
                        id="remote-host",
                        compact=True,
                    )
                    yield Static("[Enter] Connect →", classes="connect-hint")
            yield Static("", id="connect-error")
        yield Footer()

    def action_focus_remote(self) -> None:
        self._log("focus remote-host input")
        self.query_one("#remote-section", Container).remove_class("-hidden")
        self.query_one("#remote-host", Input).focus()

    def action_back_or_quit(self) -> None:
        """Escape from the remote-host input backs out of it; escape with
        nothing focused quits — this screen is the root, there is no
        "back" to pop to (see class docstring/BINDINGS comment)."""
        if self.focused is not None:
            self._log("escape unfocuses remote-host input")
            self.set_focus(None)
            self.query_one("#remote-section", Container).add_class("-hidden")
        else:
            self._log("escape with nothing focused, quitting")
            self.app.exit()

    def action_use_local(self) -> None:
        self._log("chose local Docker (unix socket)")
        settings.docker_host = ""
        self.app.push_screen(WindowScreen())

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id != "remote-host":
            return
        raw = event.value.strip()
        error = self.query_one("#connect-error", Static)
        if not raw:
            self._log("remote host submitted empty, showing error")
            error.update("[red]Enter a remote IP (or user@ip), or press l for local.[/red]")
            return

        user_host = raw if "@" in raw else f"{getpass.getuser()}@{raw}"
        settings.docker_host = f"ssh://{user_host}"
        self._log("chose remote Docker via ssh://%s", user_host)
        self.app.push_screen(WindowScreen())


class WindowScreen(Screen):
    """Second screen: pick how far back log-fetching tools look
    (settings.log_lookback_minutes) before picking a flow to run.

    Press 1/2/3 or click a card to pick 5/10/30 min — no textual.widgets.Button
    anywhere, but every card is a ClickableCard responding to both.
    """

    BINDINGS = [
        (str(i + 1), f"pick_window({minutes})", label)
        for i, (label, minutes) in enumerate(WINDOW_CHOICES)
    ] + [("escape", "app.pop_screen", "Back")]
    _log = staticmethod(action_logger("WindowScreen"))

    def compose(self) -> ComposeResult:
        yield Header()
        yield breadcrumb_bar(1)
        with bordered(Container(classes="connect-box"), f"{step_prefix(1, TOTAL_STEPS)}Time window"):
            yield Label("How far back should log-fetching tools look?", classes="title")
            yield Static("Applies to every log-fetching tool for this session.", classes="hint-bar")
            with Horizontal(classes="window-choices"):
                for i, (label, minutes) in enumerate(WINDOW_CHOICES):
                    with ClickableCard(
                        classes="window-card", on_activate=lambda m=minutes: self.action_pick_window(m)
                    ):
                        yield Static(str(i + 1), classes="window-badge")
                        yield Label(label, classes="window-value")
            yield Static(
                f"No selection yet (previous default: {settings.log_lookback_minutes} min).",
                id="window-status",
                classes="hint-bar",
            )
        yield Footer()

    def action_pick_window(self, minutes: int) -> None:
        self._log("chose %d min log lookback window", minutes)
        settings.log_lookback_minutes = minutes
        self.app.push_screen(MenuScreen())


class ContainerNameScreen(Screen):
    """Collect a container name before running a container-scoped tool. Enter to run."""

    BINDINGS = [("escape", "app.pop_screen", "Back to flows")]
    _log = staticmethod(action_logger("ContainerNameScreen"))

    def __init__(self, label: str, tool_name: str) -> None:
        super().__init__()
        self._label = label
        self._tool_name = tool_name

    def compose(self) -> ComposeResult:
        yield Header()
        yield breadcrumb_bar(2)
        with bordered(Container(classes="modal-box"), f"{step_prefix(2, TOTAL_STEPS)}{self._label}"):
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
            self._log("submitted empty name, showing error")
            self.query_one("#connect-error", Static).update("[red]Container name required.[/red]")
            return
        self._log("running %s for container '%s'", self._tool_name, name)
        self.app.push_screen(ResultScreen(self._label, self._tool_name, {"container_name": name}))


class MenuScreen(Screen):
    """Third screen: pick one of the 8 most useful docker prompts."""

    BINDINGS = [("escape", "app.pop_screen", "Back")]
    _log = staticmethod(action_logger("MenuScreen"))

    def compose(self) -> ComposeResult:
        yield Header()
        yield breadcrumb_bar(2)
        target = settings.docker_host or "local (unix socket)"
        with bordered(Container(classes="detail-box"), f"{step_prefix(2, TOTAL_STEPS)}Pick a prompt"):
            with Horizontal(classes="status-chips"):
                yield Static(f"● Connected: {target}", classes="status-chip connected")
                yield Static(f"◔ Window: {settings.log_lookback_minutes} min", classes="status-chip")
            yield Label("Pick a prompt to run:", classes="title")
            yield bordered(ListView(*self._build_items(), id="prompt-list"), "Prompts")
        yield Footer()

    @staticmethod
    def _build_items() -> list[ListItem]:
        """Category header rows (disabled, unselectable) interleaved with the
        real prompt rows — one flat ListView so up/down navigation and the
        existing "prompt-i" id scheme both keep working, while still reading
        as a grouped/sectioned grid like the mockup."""
        items: list[ListItem] = []
        for category in PROMPT_CATEGORIES:
            indices = [i for i, p in enumerate(PROMPTS) if p[3] == category]
            if not indices:
                continue
            header_id = "header-" + category.replace(" ", "-").replace("&", "and")
            items.append(
                ListItem(Label(category, classes="category-header-label"), id=header_id,
                          classes="category-header", disabled=True)
            )
            for i in indices:
                label, _tool_name, _required_arg, _category, icon = PROMPTS[i]
                items.append(
                    ListItem(Label(f"{i + 1}  {icon}  {label}"), id=f"prompt-{i}", classes="gc-item")
                )
        return items

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        item_id = event.item.id or ""
        if not item_id.startswith("prompt-"):
            return  # category header row — not a real selectable prompt
        index = int(item_id.removeprefix("prompt-"))
        label, tool_name, required_arg, _category, _icon = PROMPTS[index]
        self._log("selected prompt '%s' (%s)", label, tool_name)
        if required_arg:
            self.app.push_screen(ContainerNameScreen(label, tool_name))
        else:
            self.app.push_screen(ResultScreen(label, tool_name, {}))


class ResultScreen(Screen):
    """Runs the selected tool_* function in a worker thread and shows the result:
    a status/tool/duration chips row, a parsed summary + stat tiles when a
    summarizer exists for this tool (RESULT_SUMMARIZERS), and the raw JSON
    behind a collapsible toggle (press 'j')."""

    BINDINGS = [("escape", "app.pop_screen", "Back to flows"), ("j", "toggle_json", "Toggle raw JSON")]
    _log = staticmethod(action_logger("ResultScreen"))

    def __init__(self, label: str, tool_name: str, kwargs: dict) -> None:
        super().__init__()
        self._label = label
        self._tool_name = tool_name
        self._kwargs = kwargs
        self._json_visible = False

    def compose(self) -> ComposeResult:
        yield Header()
        yield breadcrumb_bar(3)
        with bordered(VerticalScroll(classes="detail-box"), f"{step_prefix(3, TOTAL_STEPS)}{self._label}"):
            with Horizontal(classes="status-chips"):
                yield Static("… Running", id="status-chip", classes="status-chip")
                yield Static(self._tool_name, classes="status-chip")
                yield Static("", id="duration-chip", classes="status-chip")
            yield EventFeed(id="result-feed")
            yield Container(id="summary-box")
            yield Static("▶ Show raw JSON [j]", id="json-toggle", classes="hint-bar")
            yield EventFeed(id="raw-json-feed", classes="-hidden")
        yield Footer()

    def on_mount(self) -> None:
        self._log("running %s, kwargs=%s", self._tool_name, self._kwargs)
        self.query_one("#result-feed", EventFeed).write_event(
            "tool_call", EventFeed.escape(f"Running {self._tool_name}...")
        )
        self.run_worker(self._run_tool(), exclusive=True)

    def action_toggle_json(self) -> None:
        self._json_visible = not self._json_visible
        self._log("raw JSON %s", "shown" if self._json_visible else "hidden")
        feed = self.query_one("#raw-json-feed", EventFeed)
        toggle = self.query_one("#json-toggle", Static)
        if self._json_visible:
            feed.remove_class("-hidden")
            toggle.update("▼ Hide raw JSON [j]")
        else:
            feed.add_class("-hidden")
            toggle.update("▶ Show raw JSON [j]")

    def _render_summary(self, result: dict) -> None:
        summarizer = RESULT_SUMMARIZERS.get(self._tool_name)
        if summarizer is None:
            return
        summarized = summarizer(result)
        if summarized is None:
            return
        headline, tiles = summarized
        box = self.query_one("#summary-box", Container)
        box.mount(Label(headline, classes="summary-headline"))
        if tiles:
            tile_row = Horizontal(classes="stat-tiles")
            box.mount(tile_row)
            for tile_label, count in tiles:
                tile = Container(classes="stat-tile")
                tile_row.mount(tile)
                tile.mount(Static(tile_label, classes="stat-tile-label"))
                tile.mount(Static(str(count), classes="stat-tile-value"))

    async def _run_tool(self) -> None:
        import asyncio
        import time

        feed = self.query_one("#result-feed", EventFeed)
        raw_feed = self.query_one("#raw-json-feed", EventFeed)
        status_chip = self.query_one("#status-chip", Static)
        duration_chip = self.query_one("#duration-chip", Static)
        fn = getattr(tools, self._tool_name)
        started = time.monotonic()
        try:
            result = await asyncio.to_thread(fn, **self._kwargs)
            elapsed = time.monotonic() - started
            text = json.dumps(result, indent=2, default=str)
            status = result.get("status") if isinstance(result, dict) else "?"
            self._log("%s completed in %.2fs, status=%s", self._tool_name, elapsed, status)
            feed.write_event("tool_done", EventFeed.escape(f"done ({status})"))
            raw_feed.write_event("tool_done", EventFeed.escape(text))
            status_chip.update("✓ Success" if status == "success" else f"✗ {status}")
            status_chip.set_class(status == "success", "status-chip-success")
            duration_chip.update(f"{elapsed * 1000:.0f}ms")
            if isinstance(result, dict):
                self._render_summary(result)
        except Exception as exc:  # noqa: BLE001 - surface any failure in the UI
            elapsed = time.monotonic() - started
            logger.exception("tui: ResultScreen — %s raised after %.2fs", self._tool_name, elapsed)
            text = json.dumps({"status": "error", "error": str(exc)}, indent=2)
            feed.write_event("tool_crashed", EventFeed.escape(str(exc)))
            raw_feed.write_event("tool_crashed", EventFeed.escape(text))
            status_chip.update("✗ Error")
            duration_chip.update(f"{elapsed * 1000:.0f}ms")


class DockerTUIApp(App):
    """Textual entry point for the Docker Log Analyzer TUI.

    Shared visual language matches SeniorDevAgent's ConceptBrowser app —
    same .hint-bar / .modal-box / .detail-box / .gc-item classes, the same
    bordered()/step_prefix()/EventFeed helpers (vendored in tui_widgets.py),
    no mouse-clickable buttons anywhere.
    """

    CSS = """
    .hint-bar { height: auto; padding: 0 1; color: $text-muted; background: $panel; border-bottom: solid $accent; }
    .modal-box { width: 70; height: auto; padding: 1 2; margin: 2 4; border: round $accent; background: $panel; }
    .connect-box { width: 96; height: auto; padding: 1 2; margin: 2 4; border: round $accent; background: $panel; }
    .detail-box { padding: 1 2; margin: 1 2; border: round $accent; background: $panel; }
    .title { text-style: bold; margin-bottom: 1; }
    .gc-item { border-bottom: dashed $accent 50%; }
    .gc-item:last-of-type { border-bottom: none; }
    #connect-error { margin-top: 1; }
    ListView { height: auto; max-height: 16; border: round $accent; }
    #result-feed { height: 4; border: round $accent; }

    .daemon-choices, .window-choices { height: auto; margin-top: 1; }
    .daemon-card, .window-card {
        width: 1fr; height: auto; margin-right: 2; padding: 1 2;
        border: round $accent 50%; background: $panel;
        align: center middle;
    }
    .daemon-card:last-of-type, .window-card:last-of-type { margin-right: 0; }
    .daemon-icon, .window-badge { text-align: center; width: 100%; color: $accent; margin-bottom: 1; }
    .daemon-label, .window-value { text-style: bold; text-align: center; width: 100%; }
    .daemon-hint { color: $text-muted; text-align: center; width: 100%; }
    #remote-section { height: auto; margin-top: 1; }
    #remote-section.-hidden { display: none; }
    .section-divider { height: 1; border-bottom: dashed $accent 50%; margin-bottom: 1; }
    .section-label { color: $text-muted; text-style: bold; margin-bottom: 1; }
    .remote-input-row { height: auto; }
    .remote-input-row Input { width: 1fr; }
    .connect-hint { width: auto; color: $accent; text-style: bold; padding: 1 2; }

    .breadcrumb-bar { height: auto; padding: 1 2; border-bottom: solid $accent 30%; align: left middle; }
    .breadcrumb-chip { width: auto; height: 3; color: $text-muted; padding: 1 1; }
    .breadcrumb-chip.active { color: $accent; text-style: bold; border: round $accent; padding: 0 1; }
    .breadcrumb-sep { width: auto; height: 3; color: $text-muted; padding: 1 1; }

    .status-chips { height: auto; margin-bottom: 1; }
    .status-chip {
        width: auto; border: round $accent 50%; padding: 0 1; margin-right: 1; color: $text-muted;
    }
    .status-chip.connected { color: $success; border: round $success 50%; }
    .status-chip-success { color: $success; border: round $success 50%; }

    .category-header { background: transparent; }
    .category-header-label { color: $text-muted; text-style: bold; }

    #summary-box { height: auto; margin-top: 1; }
    .summary-headline { text-style: bold; margin-bottom: 1; }
    .stat-tiles { height: auto; }
    .stat-tile {
        width: 1fr; height: auto; margin-right: 2; padding: 1 2;
        border: round $accent 50%; background: $panel;
    }
    .stat-tile:last-of-type { margin-right: 0; }
    .stat-tile-label { color: $text-muted; }
    .stat-tile-value { text-style: bold; }
    #json-toggle { margin-top: 1; }
    #raw-json-feed { height: 12; border: round $accent; margin-top: 1; }
    #raw-json-feed.-hidden { display: none; }
    """
    BINDINGS = [("q", "quit", "Quit"), ("escape", "pop_screen", "Back")]
    _log = staticmethod(action_logger("app"))

    def on_mount(self) -> None:
        self._log("started")
        self.push_screen(ConnectScreen())

    async def action_pop_screen(self) -> None:
        # WindowScreen/ContainerNameScreen/MenuScreen/ResultScreen all bind
        # escape to "app.pop_screen" directly rather than a local method —
        # logging centrally here covers every one of those "Back" presses
        # without repeating the same log call in each screen class.
        # App.action_pop_screen is a coroutine in this Textual version —
        # must await it, not call it synchronously.
        self._log("back — popping %s", type(self.screen).__name__)
        await super().action_pop_screen()

    async def action_quit(self) -> None:
        self._log("quit requested (q)")
        await super().action_quit()


def run() -> None:
    DockerTUIApp().run()


if __name__ == "__main__":
    run()
