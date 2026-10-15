"""Check readable output through the real chat, plan and saved-session flows."""

import io

import pytest
from rich.console import Console

from gamescoach import chat, cli, engine
from gamescoach.chat import ChatCoach
from gamescoach.models import AthleteProfile
from gamescoach.storage import Store


REPLY = (
    "## Conditioning\n\n"
    "**Work:** Four controlled rowing intervals.\n\n"
    "**Scaling:** Choose a wrist-friendly option.\n\n"
    "**Progress check:** Record repeatable splits."
)
FINAL_PLAN = "## Final CrossFit session\n\nComplete final coaching prescription."


@pytest.fixture
def readable_store(monkeypatch, tmp_path):
    monkeypatch.setenv("GAMESCOACH_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setitem(chat._DEMO_CONTENT, "chat-answer", REPLY)
    return Store()


def output_console(monkeypatch, entries=()):
    output = io.StringIO()
    console = Console(file=output, width=120, color_system=None, highlight=False)
    answers = iter(entries)
    monkeypatch.setattr(console, "input", lambda _prompt: next(answers))
    return console, output


def install_distinct_plan(monkeypatch):
    monkeypatch.setattr(
        engine,
        "_demo_content",
        lambda _kind, stage: FINAL_PLAN if stage == "synthesis" else f"Intermediate coach draft: {stage}.",
    )


def assert_readable_reply(output, count=1):
    assert output.count("Four controlled rowing intervals.") == count
    assert output.count("Choose a wrist-friendly option.") == count
    assert output.count("Record repeatable splits.") == count
    assert "Conditioning" in output and "Coach" in output
    assert output.count("╭─ Coach ") == count
    assert "\x1b[" not in output


def test_new_resumed_and_shown_chat_preserve_the_complete_readable_reply(monkeypatch, readable_store):
    console, output = output_console(monkeypatch, ["CrossFit intervals", "/quit"])
    cli._chat(console, readable_store, demo=True)
    assert_readable_reply(output.getvalue())

    saved, = readable_store.list_chats(demo=True)
    assert REPLY in saved.final_plan
    original = saved.to_dict()
    path = readable_store.root / "sessions" / f"{saved.id}.json"
    original_bytes = path.read_bytes()

    console, output = output_console(monkeypatch, ["/quit"])
    cli._chat(console, readable_store, demo=True, resume=saved.id)
    assert_readable_reply(output.getvalue())
    assert "Resuming chat:" in output.getvalue()

    console, output = output_console(monkeypatch)
    cli._render_session(console, readable_store.get_session(saved.id))
    assert_readable_reply(output.getvalue())
    assert saved.to_dict() == original
    assert path.read_bytes() == original_bytes


def test_plan_discussion_labels_both_coaches_and_displays_final_answer_once(monkeypatch, readable_store):
    install_distinct_plan(monkeypatch)
    console, output = output_console(monkeypatch)
    cli._run_plan(console, readable_store, kind="workout", request="CrossFit aerobic work", rounds=1, demo=True, show_debate=True)
    shown = output.getvalue()
    assert "ChatGPT · Draft" in shown
    assert "Gemini · Review" in shown
    assert shown.count("Complete final coaching prescription.") == 1
    assert "╭─ CrossFit workout " in shown

    saved, = readable_store.list_sessions()
    assert saved.turns[-1].stage == "synthesis"
    console, output = output_console(monkeypatch)
    cli._render_session(console, saved, transcript=True)
    assert "Intermediate coach draft: proposal." in output.getvalue()
    assert "Intermediate coach draft: review." in output.getvalue()
    assert output.getvalue().count("Complete final coaching prescription.") == 1


def test_chat_discussion_keeps_provider_labels_and_one_final_reply(monkeypatch, readable_store):
    console, output = output_console(monkeypatch, ["CrossFit pacing", "/quit"])
    cli._chat(console, readable_store, demo=True, show_debate=True)
    shown = output.getvalue()
    assert "ChatGPT · Draft" in shown
    assert "Gemini · Review" in shown
    assert_readable_reply(shown)
    saved, = readable_store.list_chats(demo=True)
    assert [turn.stage for turn in saved.turns] == ["chat-proposal", "chat-review", "chat-answer"]


def test_menu_shows_final_plan_without_intermediate_drafts(monkeypatch, readable_store):
    install_distinct_plan(monkeypatch)
    console, output = output_console(monkeypatch, ["1", "CrossFit aerobic session", "q"])
    assert cli._interactive(console, readable_store, demo=True) == 0
    assert output.getvalue().count("Complete final coaching prescription.") == 1
    assert "Intermediate coach draft:" not in output.getvalue()
    saved, = readable_store.list_sessions()
    assert any(turn.stage == "review" for turn in saved.turns)


@pytest.mark.parametrize("kind", ["chat", "workout"])
def test_readable_display_does_not_change_saved_or_exported_markdown(monkeypatch, readable_store, tmp_path, kind):
    if kind == "chat":
        session = ChatCoach(AthleteProfile(), [], demo=True).reply("CrossFit rower pacing")
        readable_store.save_chat(session)
    else:
        install_distinct_plan(monkeypatch)
        session = engine.create_session("workout", "CrossFit rower pacing", AthleteProfile(), rounds=1, demo=True)
        readable_store.save_session(session)
    source = session.final_plan
    path = readable_store.root / "sessions" / f"{session.id}.json"
    original_bytes = path.read_bytes()
    console, output = output_console(monkeypatch)
    cli._render_session(console, session, transcript=True)
    target = tmp_path / f"{kind}.md"
    cli._export(console, session, target, force=False)
    exported = target.read_text(encoding="utf-8")
    assert source in exported
    assert "\x1b[" not in exported
    assert "╭" not in exported and "╰" not in exported
    assert path.read_bytes() == original_bytes
    assert session.final_plan == source


def test_view_reprints_latest_reply_without_another_coaching_request(monkeypatch, readable_store):
    requests = []
    original_reply = ChatCoach.reply

    def recorded_reply(self, message, on_turn=None):
        requests.append(message)
        return original_reply(self, message, on_turn)

    monkeypatch.setattr(ChatCoach, "reply", recorded_reply)
    console, output = output_console(monkeypatch, ["/view", "CrossFit intervals", "/view", "/quit"])
    assert not console.is_terminal
    cli._chat(console, readable_store, demo=True)
    assert "No coach reply yet." in output.getvalue()
    assert_readable_reply(output.getvalue(), count=2)
    assert requests == ["CrossFit intervals"]
    saved, = readable_store.list_chats(demo=True)
    assert len(saved.messages) == 2
