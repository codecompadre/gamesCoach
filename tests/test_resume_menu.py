"""Resume coaching conversations through the menu without shell commands or IDs."""

from dataclasses import replace
import io
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest
from rich.console import Console

from gamescoach import cli
from gamescoach.chat import ChatCoach
from gamescoach.engine import create_session
from gamescoach.models import AthleteProfile
from gamescoach.storage import Store


@pytest.fixture
def menu_env(tmp_path):
    env = os.environ.copy()
    for name in (
        "OPENAI_API_KEY", "GAMESCOACH_OPENAI_API_KEY", "GEMINI_API_KEY",
        "GOOGLE_API_KEY", "GAMESCOACH_GEMINI_API_KEY", "OPENAI_MODEL",
        "GEMINI_MODEL", "GAMESCOACH_COACH_NOTES_FILE",
    ):
        env.pop(name, None)
    env["GAMESCOACH_DATA_DIR"] = str(tmp_path / "data")
    source = str(Path(__file__).resolve().parents[1] / "src")
    env["PYTHONPATH"] = source + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    return env


def run_menu(env, input_text, *, demo=True):
    return subprocess.run(
        [sys.executable, "-m", "gamescoach", *(["--demo"] if demo else [])],
        env=env, input=input_text, capture_output=True, text=True,
        encoding="utf-8", timeout=15,
    )


def saved_entries(env):
    return [json.loads(path.read_text(encoding="utf-8")) for path in (Path(env["GAMESCOACH_DATA_DIR"]) / "sessions").glob("*.json")]


def save_chat(env, request, *, demo=True, order=1):
    session = ChatCoach(AthleteProfile(), [], demo=True).reply(request)
    session = replace(session, demo=demo)
    store = Store(env["GAMESCOACH_DATA_DIR"])
    store.save_chat(session)
    path = store.root / "sessions" / f"{session.id}.json"
    os.utime(path, ns=(order * 1_000_000_000, order * 1_000_000_000))
    return session


def test_reopen_menu_enter_resumes_same_complete_conversation(menu_env):
    first = run_menu(menu_env, "1\nCrossFit aerobic capacity\nHow should I progress?\n/quit\nq\n")
    assert first.returncode == 0, first.stdout + first.stderr
    original, = saved_entries(menu_env)
    second = run_menu(menu_env, "1\n\nCan I retest next month?\n/ quit\nq\n")
    assert second.returncode == 0, second.stdout + second.stderr
    assert "Continue latest conversation (Enter)" in second.stdout
    assert "Resuming chat:" in second.stdout and original["id"] in second.stdout
    assert "CrossFit aerobic capacity" in second.stdout and "How should I progress?" in second.stdout
    updated, = saved_entries(menu_env)
    assert updated["id"] == original["id"]
    assert updated["messages"][:4] == original["messages"]
    assert [message["content"] for message in updated["messages"] if message["role"] == "user"] == [
        "CrossFit aerobic capacity", "How should I progress?", "Can I retest next month?",
    ]


def test_new_menu_option_preserves_old_conversation_and_saves_new_id(menu_env):
    previous = save_chat(menu_env, "CrossFit engine development")
    result = run_menu(menu_env, "1\n2\nCrossFit competition taper\n/quit\nq\n")
    assert result.returncode == 0, result.stdout + result.stderr
    chats = saved_entries(menu_env)
    assert len(chats) == 2 and len({chat["id"] for chat in chats}) == 2
    assert {chat["request"] for chat in chats} == {previous.request, "CrossFit competition taper"}
    original, = [chat for chat in chats if chat["id"] == previous.id]
    assert original == previous.to_dict()


def test_numbered_picker_resumes_older_chat_and_retries_invalid_choices(menu_env):
    older = save_chat(menu_env, "CrossFit intervals", order=1)
    latest = save_chat(menu_env, "CrossFit nutrition", order=2)
    result = run_menu(menu_env, "1\nunknown\n3\n\n0\n99\ninvalid\n2\nWhat pace should I use?\n/quit\nq\n")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Choose 1–3" in result.stdout
    assert result.stdout.count("Choose a conversation number") == 4
    assert "Saved conversations" in result.stdout
    assert "CrossFit intervals" in result.stdout and "CrossFit nutrition" in result.stdout
    assert f"Resuming chat: {older.id}" in result.stdout
    chats = {chat["id"]: chat for chat in saved_entries(menu_env)}
    assert len(chats[older.id]["messages"]) == 4
    assert chats[latest.id] == latest.to_dict()


@pytest.mark.parametrize("back", ("b", "/quit", "/ quit", "/BACK"))
@pytest.mark.parametrize("picker", (False, True))
def test_back_from_chat_choices_returns_to_main_menu_without_writes(menu_env, back, picker):
    original = save_chat(menu_env, "CrossFit pacing")
    prefix = "1\n3\n" if picker else "1\n"
    result = run_menu(menu_env, prefix + back + "\nq\n")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Returning to the menu" in result.stdout
    assert result.stdout.count("1 Chat") == 2
    assert saved_entries(menu_env) == [original.to_dict()]
    assert "Resuming chat:" not in result.stdout


def test_first_chat_starts_directly_when_only_plans_are_saved(menu_env):
    store = Store(menu_env["GAMESCOACH_DATA_DIR"])
    store.save_session(create_session("training", "CrossFit base training", AthleteProfile(), rounds=1, demo=True))
    result = run_menu(menu_env, "1\nCrossFit rowing pacing\n/quit\nq\n")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Continue latest conversation" not in result.stdout
    assert "Chat option" not in result.stdout
    assert {entry["kind"] for entry in saved_entries(menu_env)} == {"training", "chat"}


def test_many_newer_plans_do_not_hide_saved_conversation(menu_env):
    original = save_chat(menu_env, "CrossFit aerobic progression")
    store = Store(menu_env["GAMESCOACH_DATA_DIR"])
    for index in range(12):
        store.save_session(create_session("workout", f"CrossFit workout {index}", AthleteProfile(), rounds=1, demo=True))
    result = run_menu(menu_env, "1\n\n/quit\nq\n")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Continue latest conversation" in result.stdout
    assert f"Resuming chat: {original.id}" in result.stdout
    assert len(saved_entries(menu_env)) == 13


@pytest.mark.parametrize("demo", (True, False))
def test_resume_picker_only_offers_chats_in_current_mode(menu_env, demo):
    matching = save_chat(menu_env, "Matching CrossFit conversation", demo=demo, order=1)
    other = save_chat(menu_env, "Other mode CrossFit conversation", demo=not demo, order=2)
    result = run_menu(menu_env, "1\n3\n1\n/quit\nq\n", demo=demo)
    assert result.returncode == 0, result.stdout + result.stderr
    assert f"Resuming chat: {matching.id}" in result.stdout
    assert other.id not in result.stdout and other.request not in result.stdout
    assert "Live coaching needs" not in result.stdout
    assert len(saved_entries(menu_env)) == 2


@pytest.mark.parametrize("demo", (True, False))
def test_opposite_mode_only_skips_picker(menu_env, demo):
    save_chat(menu_env, "Different mode CrossFit coaching", demo=not demo)
    result = run_menu(menu_env, "1\n/quit\nq\n", demo=demo)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Continue latest conversation" not in result.stdout
    assert "Resuming chat:" not in result.stdout
    assert len(saved_entries(menu_env)) == 1


@pytest.mark.parametrize("input_text", ("1\n", "1\n3\n"))
def test_eof_from_saved_chat_choices_returns_cleanly_without_writes(menu_env, input_text):
    original = save_chat(menu_env, "CrossFit pacing")
    result = run_menu(menu_env, input_text)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Returning to the menu" in result.stdout
    assert "Traceback" not in result.stdout + result.stderr
    assert saved_entries(menu_env) == [original.to_dict()]


@pytest.mark.parametrize("picker", (False, True))
def test_ctrl_c_from_choices_returns_without_starting_chat(monkeypatch, menu_env, picker):
    original = save_chat(menu_env, "CrossFit pacing")
    console = Console(file=io.StringIO(), width=120)
    entries = iter(["3"] if picker else [])

    def interrupted_input(prompt):
        try:
            return next(entries)
        except StopIteration:
            raise KeyboardInterrupt

    monkeypatch.setattr(console, "input", interrupted_input)
    monkeypatch.setattr(cli, "_chat", lambda *args, **kwargs: pytest.fail("Must not start chat"))
    cli._chat_menu(console, Store(menu_env["GAMESCOACH_DATA_DIR"]), demo=True)
    assert "Returning to the menu" in console.file.getvalue()
    assert saved_entries(menu_env) == [original.to_dict()]


def test_menu_labels_escape_request_markup_and_identify_started_time(monkeypatch, menu_env):
    original = save_chat(menu_env, "CrossFit [red]intervals[/red]\nwith rowing")
    console = Console(file=io.StringIO(), width=120, color_system=None)
    monkeypatch.setattr(console, "input", lambda prompt: "b")
    cli._chat_menu(console, Store(menu_env["GAMESCOACH_DATA_DIR"]), demo=True)
    output = console.file.getvalue()
    assert "CrossFit [red]intervals[/red] with rowing" in output
    assert f"Started: {original.created_at}" in output
    assert original.id not in output
