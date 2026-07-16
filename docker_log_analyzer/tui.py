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
import re

from datetime import datetime
from pathlib import Path
from typing import Callable

from textual.app import App, ComposeResult
from textual.containers import Container, Horizontal, VerticalScroll
from textual.screen import Screen
from textual.widget import Widget
from textual.widgets import Footer, Header, Input, Label, ListItem, ListView, SelectionList, Static

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
    ("Fetch logs for container(s)", "tool_sync_docker_logs", "multi_container_names", "CONTAINER LOOKUP", "⇊"),
    ("Full system health report (all containers)", "tool_analyze_patterns", None, "HEALTH & ROOT CAUSE", "◎"),
    ("Error rate spikes", "tool_analyze_error_spikes", None, "ANOMALIES & DEPENDENCIES", "▲"),
    ("Cross-container error correlation", "tool_analyze_correlations", None, "ANOMALIES & DEPENDENCIES", "⇄"),
    ("Map service dependencies & cascade candidates", "tool_map_service_dependencies", None, "ANOMALIES & DEPENDENCIES", "◆"),
    ("Rank root-cause candidates", "tool_analyze_root_causes", None, "HEALTH & ROOT CAUSE", "★"),
    ("Classify errors by category", "tool_classify_errors", None, "HEALTH & ROOT CAUSE", "▦"),
    ("Last errors for a container", "tool_get_last_errors", "container_name", "CONTAINER LOOKUP", "⚑"),
    ("Capture live logs for next N minutes", "tool_capture_logs", "multi_container_and_duration", "CONTAINER LOOKUP", "⏺"),
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


def _summarize_list_containers(result: dict) -> tuple[str, list[tuple[str, str]]] | None:
    """tool_list_containers -> (headline, [(name, status), ...]).

    Headline reflects Docker's real container.state.status values only
    (running/restarting/exited/paused/...) — there is no "HEALTHY" state,
    since this tool doesn't report health-check state, only container
    status. Names are sorted alphabetically and rendered as a plain bullet
    list — green for "running", grey for anything else (see
    ResultScreen._render_summary). No separate stat tiles: they'd just repeat
    the headline's counts.
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
    names = sorted(
        ((c.get("name", "?"), c.get("status", "unknown")) for c in result["containers"]),
        key=lambda nc: nc[0],
    )
    return headline, names


def _summarize_capture_logs(result: dict) -> tuple[str, list[tuple[str, str]]] | None:
    """tool_capture_logs -> (headline, [(container summary, status), ...]).

    Reuses the same green/dim bullet-list rendering as
    _summarize_list_containers, but repurposes "status" here to mean
    "no errors captured" (green) vs "errors captured" (dim) rather than
    Docker's running/exited state — capture_logs doesn't report that."""
    if result.get("status") != "success" or "summary" not in result:
        return None
    summary = result["summary"]
    duration = result.get("capture_window", {}).get("duration_seconds", "?")
    headline = (
        f"{summary.get('total_log_lines', 0)} lines · "
        f"{summary.get('total_errors', 0)} errors · "
        f"{summary.get('spike_count', 0)} spikes over {duration}s"
    )
    rows: list[tuple[str, str]] = []
    for name, pc in sorted(result.get("per_container", {}).items()):
        lines = pc.get("lines_captured", 0)
        errors = sum(
            v for k, v in pc.get("log_levels", {}).items()
            if k in ("ERROR", "CRITICAL", "FATAL", "SEVERE")
        )
        status = "running" if errors == 0 else "errors"
        row = f"{name} — {lines} lines, {errors} errors"
        # Surface the actual error text, not just the count — top_errors is
        # already computed by the tool (detector.extract_error_patterns) but
        # was previously discarded here, leaving no way to see *what* broke
        # without opening the raw JSON. top_errors only matches a narrow set
        # of known shapes and can be empty even when errors > 0 — fall back
        # to error_lines (broader ERROR_PATTERN_RE match) in that case so
        # something concrete is always shown when errors were captured.
        top_errors = pc.get("top_errors") or []
        if top_errors:
            patterns = ", ".join(
                f"{e['pattern']} (x{e['count']})" for e in top_errors[:3]
            )
            row += f": {patterns}"
        elif errors:
            error_lines = pc.get("error_lines") or []
            if error_lines:
                row += f": {error_lines[0].get('message', '').strip()[:120]}"
        rows.append((row, status))
    return headline, rows


# tool_name -> summarizer function. Tools without an entry here fall back to
# raw JSON only, no stat tiles/headline.
RESULT_SUMMARIZERS = {
    "tool_list_containers": _summarize_list_containers,
    "tool_capture_logs": _summarize_capture_logs,
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
        ("f2", "connect_remote", "Connect"),
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

    def __init__(self) -> None:
        super().__init__()
        self._remote_visible = False

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        # "Connect" (f2) only makes sense once the remote-host field is
        # showing — before that there's nothing to submit, so hide it from
        # the Footer entirely rather than leaving a dead-looking key around.
        if action == "connect_remote":
            return self._remote_visible
        return True

    def compose(self) -> ComposeResult:
        yield Header()
        yield breadcrumb_bar(0)
        with bordered(Container(classes="connect-box"), f"{step_prefix(0, TOTAL_STEPS)}Connect"):
            yield Static("", id="connection-status", classes="-hidden")
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
                yield Input(
                    placeholder="user@ip  (uses current OS user if omitted)",
                    id="remote-host",
                    compact=True,
                )
            yield Static("", id="connect-error")
        yield Footer()

    def action_focus_remote(self) -> None:
        self._log("focus remote-host input")
        self._remote_visible = True
        self.refresh_bindings()
        self.query_one("#remote-section", Container).remove_class("-hidden")
        self.query_one("#remote-host", Input).focus()

    def action_back_or_quit(self) -> None:
        """Escape from the remote-host input backs out of it; escape with
        nothing focused quits — this screen is the root, there is no
        "back" to pop to (see class docstring/BINDINGS comment)."""
        if self.focused is not None:
            self._log("escape unfocuses remote-host input")
            self._remote_visible = False
            self.refresh_bindings()
            self.set_focus(None)
            self.query_one("#remote-section", Container).add_class("-hidden")
        else:
            self._log("escape with nothing focused, quitting")
            self.app.exit()

    def action_use_local(self) -> None:
        self._log("chose local Docker (unix socket)")
        settings.docker_host = ""
        self.run_worker(self._test_connection("local (unix socket)"), exclusive=True)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id != "remote-host":
            return
        self.action_connect_remote()

    def action_connect_remote(self) -> None:
        raw = self.query_one("#remote-host", Input).value.strip()
        error = self.query_one("#connect-error", Static)
        if not raw:
            self._log("remote host submitted empty, showing error")
            error.update("[red]Enter a remote IP (or user@ip), or press l for local.[/red]")
            return

        raw = raw.removeprefix("ssh://")
        user_host = raw if "@" in raw else f"{getpass.getuser()}@{raw}"
        settings.docker_host = f"ssh://{user_host}"
        self._log("chose remote Docker via ssh://%s", user_host)
        self.run_worker(self._test_connection(user_host), exclusive=True)

    async def _test_connection(self, target_label: str) -> None:
        """Actually verifies the Docker daemon is reachable (tool_list_containers
        round-trips through _docker_client()'s client.system.info() ping) before
        advancing — settings.docker_host being set doesn't mean it works.

        Every interpolated field is escaped via rich.markup.escape() before being
        wrapped in a [color] tag — target_label comes from user input and the
        Docker error text is raw CLI output full of literal `[`/`]`/`{`/`}` (JSON),
        any of which Rich would otherwise try to parse as markup and crash on."""
        import asyncio

        from rich.markup import escape

        status = self.query_one("#connection-status", Static)
        error = self.query_one("#connect-error", Static)
        safe_target = escape(target_label)
        error.update("")
        status.remove_class("-hidden")
        status.update(f"[dim]◌ Testing connection to {safe_target}...[/dim]")
        result = await asyncio.to_thread(tools.tool_list_containers)
        if result.get("status") == "success":
            self._log("connection test to %s succeeded", target_label)
            status.update(f"[green]● Connected — {safe_target}[/green]")
            # Brief pause so the success state is actually visible — without
            # this the screen advances to WindowScreen in the same frame and
            # the green "Connected" flash is never seen, leaving a gap before
            # the next permanent "Connected" chip (WindowScreen/MenuScreen).
            await asyncio.sleep(0.6)
            self.app.push_screen(WindowScreen())
        else:
            error_text = escape(str(result.get("error", "Could not reach Docker daemon.")))
            self._log("connection test to %s failed: %s", target_label, result.get("error"))
            status.update(f"[red]✗ Connection failed — {safe_target}[/red]")
            error.update(f"[red]{error_text}[/red]")


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
        target = settings.docker_host or "local (unix socket)"
        with bordered(Container(classes="connect-box"), f"{step_prefix(1, TOTAL_STEPS)}Time window"):
            with Horizontal(classes="status-chips"):
                yield Static(f"● Connected: {target}", classes="status-chip connected")
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
    """Pick one container (from tool_list_containers) before running a
    container-scoped tool. A ListView, not free text — the exact container
    names are already known from the daemon, so typos/guessing shouldn't be
    possible. Enter selects, same convention as MenuScreen's prompt list
    (ListView's own "enter" -> select_cursor binding fires on_list_view_selected;
    no competing screen-level binding needed, so there's nothing to shadow)."""

    BINDINGS = [("escape", "app.pop_screen", "Back to flows")]
    _log = staticmethod(action_logger("ContainerNameScreen"))

    def __init__(self, label: str, tool_name: str) -> None:
        super().__init__()
        self._label = label
        self._tool_name = tool_name

    def compose(self) -> ComposeResult:
        yield Header()
        yield breadcrumb_bar(2)
        target = settings.docker_host or "local (unix socket)"
        with bordered(Container(classes="modal-box"), f"{step_prefix(2, TOTAL_STEPS)}{self._label}"):
            with Horizontal(classes="status-chips"):
                yield Static(f"● Connected: {target}", classes="status-chip connected")
                yield Static(f"◔ Window: {settings.log_lookback_minutes} min", classes="status-chip")
            yield Label(self._label, classes="title")
            yield Static("↑↓ to pick a container, Enter to run.", classes="hint-bar")
            yield bordered(ListView(id="container-name-list"), "Containers")
            yield Static("", id="connect-error")
        yield Footer()

    def on_mount(self) -> None:
        self.run_worker(self._load_containers(), exclusive=True)

    async def _load_containers(self) -> None:
        import asyncio

        result = await asyncio.to_thread(tools.tool_list_containers)
        list_view = self.query_one("#container-name-list", ListView)
        error = self.query_one("#connect-error", Static)
        if result.get("status") != "success" or not result.get("containers"):
            self._log("no containers available to pick")
            error.update("[red]No containers found.[/red]")
            return
        for c in sorted(result["containers"], key=lambda c: c.get("name", "?")):
            name = c.get("name", "?")
            running = c.get("status") == "running"
            bullet = "[green]●[/green]" if running else "[dim]●[/dim]"
            # ListView.append() returns an AwaitMount — must actually be
            # awaited or the item never mounts (fire-and-forget silently
            # drops it, leaving the list empty despite the loop "running").
            await list_view.append(ListItem(Label(f"{bullet} {EventFeed.escape(name)}"), name=name))
        self._log("loaded %d containers to pick from", len(result["containers"]))
        # ListView.append doesn't auto-highlight a row — without this, Enter
        # has nothing selected and on_list_view_selected never fires.
        list_view.index = 0
        list_view.focus()

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        name = event.item.name
        self._log("running %s for container '%s'", self._tool_name, name)
        self.app.push_screen(ResultScreen(self._label, self._tool_name, {"container_name": name}))


class ContainerMultiSelectScreen(Screen):
    """Collect one or more container names before running a multi-container
    tool (currently just "Fetch logs for container(s)" -> tool_sync_docker_logs).

    Lists every container (running or not) via tool_list_containers in a
    worker thread, then lets the user check any number of them with a
    SelectionList (space toggles, f2 runs). Selecting nothing and pressing
    f2 runs the tool for every running container, matching
    tool_sync_docker_logs's own container_names=None ("all running") default.

    f2 rather than enter: SelectionList/OptionList already binds enter to
    its own "select" action (toggle highlighted row) with show=False — while
    the list has focus, that shadows a screen-level "enter" binding both in
    the Footer (hidden) and in actual key handling (Enter would just toggle
    the row instead of fetching). Same class of bug as ConnectScreen's
    f2-for-Connect fix.
    """

    BINDINGS = [
        ("space", "toggle_highlighted", "Toggle"),
        ("f2", "run_selected", "Fetch"),
        ("escape", "app.pop_screen", "Back to flows"),
    ]
    _log = staticmethod(action_logger("ContainerMultiSelectScreen"))

    def __init__(self, label: str, tool_name: str) -> None:
        super().__init__()
        self._label = label
        self._tool_name = tool_name
        self._has_options = False

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        # "Toggle" is meaningless (and toggles nothing) until the container
        # list has actually loaded — hide it from the Footer until then.
        if action == "toggle_highlighted":
            return self._has_options
        return True

    def compose(self) -> ComposeResult:
        yield Header()
        yield breadcrumb_bar(2)
        target = settings.docker_host or "local (unix socket)"
        with bordered(Container(classes="modal-box"), f"{step_prefix(2, TOTAL_STEPS)}{self._label}"):
            with Horizontal(classes="status-chips"):
                yield Static(f"● Connected: {target}", classes="status-chip connected")
                yield Static(f"◔ Window: {settings.log_lookback_minutes} min", classes="status-chip")
            yield Label(self._label, classes="title")
            yield Static(
                "Click or use ↑↓ + Space to toggle, F2 to fetch. "
                "None selected = all running containers.",
                classes="hint-bar",
            )
            yield SelectionList(id="container-select")
            yield Static("", id="selection-status", classes="hint-bar")
        yield Footer()

    def on_mount(self) -> None:
        self.run_worker(self._load_containers(), exclusive=True)

    async def _load_containers(self) -> None:
        import asyncio

        result = await asyncio.to_thread(tools.tool_list_containers)
        select = self.query_one("#container-select", SelectionList)
        status = self.query_one("#selection-status", Static)
        if result.get("status") != "success" or not result.get("containers"):
            self._log("no containers available to select")
            status.update("[red]No containers found.[/red]")
            return
        for c in result["containers"]:
            name = c.get("name", "?")
            select.add_option((name, name))
        self._log("loaded %d containers to select from", len(result["containers"]))
        self._has_options = True
        self.refresh_bindings()
        self._update_selection_status()
        select.focus()

    def _update_selection_status(self) -> None:
        selected = list(self.query_one("#container-select", SelectionList).selected)
        status = self.query_one("#selection-status", Static)
        if selected:
            status.update(f"{len(selected)} selected: {', '.join(selected)}")
        else:
            status.update("None selected — will fetch all running containers")

    def on_selection_list_selected_changed(self, event: SelectionList.SelectedChanged) -> None:
        selected = list(self.query_one("#container-select", SelectionList).selected)
        self._log("selection changed, now selected=%s", selected or "<none>")
        self._update_selection_status()

    def action_toggle_highlighted(self) -> None:
        # SelectionList's own "space" binding (show=False) already does this
        # when it's focused; this screen-level binding exists so Footer
        # surfaces "Space Toggle" like every other key in this app.
        self.query_one("#container-select", SelectionList).action_select()

    def action_run_selected(self) -> None:
        select = self.query_one("#container-select", SelectionList)
        selected = list(select.selected)
        self._log("running %s for containers %s", self._tool_name, selected or "<all running>")
        kwargs = {"container_names": selected} if selected else {}
        self.app.push_screen(ResultScreen(self._label, self._tool_name, kwargs))


class CaptureLogsScreen(Screen):
    """Collect container(s) + a duration in minutes before running
    tool_capture_logs, which blocks for that long (time.sleep) watching live
    logs before returning a combined spike/correlation/error report.

    Same container SelectionList as ContainerMultiSelectScreen (none selected
    = all running), plus a minutes Input. F2 rather than Enter for the same
    reason as ContainerMultiSelectScreen: SelectionList already shadows Enter
    with its own toggle action while focused.
    """

    BINDINGS = [
        ("space", "toggle_highlighted", "Toggle"),
        ("f2", "start_capture", "Start capture"),
        ("escape", "app.pop_screen", "Back to flows"),
    ]
    _log = staticmethod(action_logger("CaptureLogsScreen"))
    DEFAULT_MINUTES = 2

    def __init__(self, label: str, tool_name: str) -> None:
        super().__init__()
        self._label = label
        self._tool_name = tool_name
        self._has_options = False

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        if action == "toggle_highlighted":
            return self._has_options
        return True

    def compose(self) -> ComposeResult:
        yield Header()
        yield breadcrumb_bar(2)
        target = settings.docker_host or "local (unix socket)"
        with bordered(Container(classes="modal-box"), f"{step_prefix(2, TOTAL_STEPS)}{self._label}"):
            with Horizontal(classes="status-chips"):
                yield Static(f"● Connected: {target}", classes="status-chip connected")
            yield Label(self._label, classes="title")
            yield Static(
                "Click or use ↑↓ + Space to toggle containers. "
                "None selected = all running containers.",
                classes="hint-bar",
            )
            yield SelectionList(id="container-select")
            yield Static("", id="selection-status", classes="hint-bar")
            yield Static("", classes="section-divider")
            yield Label("MINUTES TO CAPTURE", classes="section-label")
            yield Input(
                value=str(self.DEFAULT_MINUTES),
                placeholder="minutes",
                id="duration-minutes",
                compact=True,
            )
            yield Static("F2 to start — logs are captured live for that many minutes.", classes="hint-bar")
            yield Static("", id="capture-error")
        yield Footer()

    def on_mount(self) -> None:
        self.run_worker(self._load_containers(), exclusive=True)

    async def _load_containers(self) -> None:
        import asyncio

        result = await asyncio.to_thread(tools.tool_list_containers)
        select = self.query_one("#container-select", SelectionList)
        status = self.query_one("#selection-status", Static)
        if result.get("status") != "success" or not result.get("containers"):
            self._log("no containers available to select")
            status.update("[red]No containers found.[/red]")
            return
        for c in result["containers"]:
            name = c.get("name", "?")
            select.add_option((name, name))
        self._log("loaded %d containers to select from", len(result["containers"]))
        self._has_options = True
        self.refresh_bindings()
        self._update_selection_status()
        select.focus()

    def _update_selection_status(self) -> None:
        selected = list(self.query_one("#container-select", SelectionList).selected)
        status = self.query_one("#selection-status", Static)
        if selected:
            status.update(f"{len(selected)} selected: {', '.join(selected)}")
        else:
            status.update("None selected — will capture all running containers")

    def on_selection_list_selected_changed(self, event: SelectionList.SelectedChanged) -> None:
        self._log("selection changed, now selected=%s", list(event.selection_list.selected) or "<none>")
        self._update_selection_status()

    def action_toggle_highlighted(self) -> None:
        self.query_one("#container-select", SelectionList).action_select()

    def action_start_capture(self) -> None:
        error = self.query_one("#capture-error", Static)
        raw_minutes = self.query_one("#duration-minutes", Input).value.strip()
        try:
            minutes = float(raw_minutes)
            if minutes <= 0:
                raise ValueError
        except ValueError:
            self._log("invalid minutes input %r", raw_minutes)
            error.update("[red]Enter a positive number of minutes.[/red]")
            return

        selected = list(self.query_one("#container-select", SelectionList).selected)
        self._log(
            "starting %s for containers %s, duration=%.1f min",
            self._tool_name, selected or "<all running>", minutes,
        )
        kwargs: dict = {"duration_seconds": int(minutes * 60)}
        if selected:
            kwargs["container_names"] = selected
        self.app.push_screen(ResultScreen(self._label, self._tool_name, kwargs))


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
        if required_arg == "multi_container_names":
            self.app.push_screen(ContainerMultiSelectScreen(label, tool_name))
        elif required_arg == "multi_container_and_duration":
            self.app.push_screen(CaptureLogsScreen(label, tool_name))
        elif required_arg:
            self.app.push_screen(ContainerNameScreen(label, tool_name))
        else:
            self.app.push_screen(ResultScreen(label, tool_name, {}))


class ResultScreen(Screen):
    """Runs the selected tool_* function in a worker thread and shows the result:
    a status/tool/lookback chips row, a parsed summary + stat tiles when a
    summarizer exists for this tool (RESULT_SUMMARIZERS), and the raw JSON
    behind a collapsible toggle (press 'j')."""

    BINDINGS = [
        ("escape", "app.pop_screen", "Back to flows"),
        ("j", "toggle_json", "Toggle raw JSON"),
        ("s", "save_json", "Save JSON"),
    ]
    _log = staticmethod(action_logger("ResultScreen"))

    def __init__(self, label: str, tool_name: str, kwargs: dict) -> None:
        super().__init__()
        self._label = label
        self._tool_name = tool_name
        self._kwargs = kwargs
        self._json_visible = False
        self._result_text: str | None = None
        self._result_dict: dict | None = None
        self._started_monotonic: float | None = None

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        # "Toggle raw JSON" / "Save JSON" are meaningless before the tool
        # call finishes — hide them from the Footer until there's actually
        # a result to toggle or save.
        if action in ("toggle_json", "save_json"):
            return self._result_text is not None
        return True

    @staticmethod
    def _format_mmss(seconds: float) -> str:
        seconds = max(0, int(seconds))
        return f"{seconds // 60}:{seconds % 60:02d}"

    def _window_chip_text(self) -> str:
        """tool_capture_logs takes its own duration_seconds (from
        CaptureLogsScreen's minutes input) rather than the global
        settings.log_lookback_minutes every other tool uses — show whichever
        one this run is actually using instead of always showing the global
        setting regardless of tool. While a capture is still running, this
        counts down live instead of showing a static total (see _tick_timer)."""
        if self._tool_name == "tool_capture_logs":
            total = self._kwargs.get("duration_seconds")
            if total:
                if self._result_text is None and self._started_monotonic is not None:
                    import time

                    remaining = total - (time.monotonic() - self._started_monotonic)
                    return f"Capturing: {self._format_mmss(remaining)} remaining"
                return f"Capturing: {total // 60}m"
        return f"Lookback: {settings.log_lookback_minutes}m"

    def compose(self) -> ComposeResult:
        yield Header()
        yield breadcrumb_bar(3)
        target = settings.docker_host or "local (unix socket)"
        with bordered(VerticalScroll(classes="detail-box"), f"{step_prefix(3, TOTAL_STEPS)}{self._label}"):
            with Horizontal(classes="status-chips"):
                yield Static(f"● Connected: {target}", classes="status-chip connected")
                yield Static("… Running", id="status-chip", classes="status-chip")
                yield Static(self._tool_name, classes="status-chip")
                yield Static(self._window_chip_text(), id="window-chip", classes="status-chip")
            yield EventFeed(id="result-feed")
            yield Container(id="summary-box")
            yield Static("▶ Show raw JSON [j]", id="json-toggle", classes="hint-bar")
            yield EventFeed(id="raw-json-feed", classes="-hidden")
        yield Footer()

    def on_mount(self) -> None:
        import time

        self._log("running %s, kwargs=%s", self._tool_name, self._kwargs)
        self.query_one("#result-feed", EventFeed).write_event(
            "tool_call", EventFeed.escape(f"Running {self._tool_name}...")
        )
        self._started_monotonic = time.monotonic()
        if self._tool_name == "tool_capture_logs":
            # Ticks the "Capturing: Nm remaining" chip once a second so a
            # capture in progress shows live countdown instead of a static
            # total. Textual cancels widget-owned interval timers
            # automatically on unmount, so no explicit cleanup is needed.
            self.set_interval(1.0, self._tick_timer)
        self.run_worker(self._run_tool(), exclusive=True)

    def _tick_timer(self) -> None:
        if self._result_text is not None:
            return
        try:
            self.query_one("#window-chip", Static).update(self._window_chip_text())
        except Exception:
            pass  # screen no longer mounted (e.g. capture kept running in the background)

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

    def action_save_json(self) -> None:
        feed = self.query_one("#result-feed", EventFeed)
        if self._result_text is None:
            feed.write_event("info", EventFeed.escape("nothing to save yet"))
            return
        downloads = Path.home() / "Downloads"
        downloads.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        safe_name = re.sub(r"[^A-Za-z0-9_-]+", "_", self._tool_name)
        path = downloads / f"{safe_name}_{timestamp}.json"
        try:
            path.write_text(self._result_text)
            self._log("saved raw JSON to %s", path)
            feed.write_event("info", EventFeed.escape(f"saved to {path}"))
        except OSError as exc:
            logger.exception("tui: ResultScreen — failed saving JSON to %s", path)
            feed.write_event("info", EventFeed.escape(f"save failed: {exc}"))
            return
        self._save_raw_logs(downloads, timestamp, feed)

    def _save_raw_logs(self, downloads: Path, timestamp: str, feed: EventFeed) -> None:
        """tool_capture_logs's result carries a raw_logs field (per-container
        log lines) alongside the analysis — separate from action_save_json's
        JSON dump, teammates auditing a captured incident usually want the
        plain log text itself, not the JSON report. Written as one .log file
        per container next to the JSON, deliberately outside .cache/logs/
        since this tool doesn't participate in the Parquet cache by design."""
        if self._tool_name != "tool_capture_logs" or not self._result_dict:
            return
        raw_logs = self._result_dict.get("raw_logs")
        if not raw_logs:
            return
        for name, lines in raw_logs.items():
            safe_container = re.sub(r"[^A-Za-z0-9_-]+", "_", name)
            path = downloads / f"capture_{safe_container}_{timestamp}.log"
            try:
                path.write_text("\n".join(lines))
                self._log("saved raw logs for %s to %s", name, path)
                feed.write_event("info", EventFeed.escape(f"raw logs for {name} saved to {path}"))
            except OSError as exc:
                logger.exception("tui: ResultScreen — failed saving raw logs for %s to %s", name, path)
                feed.write_event("info", EventFeed.escape(f"raw log save failed for {name}: {exc}"))

    def _render_summary(self, result: dict) -> None:
        summarizer = RESULT_SUMMARIZERS.get(self._tool_name)
        if summarizer is None:
            return
        summarized = summarizer(result)
        if summarized is None:
            return
        headline, names = summarized
        box = self.query_one("#summary-box", Container)
        box.mount(Label(headline, classes="summary-headline"))
        if names:
            # Plain bullet list (no border/box per row, no gaps) — scales
            # cleanly to the ~20+ containers a busy host can have. Same
            # green/muted coloring as the "● Connected" status chip, just
            # without the chip's border/padding/spacing.
            name_list = Container(classes="name-list")
            box.mount(name_list)
            for name, status in names:
                running = status == "running"
                # Rich markup, not CSS — "$success"/"$text-muted" tokens
                # aren't valid Rich style names (only work in CSS), hence
                # plain Rich color names here (see memory:
                # textual-rich-markup-escape-interpolation for the class of
                # bug this avoids).
                color = "green" if running else "dim"
                name_list.mount(Static(f"[{color}]●[/{color}] {EventFeed.escape(name)}"))

    async def _run_tool(self) -> None:
        import asyncio
        import time

        feed = self.query_one("#result-feed", EventFeed)
        raw_feed = self.query_one("#raw-json-feed", EventFeed)
        status_chip = self.query_one("#status-chip", Static)
        fn = getattr(tools, self._tool_name)
        started = time.monotonic()
        try:
            result = await asyncio.to_thread(fn, **self._kwargs)
            elapsed = time.monotonic() - started
            text = json.dumps(result, indent=2, default=str)
            self._result_text = text
            self._result_dict = result if isinstance(result, dict) else None
            status = result.get("status") if isinstance(result, dict) else "?"
            self._log("%s completed in %.2fs, status=%s", self._tool_name, elapsed, status)
            feed.write_event("tool_done", EventFeed.escape(f"done ({status})"))
            raw_feed.write_event("tool_done", EventFeed.escape(text))
            status_chip.update("✓ Success" if status == "success" else f"✗ {status}")
            status_chip.set_class(status == "success", "status-chip-success")
            if isinstance(result, dict):
                self._render_summary(result)
            self.refresh_bindings()
        except Exception as exc:  # noqa: BLE001 - surface any failure in the UI
            elapsed = time.monotonic() - started
            logger.exception("tui: ResultScreen — %s raised after %.2fs", self._tool_name, elapsed)
            text = json.dumps({"status": "error", "error": str(exc)}, indent=2)
            self._result_text = text
            feed.write_event("tool_crashed", EventFeed.escape(str(exc)))
            raw_feed.write_event("tool_crashed", EventFeed.escape(text))
            status_chip.update("✗ Error")
            self.refresh_bindings()


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
    #connection-status { margin-bottom: 1; }
    #connection-status.-hidden { display: none; }
    ListView { height: auto; max-height: 16; border: round $accent; }
    #result-feed { height: auto; min-height: 3; max-height: 10; border: round $accent; }

    #container-select {
        border: round $accent; background: $panel;
        margin-top: 1; max-height: 16; padding: 0 1;
    }
    #container-select > .option-list--option-highlighted { background: $accent 15%; }
    #container-select > .selection-list--button { color: $text-muted; background: $panel-darken-1; }
    #container-select > .selection-list--button-selected { color: $accent; background: $accent 20%; text-style: bold; }
    #container-select > .selection-list--button-highlighted { color: $text-muted; background: $panel-darken-1; }
    #container-select > .selection-list--button-selected-highlighted { color: $accent; background: $accent 30%; text-style: bold; }
    #selection-status { border-bottom: none; margin-top: 1; color: $text-muted; }

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
    .name-list { height: auto; margin-top: 1; padding: 1 2; border: round $accent 50%; background: $panel; }
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
    # Textual owns the whole terminal via an alt-screen buffer — a stray
    # stderr log write (logger's default StreamHandler) lands directly on
    # top of the rendered UI instead of scrolling normally. File logging via
    # settings/config.py stays intact; only the console handler is dropped.
    logger.disable_console_logging()
    try:
        DockerTUIApp().run()
    except Exception:
        # Crashes here (e.g. a CSS parse error) happen during App
        # registration, before DockerTUIApp.on_mount ever runs its own
        # "app — started" log line — without this, such a crash leaves no
        # trace in the JSONL log at all, only an exception on stderr that's
        # invisible once Textual's alt-screen has taken over the terminal.
        logger.exception("tui: app — crashed before/during run()")
        raise


if __name__ == "__main__":
    run()
