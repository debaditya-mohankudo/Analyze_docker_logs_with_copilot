"""
Unit tests for tui.py — Textual TUI flow (connect screen -> menu -> result).

Docker-free: tool_* calls are monkeypatched so these tests never touch a real
Docker daemon, local or remote. No buttons in this UI — everything is driven
by key bindings (`l` for local, Enter to submit an Input, Escape to go back),
matching SeniorDevAgent's ConceptBrowser TUI convention.
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
    PROMPTS,
    ResultScreen,
)

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _reset_docker_host():
    """Each test starts from a clean docker_host; restore it afterwards."""
    original = settings.docker_host
    yield
    settings.docker_host = original


def _feed_text(screen) -> str:
    """Join all rendered lines of the #result-feed EventFeed into one string,
    since RichLog has no single .text/.render() like Static does."""
    feed = screen.query_one("#result-feed")
    return "\n".join(strip.text for strip in feed.lines)


async def test_local_connect_goes_straight_to_menu():
    app = DockerTUIApp()
    async with app.run_test() as pilot:
        assert isinstance(app.screen, ConnectScreen)
        await pilot.press("l")
        await pilot.pause()
        assert isinstance(app.screen, MenuScreen)
        assert settings.docker_host == ""


async def test_remote_connect_builds_ssh_docker_host_with_default_user(monkeypatch):
    monkeypatch.setattr("docker_log_analyzer.tui.getpass.getuser", lambda: "devuser")

    app = DockerTUIApp()
    async with app.run_test() as pilot:
        await pilot.press("r")
        await pilot.press(*"10.0.0.5")
        await pilot.press("enter")
        await pilot.pause()

        assert isinstance(app.screen, MenuScreen)
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


def test_prompts_menu_has_exactly_eight_entries():
    assert len(PROMPTS) == 8
    for label, tool_name, required_arg in PROMPTS:
        assert hasattr(tools, tool_name), f"{tool_name} missing from tools.py"
        assert required_arg in (None, "container_name")


async def test_menu_lists_all_eight_prompts():
    app = DockerTUIApp()
    async with app.run_test() as pilot:
        await pilot.press("l")
        await pilot.pause()
        items = app.screen.query("ListView > ListItem")
        assert len(items) == 8


async def test_selecting_container_scoped_prompt_pushes_name_screen():
    app = DockerTUIApp()
    async with app.run_test() as pilot:
        await pilot.press("l")
        await pilot.pause()

        # "Last errors for a container" is the only entry requiring container_name.
        last_index = next(i for i, p in enumerate(PROMPTS) if p[2] == "container_name")
        list_view = app.screen.query_one("#prompt-list")
        list_view.index = last_index
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
        await pilot.press("l")
        await pilot.pause()

        list_view = app.screen.query_one("#prompt-list")
        list_view.index = 0  # "List running containers"
        await pilot.press("enter")
        await pilot.pause()

        assert isinstance(app.screen, ResultScreen)
        await asyncio.sleep(0.2)
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
        await pilot.press("l")
        await pilot.pause()
        list_view = app.screen.query_one("#prompt-list")
        list_view.index = 0
        await pilot.press("enter")
        await pilot.pause()
        await asyncio.sleep(0.2)
        await pilot.pause()

        await pilot.press("escape")
        await pilot.pause()
        assert isinstance(app.screen, MenuScreen)


async def test_container_name_screen_requires_input():
    app = DockerTUIApp()
    async with app.run_test() as pilot:
        screen = ContainerNameScreen("Last errors for a container", "tool_get_last_errors")
        await app.push_screen(screen)
        await pilot.pause()

        name_input = app.screen.query_one("#container-name")
        name_input.focus()
        await pilot.press("enter")
        await pilot.pause()

        # Empty name -> stays on the same screen with an error.
        assert isinstance(app.screen, ContainerNameScreen)
        error = app.screen.query_one("#connect-error")
        assert "Container name required" in str(error.render())


async def test_container_name_screen_runs_tool_with_name(monkeypatch):
    captured = {}

    def _fake_last_errors(container_name):
        captured["container_name"] = container_name
        return {"status": "success", "container": container_name, "errors": []}

    monkeypatch.setattr(tools, "tool_get_last_errors", _fake_last_errors)

    app = DockerTUIApp()
    async with app.run_test() as pilot:
        screen = ContainerNameScreen("Last errors for a container", "tool_get_last_errors")
        await app.push_screen(screen)
        await pilot.pause()

        name_input = app.screen.query_one("#container-name")
        name_input.focus()
        await pilot.press(*"web-app")
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
        body = _feed_text(app.screen)
        assert "Cannot connect to Docker daemon" in body


async def test_screens_have_border_titles():
    """House-style regression: every bordered panel must carry a border_title
    (set via tui_widgets.bordered()), not an unlabeled box."""
    app = DockerTUIApp()
    async with app.run_test() as pilot:
        assert app.screen.query_one(".modal-box").border_title
        await pilot.press("l")
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
        assert box.border_title.startswith("[3/3]")


async def test_no_buttons_anywhere():
    """Matches SeniorDevAgent's ConceptBrowser convention: key bindings only."""
    from textual.widgets import Button

    app = DockerTUIApp()
    async with app.run_test() as pilot:
        assert not app.screen.query(Button)
        await pilot.press("l")
        await pilot.pause()
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
                await pilot.press("l")
                await pilot.pause()
                list_view = app.screen.query_one("#prompt-list")
                list_view.index = 0
                await pilot.press("enter")
                await pilot.pause()
                await asyncio.sleep(0.2)
                await pilot.pause()
                print(_feed_text(app.screen))

    asyncio.run(_manual_check())
