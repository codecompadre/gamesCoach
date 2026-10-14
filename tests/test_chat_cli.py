"""Exercise conversational navigation and persistent coaching through the CLI."""

import io
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest
from rich.console import Console

from gamescoach import cli
from gamescoach.chat import ChatCoach, ChatContextError
from gamescoach.engine import create_session
from gamescoach.models import AthleteProfile
from gamescoach.providers import ProviderError
from gamescoach.storage import Store, StoreError


@pytest.fixture
def chat_env(tmp_path):
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


def run_cli(env, *args, input_text=None):
    return subprocess.run(
        [sys.executable, "-m", "gamescoach", *args], env=env, input=input_text,
        capture_output=True, text=True, encoding="utf-8", timeout=15,
    )


def sessions(env):
    return [json.loads(path.read_text(encoding="utf-8")) for path in (Path(env["GAMESCOACH_DATA_DIR"]) / "sessions").glob("*.json")]


def save_plan(env, index=0, *, kind="training"):
    plan = create_session(kind, f"CrossFit entry {index}", AthleteProfile(), rounds=1, demo=True)
    plan.created_at = f"2026-10-{index + 1:02d}T00:00:00+00:00"
    Store(env["GAMESCOACH_DATA_DIR"]).save_session(plan)
    return plan


def chat_console(monkeypatch, messages):
    output = io.StringIO()
    console = Console(file=output, width=120, highlight=False)
    entries = iter(messages)
    monkeypatch.setattr(console, "input", lambda prompt: next(entries))
    return console, output


def test_menu_chat_quit_returns_to_existing_workout_menu(chat_env):
    result = run_cli(chat_env, "--demo", input_text="8\nHow do I pace my CrossFit intervals?\n/ quit\n1\nCrossFit aerobic workout\nq\n")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Returning to the menu" in result.stdout
    assert result.stdout.count("1 Workout") >= 3
    saved = sessions(chat_env)
    assert {session["kind"] for session in saved} == {"chat", "workout"}


def test_chat_two_messages_update_one_saved_thread_and_hide_debate(chat_env):
    messages = ["How do I pace CrossFit intervals?", "Can I use a bike for that?"]
    result = run_cli(chat_env, "chat", "--demo", input_text="\n".join([*messages, "/quit"]) + "\n")
    assert result.returncode == 0, result.stdout + result.stderr
    saved, = sessions(chat_env)
    assert saved["kind"] == "chat" and saved["demo"] is True
    assert [message["content"] for message in saved["messages"] if message["role"] == "user"] == messages
    assert len(saved["messages"]) == 4
    assert saved["messages"][-1]["content"] == saved["final_plan"]
    assert len(saved["turns"]) == 6
    assert result.stdout.count("Saved chat:") == 2
    assert "chat-proposal" not in result.stdout and "chat-review" not in result.stdout
    assert "static sample" in result.stdout.lower()


def test_chat_global_demo_and_optional_debate_rounds(chat_env):
    result = run_cli(chat_env, "--demo", "chat", "--rounds", "2", "--show-debate", input_text="CrossFit warm-up ideas\n/exit\n")
    assert result.returncode == 0, result.stdout + result.stderr
    saved, = sessions(chat_env)
    assert len(saved["turns"]) == 5
    assert "chat-proposal" in result.stdout
    assert "chat-review" in result.stdout and "chat-refinement" in result.stdout


def test_chat_loads_profile_latest_five_and_read_adds_older_entry(chat_env):
    store = Store(chat_env["GAMESCOACH_DATA_DIR"])
    store.save_profile(AthleteProfile(name="Quarterfinals Athlete", goals="CrossFit Quarterfinals", limitations="Wrist discomfort"))
    kinds = ("workout", "training", "nutrition", "competition", "workout", "training", "competition")
    plans = [save_plan(chat_env, index, kind=kind) for index, kind in enumerate(kinds)]
    result = run_cli(
        chat_env, "chat", "--demo",
        input_text=f"/PROFILE\n/HISTORY\n/ read {plans[0].id}\nHow should my CrossFit week fit together?\n/QUIT\n",
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Quarterfinals Athlete" in result.stdout and "Wrist discomfort" in result.stdout
    assert "Saved plans and chats" in result.stdout
    assert f"Added coaching context: {plans[0].id}" in result.stdout
    saved, = [entry for entry in sessions(chat_env) if entry["kind"] == "chat"]
    assert saved["profile"]["name"] == "Quarterfinals Athlete"
    assert saved["context_ids"] == [plan.id for plan in reversed(plans[2:])] + [plans[0].id]


def test_read_context_limit_and_clearrefs_control_sent_entries(chat_env):
    plan = save_plan(chat_env)
    result = run_cli(
        chat_env, "chat", "--demo", "--context-limit", "0",
        input_text=f"/read {plan.id}\nCrossFit pacing question\n/clearrefs\nWhat about my recovery?\n/quit\n",
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "No saved entries loaded" in result.stdout
    assert "Removed saved entries from coaching context" in result.stdout
    saved, = [entry for entry in sessions(chat_env) if entry["kind"] == "chat"]
    assert len(saved["messages"]) == 4 and saved["context_ids"] == []


def test_resume_and_show_export_include_every_user_and_coach_message(chat_env, tmp_path):
    first = run_cli(chat_env, "chat", "--demo", input_text="CrossFit aerobic capacity\nHow should I progress?\n/quit\n")
    assert first.returncode == 0, first.stdout + first.stderr
    saved, = sessions(chat_env)
    resumed = run_cli(chat_env, "chat", "--demo", "--resume", saved["id"], input_text="Can I retest next month?\n/back\n")
    assert resumed.returncode == 0, resumed.stdout + resumed.stderr
    assert "Resuming chat" in resumed.stdout and "CrossFit aerobic capacity" in resumed.stdout
    updated, = sessions(chat_env)
    assert updated["id"] == saved["id"] and len(updated["messages"]) == 6

    shown = run_cli(chat_env, "show", saved["id"], "--transcript")
    assert shown.returncode == 0
    assert "chat-proposal" in shown.stdout and "chat-review" in shown.stdout
    for question in ("CrossFit aerobic capacity", "How should I progress?", "Can I retest next month?"):
        assert question in shown.stdout
    destination = tmp_path / "chat.md"
    exported = run_cli(chat_env, "export", saved["id"], "--output", str(destination))
    assert exported.returncode == 0, exported.stdout
    contents = destination.read_text(encoding="utf-8")
    assert contents.count("### You") == 3 and contents.count("### Coach") == 3
    assert all(message["content"] in contents for message in updated["messages"])


def test_resume_restores_explicit_references_and_warns_if_deleted(chat_env):
    plan = save_plan(chat_env)
    result = run_cli(chat_env, "chat", "--demo", input_text="Help pace this CrossFit workout\n/quit\n")
    assert result.returncode == 0, result.stdout + result.stderr
    saved, = [entry for entry in sessions(chat_env) if entry["kind"] == "chat"]
    assert saved["context_ids"] == [plan.id]
    resumed = run_cli(chat_env, "chat", "--demo", "--resume", saved["id"], "--context-limit", "0", input_text="CrossFit follow-up\n/quit\n")
    assert resumed.returncode == 0, resumed.stdout + resumed.stderr
    assert plan.id in resumed.stdout
    Path(chat_env["GAMESCOACH_DATA_DIR"], "sessions", f"{plan.id}.json").unlink()
    missing = run_cli(chat_env, "chat", "--demo", "--resume", saved["id"], "--context-limit", "0", input_text="CrossFit recovery question\n/quit\n")
    assert missing.returncode == 0, missing.stdout + missing.stderr
    assert f"Previously loaded context {plan.id} is unavailable" in missing.stdout
    updated, = sessions(chat_env)
    assert updated["context_ids"] == [] and len(updated["messages"]) == 6


def test_context_changes_save_without_another_reply_and_resume_does_not_reload_recent(chat_env):
    plan = save_plan(chat_env)
    result = run_cli(
        chat_env, "chat", "--demo", "--context-limit", "0",
        input_text=f"CrossFit pacing question\n/read {plan.id}\n/quit\n",
    )
    assert result.returncode == 0, result.stdout + result.stderr
    chat, = [entry for entry in sessions(chat_env) if entry["kind"] == "chat"]
    assert chat["context_ids"] == [plan.id] and len(chat["messages"]) == 2
    newer_plan = save_plan(chat_env, 1, kind="nutrition")
    resumed = run_cli(
        chat_env, "chat", "--demo", "--resume", chat["id"],
        input_text="/clearrefs\n/quit\n",
    )
    assert resumed.returncode == 0, resumed.stdout + resumed.stderr
    assert plan.id in resumed.stdout and newer_plan.id not in resumed.stdout
    saved, = [entry for entry in sessions(chat_env) if entry["kind"] == "chat"]
    assert saved["context_ids"] == [] and len(saved["messages"]) == 2
    cleared = run_cli(chat_env, "chat", "--demo", "--resume", chat["id"], input_text="/quit\n")
    assert cleared.returncode == 0, cleared.stdout + cleared.stderr
    assert "No saved entries loaded" in cleared.stdout
    assert plan.id not in cleared.stdout and newer_plan.id not in cleared.stdout


def test_new_keeps_context_and_creates_separate_saved_chat(chat_env):
    plan = save_plan(chat_env)
    result = run_cli(chat_env, "chat", "--demo", input_text="First CrossFit question\n/new\nSecond CrossFit question\n/quit\n")
    assert result.returncode == 0, result.stdout + result.stderr
    chats = [entry for entry in sessions(chat_env) if entry["kind"] == "chat"]
    assert len(chats) == 2 and len({entry["id"] for entry in chats}) == 2
    assert all(len(entry["messages"]) == 2 and entry["context_ids"] == [plan.id] for entry in chats)
    assert {entry["request"] for entry in chats} == {"First CrossFit question", "Second CrossFit question"}


@pytest.mark.parametrize("command", ("/quit", "/ quit", "/BACK", "/ Exit"))
def test_navigation_commands_and_blank_input_never_make_live_requests(chat_env, command):
    result = run_cli(chat_env, "chat", input_text=f"\n/unknown\n/ help\n/read\n{command}\n")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Unknown chat command" in result.stdout
    assert "BOTH ChatGPT and Gemini" in result.stdout
    assert "Live coaching needs" not in result.stdout
    assert not sessions(chat_env)


def test_missing_live_keys_keeps_console_usable_without_saving(chat_env):
    result = run_cli(chat_env, "chat", input_text="How should I pace CrossFit intervals?\n/help\n/quit\n")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "OPENAI_API_KEY" in result.stdout and "GEMINI_API_KEY" in result.stdout
    assert "--demo" in result.stdout and "Your previous conversation is unchanged" in result.stdout
    assert "Traceback" not in result.stdout + result.stderr
    assert not sessions(chat_env)


@pytest.mark.parametrize("args,input_text", ((["chat", "--demo"], ""), (["--demo"], "8\n")))
def test_eof_leaves_chat_cleanly(chat_env, args, input_text):
    result = run_cli(chat_env, *args, input_text=input_text)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Traceback" not in result.stdout + result.stderr
    assert not sessions(chat_env)


def test_invalid_read_does_not_block_a_later_coaching_message(chat_env):
    result = run_cli(chat_env, "chat", "--demo", input_text="/read ../secret\nCrossFit recovery question\n/quit\n")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Invalid" in result.stdout
    saved, = sessions(chat_env)
    assert len(saved["messages"]) == 2 and saved["context_ids"] == []


def test_resume_plan_or_wrong_mode_is_rejected(chat_env):
    plan = save_plan(chat_env)
    invalid = run_cli(chat_env, "chat", "--demo", "--resume", plan.id, input_text="/quit\n")
    assert invalid.returncode == 1 and "Resume a saved coach chat" in invalid.stdout
    run_cli(chat_env, "chat", "--demo", input_text="CrossFit question\n/quit\n")
    chat, = [entry for entry in sessions(chat_env) if entry["kind"] == "chat"]
    wrong_mode = run_cli(chat_env, "chat", "--resume", chat["id"], input_text="/quit\n")
    assert wrong_mode.returncode == 1 and "Resume demo chats with --demo" in wrong_mode.stdout


def test_storage_failure_still_displays_answers_and_retains_in_memory(monkeypatch, tmp_path):
    store = Store(tmp_path / "data")
    captured = []

    class RecordingCoach(ChatCoach):
        def reply(self, message, on_turn=None):
            result = super().reply(message, on_turn)
            captured.append(result)
            return result

    monkeypatch.setattr(cli, "ChatCoach", RecordingCoach)
    monkeypatch.setattr(store, "save_chat", lambda session: (_ for _ in ()).throw(StoreError("Disk is full")))
    console, output = chat_console(monkeypatch, ["CrossFit question", "A follow-up", "/quit"])
    cli._chat(console, store, demo=True)
    assert len(captured) == 2 and len(captured[-1].messages) == 4
    assert output.getvalue().count("This reply is unsaved") == 2
    assert output.getvalue().count("Coach\n") == 2
    assert "Saved chat:" not in output.getvalue()


def test_context_save_failure_preserves_old_disk_thread_and_updates_memory(monkeypatch, tmp_path):
    store = Store(tmp_path / "data")
    plan = create_session("training", "CrossFit", AthleteProfile(), rounds=1, demo=True)
    store.save_session(plan)
    coaches = []

    class RecordingCoach(ChatCoach):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            coaches.append(self)

    save = store.save_chat
    attempts = []

    def failing_metadata_save(session):
        attempts.append(session)
        if len(attempts) > 1:
            raise StoreError("Cannot save context")
        save(session)

    monkeypatch.setattr(cli, "ChatCoach", RecordingCoach)
    monkeypatch.setattr(store, "save_chat", failing_metadata_save)
    console, output = chat_console(monkeypatch, ["CrossFit question", "/clearrefs", "/quit"])
    cli._chat(console, store, demo=True)
    saved, = [entry for entry in store.list_sessions() if entry.kind == "chat"]
    assert saved.context_ids == [plan.id] and len(saved.messages) == 2
    assert coaches[0].session.context_ids == [] and coaches[0].context == []
    assert "These context changes are unsaved" in output.getvalue()


def test_provider_failure_keeps_prior_history_and_allows_next_reply(monkeypatch, tmp_path):
    store = Store(tmp_path / "data")
    attempts = []

    class FailingCoach(ChatCoach):
        def reply(self, message, on_turn=None):
            attempts.append(message)
            if message == "Fail this reply":
                raise ProviderError("Example provider failure")
            return super().reply(message, on_turn)

    monkeypatch.setattr(cli, "ChatCoach", FailingCoach)
    console, output = chat_console(monkeypatch, ["First CrossFit question", "Fail this reply", "CrossFit follow-up", "/quit"])
    cli._chat(console, store, demo=True)
    saved, = store.list_sessions()
    assert [message.content for message in saved.messages if message.role == "user"] == ["First CrossFit question", "CrossFit follow-up"]
    assert attempts == ["First CrossFit question", "Fail this reply", "CrossFit follow-up"]
    assert "Example provider failure" in output.getvalue()


def test_menu_context_overflow_explicitly_starts_without_references(monkeypatch, tmp_path):
    store = Store(tmp_path / "data")
    plan = create_session("training", "CrossFit", AthleteProfile(), rounds=1, demo=True)
    store.save_session(plan)

    class OverflowCoach(ChatCoach):
        def __init__(self, profile, context, **kwargs):
            if context:
                raise ChatContextError("Saved references are too large")
            super().__init__(profile, context, **kwargs)

    monkeypatch.setattr(cli, "ChatCoach", OverflowCoach)
    console, output = chat_console(monkeypatch, ["CrossFit question", "/quit"])
    cli._chat(console, store, demo=True, from_menu=True)
    assert "Starting chat without saved entries" in output.getvalue()
    saved, = [entry for entry in store.list_sessions() if entry.kind == "chat"]
    assert saved.context_ids == []


def test_keyboard_interrupt_returns_to_menu(monkeypatch, tmp_path):
    console, output = chat_console(monkeypatch, [])

    def interrupt(prompt):
        raise KeyboardInterrupt

    monkeypatch.setattr(console, "input", interrupt)
    cli._chat(console, Store(tmp_path / "data"), demo=True, from_menu=True)
    assert "Returning to the menu" in output.getvalue()


def test_plan_choices_stay_limited_to_four_plan_kinds(chat_env):
    result = run_cli(chat_env, "plan", "--demo", "--kind", "chat", "--request", "CrossFit")
    assert result.returncode == 2 and "invalid choice" in result.stderr
