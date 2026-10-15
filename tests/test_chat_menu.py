"""Keep the main console focused on chat and noncoaching navigation."""

import io

import pytest
from rich.console import Console

from gamescoach import cli
from gamescoach.chat import ChatCoach
from gamescoach.models import AthleteProfile
from gamescoach.storage import Store


@pytest.fixture
def menu_store(monkeypatch, tmp_path):
    monkeypatch.setenv("GAMESCOACH_DATA_DIR", str(tmp_path / "data"))
    for name in (
        "OPENAI_API_KEY", "GAMESCOACH_OPENAI_API_KEY", "GEMINI_API_KEY",
        "GOOGLE_API_KEY", "GAMESCOACH_GEMINI_API_KEY", "GAMESCOACH_COACH_NOTES_FILE",
    ):
        monkeypatch.delenv(name, raising=False)
    return Store()


def console_input(monkeypatch, answers):
    output = io.StringIO()
    console = Console(file=output, width=200, color_system=None, highlight=False)
    entries = iter(answers)
    monkeypatch.setattr(console, "input", lambda _prompt: next(entries))
    return console, output


def test_main_menu_offers_chat_and_supporting_tools(monkeypatch, menu_store):
    console, output = console_input(monkeypatch, ["q"])
    assert cli._interactive(console, menu_store, demo=True) == 0
    rendered = output.getvalue()
    for label in (
        "1 Chat", "2 Athlete profile", "3 History", "4 Import training file",
        "5 Methodology PDF library", "6 Settings", "q Quit",
    ):
        assert label in rendered
    for old_option in ("1 Workout", "2 Training plan", "3 Nutrition", "4 Competition", "8 Chat"):
        assert old_option not in rendered
    assert not menu_store.list_sessions()


@pytest.mark.parametrize("choice", ("", "1", "chat", " CHAT "))
def test_enter_and_chat_choices_open_the_conversation_menu(monkeypatch, menu_store, choice):
    routed = []
    monkeypatch.setattr(cli, "_chat_menu", lambda console, store, *, demo: routed.append((store, demo)))
    monkeypatch.setattr(cli, "create_session", lambda **kwargs: pytest.fail("Opening chat must not create a plan"))
    console, _ = console_input(monkeypatch, [choice, "q"])
    assert cli._interactive(console, menu_store, demo=False) == 0
    assert routed == [(menu_store, False)]
    assert not menu_store.list_sessions()


@pytest.mark.parametrize("choice,helper,extra", (
    ("2", "_edit_profile", []),
    ("profile", "_edit_profile", []),
    ("3", "_history", [""]),
    ("history", "_history", [""]),
    ("4", "_import_file", ["week.txt"]),
    ("import", "_import_file", ["week.txt"]),
    ("import-file", "_import_file", ["week.txt"]),
    ("5", "_show_library", [""]),
    ("library", "_show_library", [""]),
    ("6", "_doctor", []),
    ("settings", "_doctor", []),
    ("doctor", "_doctor", []),
))
def test_supporting_menu_choices_route_without_coaching(monkeypatch, menu_store, choice, helper, extra):
    calls = []
    monkeypatch.setattr(cli, helper, lambda *args, **kwargs: calls.append((args, kwargs)))
    monkeypatch.setattr(cli, "create_session", lambda **kwargs: pytest.fail("Navigation must not generate a plan"))
    monkeypatch.setattr(ChatCoach, "reply", lambda *args, **kwargs: pytest.fail("Navigation must not generate a reply"))
    console, _ = console_input(monkeypatch, [choice, *extra, "q"])
    assert cli._interactive(console, menu_store, demo=False) == 0
    assert len(calls) == 1
    if helper == "_doctor":
        assert not calls[0][1].get("check_api", False)
    if helper == "_import_file":
        assert calls[0][0][-1] == "week.txt"
    assert not menu_store.list_sessions()


def test_enter_starts_a_new_chat_and_quit_returns_to_main_menu(monkeypatch, menu_store):
    console, output = console_input(monkeypatch, ["", "CrossFit aerobic pacing", "/quit", "q"])
    assert cli._interactive(console, menu_store, demo=True) == 0
    saved, = menu_store.list_chats(demo=True)
    assert saved.request == "CrossFit aerobic pacing"
    assert len(saved.messages) == 2
    assert output.getvalue().count("1 Chat") == 2
    assert "Returning to the menu" in output.getvalue()


def test_enter_continues_latest_saved_chat_without_changing_its_id(monkeypatch, menu_store):
    original = ChatCoach(AthleteProfile(), [], demo=True).reply("CrossFit competition taper")
    menu_store.save_chat(original)
    console, output = console_input(monkeypatch, ["", "", "How should I adjust recovery?", "/quit", "q"])
    assert cli._interactive(console, menu_store, demo=True) == 0
    saved, = menu_store.list_chats(demo=True)
    assert saved.id == original.id
    assert saved.messages[:2] == original.messages
    assert len(saved.messages) == 4
    assert f"Resuming chat: {original.id}" in output.getvalue()


def test_settings_only_displays_local_configuration(monkeypatch, menu_store):
    monkeypatch.setattr(cli, "create_session", lambda **kwargs: pytest.fail("Settings must not generate a plan"))
    monkeypatch.setattr(ChatCoach, "reply", lambda *args, **kwargs: pytest.fail("Settings must not generate a reply"))
    monkeypatch.setattr(cli.OpenAIProvider, "generate", lambda *args, **kwargs: pytest.fail("Settings must not test the live API"))
    monkeypatch.setattr(cli.GeminiProvider, "generate", lambda *args, **kwargs: pytest.fail("Settings must not test the live API"))
    console, output = console_input(monkeypatch, ["6", "q"])
    assert cli._interactive(console, menu_store, demo=False) == 0
    assert "API configuration (presence only)" in output.getvalue()
    assert "environment variable presence only" in output.getvalue()
    assert not menu_store.list_sessions()


def test_unknown_and_retired_menu_numbers_keep_navigation_usable(monkeypatch, menu_store):
    monkeypatch.setattr(cli, "create_session", lambda **kwargs: pytest.fail("Invalid menu input must not generate a plan"))
    monkeypatch.setattr(ChatCoach, "reply", lambda *args, **kwargs: pytest.fail("Invalid menu input must not generate a reply"))
    console, output = console_input(monkeypatch, ["unknown", "8", "9", "10", "q"])
    assert cli._interactive(console, menu_store, demo=False) == 0
    assert output.getvalue().count("1 Chat") == 5
    assert not menu_store.list_sessions()
