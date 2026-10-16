"""Browser entry points and deliberate local memory controls."""

import io

import pytest
from rich.console import Console

from gamescoach import cli
from gamescoach.chat import ChatCoach
from gamescoach.engine import create_session
from gamescoach.memory import memory_settings, save_memory_settings
from gamescoach.models import AthleteProfile
from gamescoach.storage import Store


@pytest.fixture
def local_store(monkeypatch, tmp_path):
    monkeypatch.setenv("GAMESCOACH_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.delenv("GAMESCOACH_COACH_NOTES_FILE", raising=False)
    return Store()


def console_input(monkeypatch, messages):
    output = io.StringIO()
    console = Console(file=output, width=160, color_system=None, highlight=False)
    entries = iter(messages)
    monkeypatch.setattr(console, "input", lambda prompt: next(entries))
    return console, output


def test_gui_command_passes_explicit_options_without_making_api_calls(monkeypatch, local_store):
    from gamescoach import web

    called = []
    monkeypatch.setattr(web, "run_gui", lambda **kwargs: called.append(kwargs))
    assert cli.main(["gui", "--demo", "--no-browser", "--port", "8766"]) == 0
    assert called == [{"demo": True, "port": 8766, "open_browser": False}]
    assert not local_store.list_sessions()


def test_menu_can_launch_browser_chat_and_return(monkeypatch, local_store):
    from gamescoach import web

    called = []
    monkeypatch.setattr(web, "run_gui", lambda **kwargs: called.append(kwargs))
    console, output = console_input(monkeypatch, ["7", "q"])
    assert cli._interactive(console, local_store, demo=True) == 0
    assert called == [{"demo": True}]
    assert "7 Browser chat" in output.getvalue()


@pytest.mark.parametrize("confirmation", ["", "reset", "yes"])
def test_memory_reset_cancel_keeps_profile_and_history(monkeypatch, local_store, confirmation):
    profile = AthleteProfile(goals="CrossFit aerobic capacity")
    local_store.save_profile(profile)
    session = create_session("workout", "CrossFit intervals", profile, demo=True)
    local_store.save_session(session)
    console, output = console_input(monkeypatch, [confirmation])
    cli._reset_memory(console, local_store, "all")
    assert local_store.load_profile() == profile
    assert local_store.get_session(session.id) == session
    assert "cancelled" in output.getvalue()


def test_explicit_full_reset_command_disables_context_and_clears_retention(monkeypatch, local_store):
    local_store.save_profile(AthleteProfile(goals="CrossFit competition"))
    thread = ChatCoach(AthleteProfile(), [], demo=True).reply("CrossFit pacing")
    local_store.save_chat(thread)
    console, output = console_input(monkeypatch, ["RESET"])
    monkeypatch.setattr(cli, "Console", lambda **kwargs: console)
    assert cli.main(["memory", "reset", "--scope", "all"]) == 0
    assert local_store.load_profile() is None
    assert local_store.list_sessions() == []
    assert memory_settings(local_store) == {"auto_references": False, "coach_notes": False}
    assert "Memory reset complete" in output.getvalue()


def test_disabling_cli_auto_references_does_not_delete_history(monkeypatch, local_store):
    plan = create_session("workout", "CrossFit week", AthleteProfile(), demo=True)
    local_store.save_session(plan)
    console, output = console_input(monkeypatch, [])
    monkeypatch.setattr(cli, "Console", lambda **kwargs: console)
    assert cli.main(["memory", "settings", "--auto-references", "off"]) == 0
    console, _ = console_input(monkeypatch, ["A fresh CrossFit question", "/quit"])
    cli._chat(console, local_store, demo=True)
    chat, = local_store.list_chats(demo=True)
    assert chat.context_ids == []
    assert local_store.get_session(plan.id) == plan
    assert "Automatic recent-history references in CLI chats: off" in output.getvalue()


def test_fresh_chat_clears_references_without_deleting_existing_sessions(monkeypatch, local_store):
    plan = create_session("workout", "CrossFit training week", AthleteProfile(), demo=True)
    local_store.save_session(plan)
    console, _ = console_input(monkeypatch, ["First CrossFit question", "/fresh", "A new CrossFit training path", "/quit"])
    cli._chat(console, local_store, demo=True)
    chats = local_store.list_chats(demo=True)
    assert len(chats) == 2
    first, = [chat for chat in chats if chat.request == "First CrossFit question"]
    fresh, = [chat for chat in chats if chat.request == "A new CrossFit training path"]
    assert first.context_ids == [plan.id]
    assert fresh.context_ids == []
    assert local_store.get_session(plan.id) == plan


def test_memory_status_and_settings_do_not_generate_coaching(monkeypatch, local_store):
    monkeypatch.setattr(ChatCoach, "reply", lambda *args, **kwargs: pytest.fail("Memory commands must remain local"))
    console, output = console_input(monkeypatch, [])
    monkeypatch.setattr(cli, "Console", lambda **kwargs: console)
    assert cli.main(["memory", "status"]) == 0
    assert "Local coaching memory" in output.getvalue()
    assert "0 chats" in output.getvalue()
