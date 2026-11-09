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
    assert "Chat option" not in second.stdout
    assert "Resuming chat:" in second.stdout and original["id"] in second.stdout
    assert "CrossFit aerobic capacity" in second.stdout and "How should I progress?" in second.stdout
    updated, = saved_entries(menu_env)
    assert updated["id"] == original["id"]
    assert updated["messages"][:4] == original["messages"]
    assert [message["content"] for message in updated["messages"] if message["role"] == "user"] == [
        "CrossFit aerobic capacity", "How should I progress?", "Can I retest next month?",
    ]


def test_menu_resumes_latest_and_preserves_older_archive(menu_env):
    older = save_chat(menu_env, "CrossFit intervals", order=1)
    latest = save_chat(menu_env, "CrossFit nutrition", order=2)
    result = run_menu(menu_env, "1\nCrossFit fueling progression\n/quit\nq\n")
    assert result.returncode == 0, result.stdout + result.stderr
    chats = {chat["id"]: chat for chat in saved_entries(menu_env)}
    assert chats[older.id] == older.to_dict()
    assert len(chats[latest.id]["messages"]) == 4
    assert "Chat option" not in result.stdout


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
    assert "Chat option" not in result.stdout
    assert f"Resuming chat: {original.id}" in result.stdout
    assert len(saved_entries(menu_env)) == 13


@pytest.mark.parametrize("demo", (True, False))
def test_resume_picker_only_offers_chats_in_current_mode(menu_env, demo):
    matching = save_chat(menu_env, "Matching CrossFit conversation", demo=demo, order=1)
    other = save_chat(menu_env, "Other mode CrossFit conversation", demo=not demo, order=2)
    result = run_menu(menu_env, "1\n/quit\nq\n", demo=demo)
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


def test_eof_in_resumed_conversation_returns_cleanly_without_writes(menu_env):
    original = save_chat(menu_env, "CrossFit pacing")
    result = run_menu(menu_env, "1\n")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Traceback" not in result.stdout + result.stderr
    assert saved_entries(menu_env) == [original.to_dict()]
