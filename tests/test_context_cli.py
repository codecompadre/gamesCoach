"""Long CLI conversations retain their complete history and reference records."""

from dataclasses import replace
import io

import pytest
from rich.console import Console

from gamescoach import cli
from gamescoach.chat import ChatCoach
from gamescoach.engine import create_session
from gamescoach.models import AthleteProfile, ChatMessage
from gamescoach.storage import Store


@pytest.fixture
def private_store(monkeypatch, tmp_path):
    monkeypatch.setenv("GAMESCOACH_DATA_DIR", str(tmp_path / "data"))
    for name in (
        "OPENAI_API_KEY", "GAMESCOACH_OPENAI_API_KEY", "GEMINI_API_KEY",
        "GOOGLE_API_KEY", "GAMESCOACH_GEMINI_API_KEY", "GAMESCOACH_COACH_NOTES_FILE",
    ):
        monkeypatch.delenv(name, raising=False)
    return Store()


def console_input(monkeypatch, messages):
    output = io.StringIO()
    console = Console(file=output, width=160, highlight=False)
    answers = iter(messages)
    monkeypatch.setattr(console, "input", lambda prompt: next(answers))
    return console, output


def save_large_document(store, number):
    sample = create_session("training", f"CrossFit training week {number}", AthleteProfile(), rounds=1, demo=True)
    entry = replace(
        sample, kind="document", demo=False,
        final_plan=(f"Week {number}: aerobic intervals, pacing and CrossFit recovery. " * 1200)[:60000],
    )
    store.save_session(entry)
    return entry


def test_context_command_shows_full_context_and_pdf_status_without_coaching(monkeypatch, private_store):
    class PreparedLibrary:
        def prepare(self):
            return {"guides": 2, "pages": 307}

    def unexpected_reply(*args, **kwargs):
        pytest.fail("/context must not generate a coaching exchange")

    monkeypatch.setattr(cli, "Library", PreparedLibrary)
    monkeypatch.setattr(ChatCoach, "reply", unexpected_reply)
    console, output = console_input(monkeypatch, ["/context", "/context", "/quit"])
    cli._chat(console, private_store)
    rendered = output.getvalue()
    assert rendered.count("fit the current request budget in full") == 2
    assert "Conversation: 0 available messages" in rendered
    assert "PDF knowledge: 2 guides · 307 PDF pages" in rendered
    assert "brief and relevant PDF passages remain active" in rendered
    assert not private_store.list_sessions()


def test_large_automatic_menu_references_are_retained_and_chat_can_continue(monkeypatch, private_store):
    entries = [save_large_document(private_store, number) for number in (1, 2)]
    originals = {entry.id: (private_store.root / "sessions" / f"{entry.id}.json").read_bytes() for entry in entries}
    console, output = console_input(monkeypatch, ["/context", "How should I pace my CrossFit aerobic intervals?", "/context", "/quit"])
    cli._chat(console, private_store, demo=True, from_menu=True)
    saved, = [entry for entry in private_store.list_sessions() if entry.kind == "chat"]
    assert set(saved.context_ids) == {entry.id for entry in entries}
    assert len(saved.messages) == 2
    assert "Automatic context selection is active" in output.getvalue()
    assert "Starting chat without saved entries" not in output.getvalue()
    assert "Error:" not in output.getvalue()
    for entry in entries:
        assert entry.id in output.getvalue()
        assert (private_store.root / "sessions" / f"{entry.id}.json").read_bytes() == originals[entry.id]


def test_long_resumed_chat_keeps_all_messages_and_reference_ids(monkeypatch, private_store):
    entry = save_large_document(private_store, 1)
    initial = ChatCoach(AthleteProfile(), [], demo=True).reply("My CrossFit goal is better aerobic capacity.")
    history = list(initial.messages)
    for index in range(35):
        history.extend([
            ChatMessage("user", f"CrossFit training update {index}: " + "Aerobic intervals, pacing, wrist tolerance and recovery. " * 45),
            ChatMessage("assistant", f"CrossFit coaching reply {index}: " + "Track sustainable pacing and recovery before progressing. " * 35),
        ])
    prior = replace(initial, messages=history, final_plan=history[-1].content, context_ids=[entry.id])
    private_store.save_chat(prior)
    before_reference = (private_store.root / "sessions" / f"{entry.id}.json").read_bytes()
    console, output = console_input(monkeypatch, ["/context", "How should I progress my CrossFit aerobic training?", "/context", "/quit"])
    cli._chat(console, private_store, demo=True, resume=prior.id)
    saved = private_store.get_session(prior.id)
    assert saved.messages[:-2] == prior.messages
    assert len(saved.messages) == len(prior.messages) + 2
    assert saved.context_ids == [entry.id]
    assert (private_store.root / "sessions" / f"{entry.id}.json").read_bytes() == before_reference
    assert "Automatic context selection is active" in output.getvalue()
    assert "The complete conversation and saved entries remain stored" in output.getvalue()
    assert "Error:" not in output.getvalue()
