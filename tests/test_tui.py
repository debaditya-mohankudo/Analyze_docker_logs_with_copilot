"""
Unit tests for tui.py — Textual TUI flow (connect screen -> window -> menu
-> result).

Docker-free: tool_* calls are monkeypatched so these tests never touch a real
Docker daemon, local or remote. No buttons in this UI — everything is driven
by key bindings (`l` for local, digit keys for the time window, Enter to
submit an Input, Escape to go back), matching SeniorDevAgent's ConceptBrowser
TUI convention.
"""

import asyncio

import pytest

from docker_log_analyzer import tools
from docker_log_analyzer.config import settings
from docker_log_analyzer.tui import (
    ConnectScreen,
    ContainerNameScreen,
    DockerTUIApp,
    MenuScreen,
    PROMPT_CATEGORIES,
    PROMPTS,
    ResultScreen,
    WindowScreen,
)

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _reset_docker_host():
    """Each test starts from a clean docker_host/log_lookback_minutes; restore afterwards."""
    original_host = settings.docker_host
    original_lookback = settings.log_lookback_minutes
    yield
    settings.docker_host = original_host
    settings.log_lookback_minutes = original_lookback


@pytest.fixture(autouse=True)
def _fake_docker_available(monkeypatch):
    """ConnectScreen.action_use_local/action_connect_remote now run a real
    connectivity check (tools.tool_list_containers) via a worker before
    advancing past Connect — stub a fast success response by default so
    these stay Docker-free/deterministic per CLAUDE.md's unit-test rules.
    A test that needs a different result (e.g. exercising the failure path)
    just calls monkeypatch.setattr again itself, which overrides this."""
    monkeypatch.setattr(
        tools, "tool_list_containers",
        lambda: {"status": "success", "containers": [], "count": 0},
    )


async def _await_connection_test(pilot) -> None:
    """ConnectScreen's connection-test worker does asyncio.to_thread(...) then
    (on success) `await asyncio.sleep(0.6)` before pushing WindowScreen —
    callers must wait out that pause before asserting the screen changed."""
    await pilot.pause()
    await asyncio.sleep(0.8)
    await pilot.pause()


async def _connect_local_and_pick_window(pilot, minutes_key: str = "2") -> None:
    """Shared helper: press 'l' for local Docker, then pick a time window
    (default '2' = 10 min), landing on MenuScreen."""
    await pilot.press("l")
    await _await_connection_test(pilot)
    await pilot.press(minutes_key)
    await pilot.pause()


def _feed_text(screen, selector: str = "#raw-json-feed") -> str:
    """Join all rendered lines of an EventFeed into one string, since RichLog
    has no single .text/.render() like Static does. Defaults to the
    collapsible raw-JSON feed — the short #result-feed only ever has the
    "Running..."/"done (status)" lines, not the actual tool output."""
    feed = screen.query_one(selector)
    return "\n".join(strip.text for strip in feed.lines)


def _select_prompt(screen, prompt_index: int) -> None:
    """MenuScreen's ListView interleaves category-header rows with the real
    "prompt-i" rows (see MenuScreen._build_items), so the ListView's
    positional .index no longer matches PROMPTS' flat index — look up the
    real position by id instead."""
    list_view = screen.query_one("#prompt-list")
    target_id = f"prompt-{prompt_index}"
    position = next(i for i, item in enumerate(list_view.children) if item.id == target_id)
    list_view.index = position


async def test_clicking_local_daemon_card_advances_to_window_screen():
    """Regression test: cards must respond to mouse click, not just their key
    binding — every card in this app is a ClickableCard, not a plain
    Container, so `l`/`r`/1/2/3 and a click both trigger the same action."""
    app = DockerTUIApp()
    async with app.run_test() as pilot:
        card = app.screen.query(".daemon-card").first()
        await pilot.click(card)
        await _await_connection_test(pilot)
        assert isinstance(app.screen, WindowScreen)
        assert settings.docker_host == ""


async def test_clicking_window_card_sets_lookback_and_advances():
    app = DockerTUIApp()
    async with app.run_test() as pilot:
        await pilot.press("l")
        await _await_connection_test(pilot)
        cards = app.screen.query(".window-card")
        await pilot.click(cards[2])  # "30 min"
        await pilot.pause()
        assert isinstance(app.screen, MenuScreen)
        assert settings.log_lookback_minutes == 30


async def test_local_connect_goes_to_window_screen():
    app = DockerTUIApp()
    async with app.run_test() as pilot:
        assert isinstance(app.screen, ConnectScreen)
        await pilot.press("l")
        await _await_connection_test(pilot)
        assert isinstance(app.screen, WindowScreen)
        assert settings.docker_host == ""


async def test_window_screen_choices_set_log_lookback_minutes():
    app = DockerTUIApp()
    async with app.run_test() as pilot:
        await pilot.press("l")
        await _await_connection_test(pilot)
        assert isinstance(app.screen, WindowScreen)

        await pilot.press("3")  # 30 min
        await pilot.pause()
        assert isinstance(app.screen, MenuScreen)
        assert settings.log_lookback_minutes == 30


async def test_remote_connect_builds_ssh_docker_host_with_default_user(monkeypatch):
    monkeypatch.setattr("docker_log_analyzer.tui.getpass.getuser", lambda: "devuser")

    app = DockerTUIApp()
    async with app.run_test() as pilot:
        await pilot.press("r")
        await pilot.press(*"10.0.0.5")
        await pilot.press("enter")
        await _await_connection_test(pilot)

        assert isinstance(app.screen, WindowScreen)
        assert settings.docker_host == "ssh://devuser@10.0.0.5"


async def test_remote_connect_respects_explicit_user():
    app = DockerTUIApp()
    async with app.run_test() as pilot:
        await pilot.press("r")
        await pilot.press(*"ops@10.0.0.9")
        await pilot.press("enter")
        await pilot.pause()

        assert settings.docker_host == "ssh://ops@10.0.0.9"


async def test_remote_connect_requires_host():
    app = DockerTUIApp()
    async with app.run_test() as pilot:
        await pilot.press("r")
        await pilot.press("enter")
        await pilot.pause()

        # No host entered -> stays on ConnectScreen with an error shown.
        assert isinstance(app.screen, ConnectScreen)
        error = app.screen.query_one("#connect-error")
        assert "Enter a remote IP" in str(error.render())


async def test_escape_from_remote_input_unfocuses_not_pops_screen():
    """Regression test: escape while ConnectScreen's remote-host input is
    focused must back out of the input, not fall through to the app-level
    escape->pop_screen binding — ConnectScreen is the root screen, so
    popping it lands on Textual's empty implicit default screen (blank,
    unrecoverable — reads as a crash even though the process stays alive)."""
    app = DockerTUIApp()
    async with app.run_test() as pilot:
        await pilot.press("r")
        await pilot.pause()
        assert app.screen.focused is not None  # remote-host input focused

        await pilot.press("escape")
        await pilot.pause()
        assert isinstance(app.screen, ConnectScreen)
        assert len(app.screen_stack) > 1  # still has a real screen pushed, not popped to default
        assert app.is_running


async def test_escape_with_nothing_focused_on_connect_screen_quits():
    app = DockerTUIApp()
    async with app.run_test() as pilot:
        assert app.screen.focused is None
        await pilot.press("escape")
        await pilot.pause()
        assert not app.is_running


def test_prompts_menu_has_entries():
    assert len(PROMPTS) > 0
    for label, tool_name, required_arg, category, icon in PROMPTS:
        assert hasattr(tools, tool_name), f"{tool_name} missing from tools.py"
        assert required_arg in (
            None, "container_name", "multi_container_names", "multi_container_and_duration",
        )
        assert category in PROMPT_CATEGORIES
        assert icon


async def test_menu_lists_all_prompts():
    app = DockerTUIApp()
    async with app.run_test() as pilot:
        await _connect_local_and_pick_window(pilot)
        # .gc-item excludes the interspersed .category-header rows.
        items = app.screen.query("ListView > ListItem.gc-item")
        assert len(items) > 0
        assert len(items) == len(PROMPTS)


async def test_menu_groups_prompts_into_categories():
    app = DockerTUIApp()
    async with app.run_test() as pilot:
        await _connect_local_and_pick_window(pilot)
        headers = app.screen.query("ListView > ListItem.category-header")
        assert len(headers) == len(PROMPT_CATEGORIES)
        for header in headers:
            assert header.disabled


async def test_selecting_container_scoped_prompt_pushes_name_screen():
    app = DockerTUIApp()
    async with app.run_test() as pilot:
        await _connect_local_and_pick_window(pilot)

        # "Last errors for a container" is the only entry requiring container_name.
        last_index = next(i for i, p in enumerate(PROMPTS) if p[2] == "container_name")
        _select_prompt(app.screen, last_index)
        await pilot.press("enter")
        await pilot.pause()

        assert isinstance(app.screen, ContainerNameScreen)


async def test_selecting_no_arg_prompt_runs_tool_and_shows_result(monkeypatch):
    monkeypatch.setattr(
        tools,
        "tool_list_containers",
        lambda: {"status": "success", "containers": [], "count": 0},
    )

    app = DockerTUIApp()
    async with app.run_test() as pilot:
        await _connect_local_and_pick_window(pilot)

        _select_prompt(app.screen, 0)  # "List running containers"
        await pilot.press("enter")
        await pilot.pause()

        assert isinstance(app.screen, ResultScreen)
        await asyncio.sleep(0.2)
        await pilot.pause()
        await pilot.press("j")  # reveal the raw-JSON feed (display:none until toggled)
        await pilot.pause()
        body = _feed_text(app.screen)
        assert '"status": "success"' in body
        assert '"count": 0' in body


async def test_back_from_result_lands_on_menu_not_home(monkeypatch):
    """Regression test: escape from ResultScreen must pop exactly one screen."""
    monkeypatch.setattr(
        tools,
        "tool_list_containers",
        lambda: {"status": "success", "containers": [], "count": 0},
    )

    app = DockerTUIApp()
    async with app.run_test() as pilot:
        await _connect_local_and_pick_window(pilot)
        _select_prompt(app.screen, 0)
        await pilot.press("enter")
        await pilot.pause()
        await asyncio.sleep(0.2)
        await pilot.pause()

        await pilot.press("escape")
        await pilot.pause()
        assert isinstance(app.screen, MenuScreen)


async def test_every_action_is_logged(monkeypatch, caplog):
    """Regression test: connect, window choice, prompt selection, tool run,
    and back should each produce a log line — see task follow-up 'make sure
    every action is logged'."""
    monkeypatch.setattr(
        tools,
        "tool_list_containers",
        lambda: {"status": "success", "containers": [], "count": 0},
    )

    import logging

    caplog.set_level(logging.INFO, logger="docker-log-analyzer")

    app = DockerTUIApp()
    async with app.run_test() as pilot:
        await _connect_local_and_pick_window(pilot, minutes_key="1")
        _select_prompt(app.screen, 0)
        await pilot.press("enter")
        await pilot.pause()
        await asyncio.sleep(0.2)
        await pilot.pause()
        await pilot.press("escape")
        await pilot.pause()

    messages = "\n".join(caplog.messages)
    assert "app" in messages and "started" in messages
    assert "chose local Docker" in messages
    assert "chose 5 min log lookback window" in messages
    assert "selected prompt 'List running containers'" in messages
    assert "running tool_list_containers" in messages
    assert "tool_list_containers completed" in messages
    assert "back — popping ResultScreen" in messages


async def test_menu_status_chips_show_connection_and_window():
    app = DockerTUIApp()
    async with app.run_test() as pilot:
        await _connect_local_and_pick_window(pilot, minutes_key="1")  # 5 min
        chips = app.screen.query(".status-chip")
        chip_text = " ".join(str(c.render()) for c in chips)
        assert "local (unix socket)" in chip_text
        assert "Window: 5 min" in chip_text


async def test_container_name_screen_shows_error_when_no_containers(monkeypatch):
    """ContainerNameScreen picks from tool_list_containers (a ListView, not
    free text — see task:a638ca4b) — when there's nothing to pick from, it
    must show an error rather than a silently empty list."""
    monkeypatch.setattr(
        tools, "tool_list_containers",
        lambda: {"status": "success", "containers": [], "count": 0},
    )

    app = DockerTUIApp()
    async with app.run_test() as pilot:
        screen = ContainerNameScreen("Last errors for a container", "tool_get_last_errors")
        await app.push_screen(screen)
        await pilot.pause()
        await asyncio.sleep(0.2)
        await pilot.pause()

        assert isinstance(app.screen, ContainerNameScreen)
        error = app.screen.query_one("#connect-error")
        assert "No containers found" in str(error.render())


async def test_container_name_screen_runs_tool_with_name(monkeypatch):
    captured = {}

    monkeypatch.setattr(
        tools, "tool_list_containers",
        lambda: {
            "status": "success",
            "containers": [{"name": "web-app", "status": "running"}],
            "count": 1,
        },
    )

    def _fake_last_errors(container_name):
        captured["container_name"] = container_name
        return {"status": "success", "container": container_name, "errors": []}

    monkeypatch.setattr(tools, "tool_get_last_errors", _fake_last_errors)

    app = DockerTUIApp()
    async with app.run_test() as pilot:
        screen = ContainerNameScreen("Last errors for a container", "tool_get_last_errors")
        await app.push_screen(screen)
        await pilot.pause()
        await asyncio.sleep(0.2)
        await pilot.pause()

        # Only one container loaded -> it's the highlighted row; Enter
        # selects it (ListView's own binding -> on_list_view_selected).
        await pilot.press("enter")
        await pilot.pause()

        assert isinstance(app.screen, ResultScreen)
        await asyncio.sleep(0.2)
        await pilot.pause()
        assert captured["container_name"] == "web-app"


async def test_result_screen_renders_array_heavy_json_without_crashing(monkeypatch):
    """Regression test: RichLog with markup=True parses written content as
    Rich markup, and JSON arrays/brackets (e.g. "errors": [...]) or literal
    "[" in a log message crash it with MarkupError unless escaped first.
    tui.py must escape via EventFeed.escape() before EventFeed.write_event()."""
    monkeypatch.setattr(
        tools,
        "tool_get_last_errors",
        lambda container_name, tail=200, limit=10: {
            "status": "success",
            "container": container_name,
            "errors_found": 2,
            "errors": [
                {"timestamp": "2026-07-11T09:16:44Z", "level": "fatal", "message": "[oops] pool exhausted"},
                {"timestamp": "2026-07-11T09:17:02Z", "level": "error", "message": "conn refused"},
            ],
        },
    )

    app = DockerTUIApp()
    async with app.run_test() as pilot:
        screen = ResultScreen(
            "Last errors for a container",
            "tool_get_last_errors",
            {"container_name": "test-web-app"},
        )
        await app.push_screen(screen)
        await pilot.pause()
        await asyncio.sleep(0.2)
        await pilot.pause()
        await pilot.press("j")
        await pilot.pause()
        body = _feed_text(app.screen)
        assert "errors_found" in body
        assert "pool exhausted" in body


async def test_result_screen_surfaces_tool_exceptions(monkeypatch):
    def _boom():
        raise RuntimeError("Cannot connect to Docker daemon")

    monkeypatch.setattr(tools, "tool_list_containers", _boom)

    app = DockerTUIApp()
    async with app.run_test() as pilot:
        screen = ResultScreen("List running containers", "tool_list_containers", {})
        await app.push_screen(screen)
        await pilot.pause()
        await asyncio.sleep(0.2)
        await pilot.pause()
        await pilot.press("j")
        await pilot.pause()
        body = _feed_text(app.screen)
        assert "Cannot connect to Docker daemon" in body


async def test_screens_have_border_titles():
    """House-style regression: every bordered panel must carry a border_title
    (set via tui_widgets.bordered()), not an unlabeled box."""
    app = DockerTUIApp()
    async with app.run_test() as pilot:
        assert app.screen.query_one(".connect-box").border_title
        await pilot.press("l")
        await _await_connection_test(pilot)
        assert isinstance(app.screen, WindowScreen)
        assert app.screen.query_one(".connect-box").border_title
        await pilot.press("2")
        await pilot.pause()
        assert app.screen.query_one(".detail-box").border_title
        assert app.screen.query_one("#prompt-list").border_title


async def test_result_screen_border_title_has_step_prefix():
    app = DockerTUIApp()
    async with app.run_test() as pilot:
        screen = ResultScreen("List running containers", "tool_list_containers", {})
        await app.push_screen(screen)
        await pilot.pause()
        box = app.screen.query_one(".detail-box")
        assert box.border_title.startswith("[4/4]")


async def test_no_buttons_anywhere():
    """Matches SeniorDevAgent's ConceptBrowser convention: key bindings only."""
    from textual.widgets import Button

    app = DockerTUIApp()
    async with app.run_test() as pilot:
        assert not app.screen.query(Button)
        await _connect_local_and_pick_window(pilot)
        assert not app.screen.query(Button)


if __name__ == "__main__":
    # Quick manual smoke check: `python tests/test_tui.py` runs the same
    # local-connect -> menu -> list-containers flow pytest exercises above,
    # printing the result instead of asserting on it. Useful for eyeballing
    # the TUI's actual output without needing a live Docker daemon.
    async def _manual_check() -> None:
        from unittest.mock import patch

        with patch.object(
            tools,
            "tool_list_containers",
            lambda: {"status": "success", "containers": [], "count": 0},
        ):
            app = DockerTUIApp()
            async with app.run_test() as pilot:
                await _connect_local_and_pick_window(pilot)
                list_view = app.screen.query_one("#prompt-list")
                list_view.index = 0
                await pilot.press("enter")
                await pilot.pause()
                await asyncio.sleep(0.2)
                await pilot.pause()
                print(_feed_text(app.screen))

    asyncio.run(_manual_check())
