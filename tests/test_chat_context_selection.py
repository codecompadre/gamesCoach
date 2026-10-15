"""Both coaches can continue long persisted chats with bounded source context."""

import json

import pytest

from gamescoach import chat
from gamescoach.knowledge import Library
from gamescoach.models import AthleteProfile, ChatMessage, CoachTurn, CoachingSession
from gamescoach.providers import ProviderError
from gamescoach.storage import Store


@pytest.fixture
def long_chat(monkeypatch, tmp_path):
    monkeypatch.setenv("GAMESCOACH_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("OPENAI_API_KEY", "test-openai")
    monkeypatch.setenv("GEMINI_API_KEY", "test-gemini")
    monkeypatch.delenv("GAMESCOACH_COACH_NOTES_FILE", raising=False)
    library = Library()
    library.root.mkdir(parents=True)
    guide = {"id": "a" * 16, "filename": "Methodology.pdf", "pages": 1, "passages": [
        {"page": 1, "text": "CrossFit mechanics and consistency precede intensity; aerobic progression needs recovery."},
    ]}
    (library.root / f'{guide["id"]}.json').write_text(json.dumps(guide))
    profile = AthleteProfile(goals="CrossFit Quarterfinals", limitations="Wrist discomfort")
    messages = [
        ChatMessage("user", "My wrists hurt in loaded extension. My schedule used to allow six training days."),
        ChatMessage("assistant", "Respect wrist tolerance and training availability."),
    ]
    for number in range(20):
        messages.extend([
            ChatMessage("user", f"Training log {number}: " + "Steady rowing with controlled breathing. " * 130),
            ChatMessage("assistant", "Record repeatability and effort. " * 130),
        ])
    messages.extend([
        ChatMessage("user", "My schedule has changed: I can train only four days per week now."),
        ChatMessage("assistant", "Use the current four-day availability."),
    ])
    entry = CoachingSession(
        id="b" * 12, created_at="2026-10-15T12:00:00+00:00", kind="document",
        request="Imported CrossFit training week", profile=profile,
        turns=[CoachTurn("Local file", "import", "Imported training text")],
        final_plan="Aerobic session: row 4 x 4 minutes with easy recovery. " * 4000,
    )
    session = CoachingSession(
        id="c" * 12, created_at="2026-10-15T12:00:00+00:00", kind="chat",
        request=messages[0].content, profile=profile,
        turns=[CoachTurn("ChatGPT", "chat-answer", messages[-1].content)],
        final_plan=messages[-1].content, messages=messages, context_ids=[entry.id],
    )
    store = Store()
    store.save_session(entry)
    store.save_chat(session)
    return profile, entry, session, store


def fake_coaches(monkeypatch, rounds, *, huge_outputs=False, fail=False):
    calls = []
    for classname, name in (("OpenAIProvider", "ChatGPT"), ("GeminiProvider", "Gemini")):
        class Coach:
            def __init__(self, key, model, label=name):
                self.name, self.model = label, model

            def generate(self, *, instructions, prompt):
                calls.append((self.name, instructions, prompt))
                if fail:
                    raise ProviderError("simulated provider failure")
                if huge_outputs and len(calls) < rounds * 2 + 1:
                    return "Respect wrist limitations and aerobic progression. " * 2500
                return "Use four CrossFit training days and comfortable wrist positions."

        monkeypatch.setattr(chat, classname, Coach)
    return calls


@pytest.mark.parametrize("rounds,huge_outputs", [(1, False), (1, True), (3, True)])
def test_both_coaches_continue_same_long_chat_and_keep_pdf_guidance(long_chat, monkeypatch, rounds, huge_outputs):
    profile, entry, session, store = long_chat
    reference_path = store.root / "sessions" / f"{entry.id}.json"
    reference_bytes = reference_path.read_bytes()
    before = session.to_dict()
    calls = fake_coaches(monkeypatch, rounds, huge_outputs=huge_outputs)
    coach = chat.ChatCoach(profile, [entry], rounds=rounds, session=store.get_session(session.id))
    question = "How should I progress my CrossFit aerobic work next week?"
    result = coach.reply(question)
    assert len(calls) == rounds * 2 + 1
    for index, (_, instructions, prompt) in enumerate(calls):
        assert len(prompt) <= chat._MAX_CONTEXT_CHARS
        data = json.loads(prompt.split("\n", 1)[1].split("\nYour turn:", 1)[0])
        assert data["athlete"] == profile.to_dict()
        assert data["dialogue"][-1]["content"] == question
        assert data["methodology_brief"] == result.methodology_brief
        assert data["methodology_excerpts"] == result.methodology_excerpts
        assert "wrists hurt in loaded extension" in json.dumps(data)
        assert "only four days per week now" in json.dumps(data)
        assert data["saved_entries"][0]["id"] == entry.id
        assert data["context_selection"]["shared_turns_total"] == index
        assert data["context_selection"]["shared_turns_included"] == len(data["shared_conversation"])
        assert "newer" in instructions and "supersede" in instructions
    assert result.id == session.id and result.messages[:-2] == session.messages
    assert result.context_ids == [entry.id]
    assert coach.context_report and result.methodology_brief
    if huge_outputs:
        assert any(len(turn.content) > chat._MAX_CONTEXT_CHARS for turn in result.turns)
    store.save_chat(result)
    assert store.get_session(result.id).to_dict() == result.to_dict()
    assert reference_path.read_bytes() == reference_bytes
    assert session.to_dict() == before


def test_provider_failure_after_selection_preserves_complete_saved_chat(long_chat, monkeypatch):
    profile, entry, session, store = long_chat
    path = store.root / "sessions" / f"{session.id}.json"
    saved_bytes = path.read_bytes()
    calls = fake_coaches(monkeypatch, 1, fail=True)
    coach = chat.ChatCoach(profile, [entry], session=session)
    with pytest.raises(ProviderError, match="simulated"):
        coach.reply("How should I pace my CrossFit aerobic work?")
    assert len(calls) == 1
    assert coach.session is session
    assert path.read_bytes() == saved_bytes
