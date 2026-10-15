"""The reply viewer is local, preserves content, and safely returns to chat."""

import io

import pytest
from rich.console import Console

from gamescoach import cli
from gamescoach.chat import ChatCoach
from gamescoach.models import AthleteProfile


@pytest.fixture
def coach(monkeypatch, tmp_path):
    monkeypatch.setenv("GAMESCOACH_DATA_DIR", str(tmp_path / "data"))
    result = ChatCoach(AthleteProfile(), [], demo=True)
    result.reply("CrossFit aerobic intervals")

    def no_request(*args, **kwargs):
        pytest.fail("Viewing a reply must not generate another coaching request")

    monkeypatch.setattr(result, "reply", no_request)
    return result


@pytest.mark.parametrize("no_color", [False, True])
def test_terminal_viewer_pipes_full_reply_to_less_and_honors_color_policy(monkeypatch, coach, no_color):
    output = io.StringIO()
    console = Console(file=output, force_terminal=True, color_system="standard", no_color=no_color, width=120)
    monkeypatch.setattr(cli.shutil, "which", lambda name: "/usr/bin/less")
    calls = []
    monkeypatch.setattr(cli.subprocess, "run", lambda args, **kwargs: calls.append((args, kwargs)))
    before = coach.session.to_dict()
    cli._view_reply(console, coach)
    assert len(calls) == 1
    args, kwargs = calls[0]
    assert args == ["less", "-R", "-X"]
    assert not kwargs.get("shell")
    assert "This is a static sample of the chat interface." in kwargs["input"]
    assert ("\x1b[" in kwargs["input"]) is not no_color
    assert "q returns to chat" in output.getvalue()
    assert coach.session.to_dict() == before


def test_missing_pager_displays_reply_without_starting_a_process(monkeypatch, coach):
    output = io.StringIO()
    console = Console(file=output, force_terminal=True, width=120)
    monkeypatch.setattr(cli.shutil, "which", lambda name: None)

    def unexpected(*args, **kwargs):
        pytest.fail("No pager is installed")

    monkeypatch.setattr(cli.subprocess, "run", unexpected)
    cli._view_reply(console, coach)
    assert "Install 'less'" in output.getvalue()
    assert "This is a static sample of the chat interface." in output.getvalue()


def test_interrupting_viewer_returns_to_chat_with_saved_thread_unchanged(monkeypatch, coach):
    output = io.StringIO()
    console = Console(file=output, force_terminal=True, width=120)
    monkeypatch.setattr(cli.shutil, "which", lambda name: "/usr/bin/less")

    def interrupted(*args, **kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr(cli.subprocess, "run", interrupted)
    before = coach.session.to_dict()
    cli._view_reply(console, coach)
    console.print("Ready for another coaching question.")
    assert "Ready for another coaching question." in output.getvalue()
    assert coach.session.to_dict() == before


def test_pager_startup_failure_restores_normal_console_output(monkeypatch, coach):
    output = io.StringIO()
    console = Console(file=output, force_terminal=True, width=120)
    monkeypatch.setattr(cli.shutil, "which", lambda name: "/usr/bin/less")

    def unavailable(*args, **kwargs):
        raise FileNotFoundError("Pager disappeared")

    monkeypatch.setattr(cli.subprocess, "run", unavailable)
    cli._view_reply(console, coach)
    console.print("Ready for another coaching question.")
    assert "Could not open the scrollable viewer" in output.getvalue()
    assert "This is a static sample of the chat interface." in output.getvalue()
    assert "Ready for another coaching question." in output.getvalue()
