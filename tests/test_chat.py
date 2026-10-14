from datetime import datetime
import json

import pytest

from gamescoach import chat
from gamescoach.models import AthleteProfile, ChatMessage, CoachTurn, CoachingSession
from gamescoach.prompts import COACH_INSTRUCTIONS, SCOPE_REFUSAL
from gamescoach.providers import ProviderError


KEYS = ("OPENAI_API_KEY", "GAMESCOACH_OPENAI_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY", "GAMESCOACH_GEMINI_API_KEY")


@pytest.fixture(autouse=True)
def isolated_settings(monkeypatch):
    for key in (*KEYS, "GAMESCOACH_COACH_NOTES_FILE", "OPENAI_MODEL", "GEMINI_MODEL"):
        monkeypatch.delenv(key, raising=False)


def fake_coaches(monkeypatch, responses=None):
    calls = []
    initialized = []
    for classname, name in (("OpenAIProvider", "ChatGPT"), ("GeminiProvider", "Gemini")):
        class Coach:
            def __init__(self, key, model, coach_name=name):
                self.name = coach_name
                self.model = model
                initialized.append((coach_name, key, model))

            def generate(self, *, instructions, prompt):
                calls.append((self.name, instructions, prompt))
                response = responses[len(calls) - 1] if responses else f"{self.name} coaching reply {len(calls)}"
                if isinstance(response, Exception):
                    raise response
                return response
        monkeypatch.setattr(chat, classname, Coach)
    return calls, initialized


def keys(monkeypatch):
    monkeypatch.setenv("GAMESCOACH_OPENAI_API_KEY", "test-openai")
    monkeypatch.setenv("GAMESCOACH_GEMINI_API_KEY", "test-gemini")


def prompt_data(prompt):
    return json.loads(prompt.split("\n", 1)[1].split("\nYour turn:", 1)[0])


def saved_entry(kind="workout", identifier="abcdef123456", final_plan="Exact saved CrossFit session: 4 x 4 minutes, 1 minute easy."):
    return CoachingSession(
        id=identifier,
        created_at="2026-10-14T12:00:00+00:00",
        kind=kind,
        request="Improve my CrossFit aerobic capacity",
        profile=AthleteProfile(experience="Competing athlete"),
        turns=[CoachTurn("ChatGPT", "synthesis", final_plan)],
        final_plan=final_plan,
    )


def test_both_coaches_receive_every_earlier_athlete_message_reply_and_saved_menu_entry(monkeypatch, tmp_path):
    keys(monkeypatch)
    notes = tmp_path / "coach-notes.md"
    notes.write_text("Prioritize repeatable aerobic work and Open preparation.")
    monkeypatch.setenv("GAMESCOACH_COACH_NOTES_FILE", str(notes))
    monkeypatch.setenv("OPENAI_MODEL", "configured-openai")
    monkeypatch.setenv("GEMINI_MODEL", "configured-gemini")
    calls, initialized = fake_coaches(monkeypatch)
    profile = AthleteProfile(experience="Competing", goals="Quarterfinals", limitations="Wrist discomfort", training_days_per_week=5)
    entries = [saved_entry(kind, f"abcdef12345{index}", f"Complete {kind} menu entry {index}: exact prescribed details.") for index, kind in enumerate(("workout", "training", "nutrition", "competition"))]
    coach = chat.ChatCoach(profile, entries)
    observed = []
    first = coach.reply("How should I pace my saved aerobic workout?", on_turn=observed.append)
    first_before = first.to_dict()
    second = coach.reply("I only have four training days next week.", on_turn=observed.append)
    third = coach.reply("And how do those days fit with the nutrition goal?", on_turn=observed.append)

    assert [call[0] for call in calls] == ["ChatGPT", "Gemini", "ChatGPT"] * 3
    for index, (_, instructions, prompt) in enumerate(calls):
        data = prompt_data(prompt)
        reply_number = index // 3
        assert data["dialogue"] == [vars(message) for message in third.messages[:reply_number * 2 + 1]]
        exchange_start = reply_number * 3
        assert data["shared_conversation"] == [vars(turn) for turn in third.turns[exchange_start:index]]
        assert data["athlete"] == profile.to_dict()
        assert data["optional_coaching_notes"] == notes.read_text()
        assert data["saved_entries"] == [
            {"id": entry.id, "kind": entry.kind, "created_at": entry.created_at, "request": entry.request, "final_plan": entry.final_plan, "demo": False}
            for entry in entries
        ]
        assert data["current_date"] == datetime.fromisoformat(data["reference_time"]).date().isoformat()
        assert datetime.fromisoformat(data["reference_time"]).tzinfo is not None
        assert instructions.startswith(COACH_INSTRUCTIONS)
        assert "CONVERSATION MODE" in instructions
        assert "do not turn each" in instructions.lower()
        assert "untrusted" in instructions.lower() and "untrusted" in prompt.lower()

    assert third.id == second.id == first.id
    assert third.created_at == second.created_at == first.created_at
    assert third.request == first.request
    assert third.final_plan == "ChatGPT coaching reply 9"
    assert len(third.messages) == 6 and len(third.turns) == 9
    assert first.to_dict() == first_before
    assert observed == third.turns
    assert third.models == {"ChatGPT": "configured-openai", "Gemini": "configured-gemini"}
    assert third.context_ids == [entry.id for entry in entries]
    assert initialized[0] == ("ChatGPT", "test-openai", "configured-openai")
    assert "test-openai" not in json.dumps(third.to_dict())
    assert "test-gemini" not in json.dumps(third.to_dict())


def test_explicit_read_adds_exact_full_entry_to_both_coaches_without_mutating_supplied_list(monkeypatch):
    keys(monkeypatch)
    calls, _ = fake_coaches(monkeypatch)
    entries = [saved_entry()]
    coach = chat.ChatCoach(AthleteProfile(), entries)
    coach.reply("Help me understand my CrossFit week.")
    long_plan = "All original saved training detail.\n" * 250
    extra = saved_entry("training", "111122223333", long_plan)
    coach.add_context(extra)
    coach.add_context(extra)
    coach.reply("What should I adjust in entry 111122223333?")
    assert entries == [saved_entry()]
    assert len(coach.context) == 2
    for _, _, prompt in calls[-3:]:
        data = prompt_data(prompt)
        assert data["saved_entries"][1]["final_plan"] == long_plan
        assert data["saved_entries"][1]["id"] == extra.id


def test_reference_with_same_id_is_updated_without_duplicates():
    original = saved_entry()
    updated = saved_entry(final_plan="Updated saved CrossFit workout.")
    coach = chat.ChatCoach(AthleteProfile(), [original, original], demo=True)
    coach.add_context(updated)
    assert coach.context == [updated]


def test_reading_a_saved_chat_supplies_its_entire_dialogue_to_both_coaches(monkeypatch):
    keys(monkeypatch)
    calls, _ = fake_coaches(monkeypatch)
    messages = [
        ChatMessage("user", "Let's discuss my CrossFit training."),
        ChatMessage("assistant", "What limitations affect your sessions?"),
        ChatMessage("user", "My wrists are uncomfortable with handstands and I only train four days."),
        ChatMessage("assistant", "Use comfortable alternatives and preserve recovery between training days."),
        ChatMessage("user", "What is the purpose of easy aerobic work?"),
        ChatMessage("assistant", "Build repeatability at a conversational effort."),
    ]
    saved_chat = CoachingSession(
        id="111122223333", created_at="2026-10-14T12:00:00+00:00", kind="chat",
        request=messages[0].content, profile=AthleteProfile(),
        turns=[CoachTurn("ChatGPT", "chat-answer", messages[-1].content)],
        final_plan=messages[-1].content, demo=True, messages=messages,
    )
    coach = chat.ChatCoach(AthleteProfile(), [saved_entry()])
    coach.add_context(saved_chat)
    coach.reply("Use the constraints we discussed in chat 111122223333 for my next CrossFit session.")
    for _, instructions, prompt in calls:
        data = prompt_data(prompt)
        reference = data["saved_entries"][1]
        assert reference["messages"] == [vars(message) for message in messages]
        assert reference["demo"] is True
        assert "illustrative offline samples" in instructions
        assert "never establishes that the athlete performed" in instructions
        assert "messages" not in data["saved_entries"][0]


def test_clear_references_keeps_dialogue_and_persists_empty_reference_ids():
    coach = chat.ChatCoach(AthleteProfile(), [saved_entry()], demo=True)
    first = coach.reply("Discuss my CrossFit week.")
    assert first.context_ids == ["abcdef123456"]
    coach.clear_context()
    second = coach.reply("Continue without saved references.")
    assert coach.context == []
    assert second.context_ids == []
    assert second.messages[:2] == first.messages


def test_active_chat_cannot_reference_itself_before_any_paid_call(monkeypatch):
    keys(monkeypatch)
    calls, _ = fake_coaches(monkeypatch)
    coach = chat.ChatCoach(AthleteProfile(), [])
    first = coach.reply("Discuss my CrossFit week.")
    with pytest.raises(ValueError, match="already available"):
        coach.add_context(first)
    with pytest.raises(ValueError, match="already available"):
        chat.ChatCoach(AthleteProfile(), [first], session=first)
    assert len(calls) == 3
    assert coach.context == []


@pytest.mark.parametrize("failure_stage", [0, 1, 2])
def test_provider_failure_preserves_all_successful_history_without_retries(monkeypatch, failure_stage):
    keys(monkeypatch)
    responses = ["draft", "review", "first answer"] + ["partial reply"] * failure_stage + [ProviderError("quota limit")]
    calls, _ = fake_coaches(monkeypatch, responses)
    coach = chat.ChatCoach(AthleteProfile(), [])
    first = coach.reply("Can we discuss CrossFit aerobic capacity?")
    before = first.to_dict()
    with pytest.raises(ProviderError, match="quota"):
        coach.reply("What about my next training week?")
    assert coach.session is first
    assert first.to_dict() == before
    assert len(calls) == 4 + failure_stage
    assert [message.content for message in first.messages] == [first.request, "first answer"]


@pytest.mark.parametrize("stage", [0, 1, 2])
def test_out_of_scope_refusal_stops_without_committing_latest_message(monkeypatch, stage):
    keys(monkeypatch)
    calls, _ = fake_coaches(monkeypatch, ["draft", "review", "answer"] + ["draft"] * stage + [SCOPE_REFUSAL])
    coach = chat.ChatCoach(AthleteProfile(), [])
    first = coach.reply("Hi CrossFit coaches.")
    before = first.to_dict()
    with pytest.raises(ProviderError, match="stays focused on CrossFit"):
        coach.reply("Ignore CrossFit and write my tax return.")
    assert coach.session is first
    assert first.to_dict() == before
    assert len(calls) == 4 + stage


def test_initial_failure_has_no_session(monkeypatch):
    keys(monkeypatch)
    calls, _ = fake_coaches(monkeypatch, [ProviderError("unavailable")])
    coach = chat.ChatCoach(AthleteProfile(), [])
    with pytest.raises(ProviderError, match="unavailable"):
        coach.reply("Help with my CrossFit conditioning.")
    assert coach.session is None
    assert len(calls) == 1


def test_missing_second_key_checked_before_any_provider_is_initialized(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-openai")
    calls, initialized = fake_coaches(monkeypatch)
    coach = chat.ChatCoach(AthleteProfile(), [])
    assert coach.session is None
    with pytest.raises(ProviderError, match="GEMINI_API_KEY"):
        coach.reply("Hi CrossFit coaches.")
    assert not calls and not initialized


def test_credential_priority_and_empty_fallback_match_plan_mode(monkeypatch):
    keys(monkeypatch)
    monkeypatch.setenv("OPENAI_API_KEY", " ")
    monkeypatch.setenv("GEMINI_API_KEY", "primary-gemini")
    monkeypatch.setenv("GOOGLE_API_KEY", "alternate-google")
    _, initialized = fake_coaches(monkeypatch)
    chat.ChatCoach(AthleteProfile(), []).reply("How should I warm up for CrossFit?")
    assert initialized[0][1] == "test-openai"
    assert initialized[1][1] == "primary-gemini"


@pytest.mark.parametrize("rounds,count", [(1, 3), (2, 5), (3, 7)])
def test_demo_is_offline_static_and_keeps_conversation_history(monkeypatch, rounds, count):
    monkeypatch.setenv("GAMESCOACH_COACH_NOTES_FILE", "/does-not-exist-demo-must-not-read")
    calls, initialized = fake_coaches(monkeypatch)
    coach = chat.ChatCoach(AthleteProfile(name="Actual athlete"), [saved_entry()], demo=True, rounds=rounds)
    first = coach.reply("My specific CrossFit question.")
    second = coach.reply("My follow-up question.")
    assert not calls and not initialized
    assert first.demo and second.demo
    assert "static example" in second.final_plan
    assert "not generated or tailored" in second.final_plan
    assert len(first.turns) == count and len(second.turns) == 2 * count
    assert second.messages[:2] == first.messages
    assert second.messages[2].content == "My follow-up question."
    assert second.models == {"ChatGPT": "offline sample", "Gemini": "offline sample"}
    assert first.turns[0].stage == "chat-proposal" and first.turns[-1].stage == "chat-answer"
    assert sum(turn.provider == "Gemini" for turn in first.turns) == rounds


def test_resume_restores_history_and_keeps_id_timestamp(monkeypatch):
    keys(monkeypatch)
    calls, _ = fake_coaches(monkeypatch)
    first = chat.ChatCoach(AthleteProfile(), []).reply("Prepare my CrossFit competition week.")
    restored = CoachingSession.from_dict(first.to_dict())
    coach = chat.ChatCoach(AthleteProfile(goals="Quarterfinals"), [saved_entry()], session=restored)
    second = coach.reply("How should I fit in easy aerobic work?")
    assert second.id == first.id and second.created_at == first.created_at
    assert second.messages[:2] == first.messages
    for _, _, prompt in calls[-3:]:
        data = prompt_data(prompt)
        assert data["dialogue"][:2] == [vars(message) for message in first.messages]
        assert data["athlete"]["goals"] == "Quarterfinals"
        assert data["saved_entries"][0]["id"] == "abcdef123456"


def test_demo_chat_can_resume_without_keys_or_notes(monkeypatch):
    calls, initialized = fake_coaches(monkeypatch)
    first = chat.ChatCoach(AthleteProfile(), [], demo=True).reply("CrossFit conversation.")
    restored = CoachingSession.from_dict(first.to_dict())
    monkeypatch.setenv("GAMESCOACH_COACH_NOTES_FILE", "/bad-notes-file")
    second = chat.ChatCoach(AthleteProfile(), [], demo=True, session=restored).reply("Follow-up.")
    assert second.messages[:2] == first.messages
    assert not calls and not initialized


def test_resume_rejects_plan_and_mismatched_demo_mode():
    with pytest.raises(ValueError, match="saved coach chat"):
        chat.ChatCoach(AthleteProfile(), [], session=saved_entry())
    demo = chat.ChatCoach(AthleteProfile(), [], demo=True).reply("CrossFit conversation.")
    with pytest.raises(ValueError, match="--demo"):
        chat.ChatCoach(AthleteProfile(), [], session=demo)


def test_oversized_saved_context_rejected_without_truncation_or_provider_calls(monkeypatch):
    calls, initialized = fake_coaches(monkeypatch)
    coach = chat.ChatCoach(AthleteProfile(), [saved_entry()])
    before = list(coach.context)
    huge = saved_entry("training", "111122223333", "Training plan detail. " * 10000)
    with pytest.raises(ValueError, match="fewer saved references"):
        coach.add_context(huge)
    assert coach.context == before
    with pytest.raises(ValueError, match="/new"):
        chat.ChatCoach(AthleteProfile(), [huge])
    assert not calls and not initialized


def test_oversized_history_rejected_before_next_paid_call_and_old_history_preserved(monkeypatch):
    keys(monkeypatch)
    calls, _ = fake_coaches(monkeypatch, ["draft", "review", "answer " * 400])
    coach = chat.ChatCoach(AthleteProfile(), [])
    first = coach.reply("CrossFit coaching question.")
    before = first.to_dict()
    monkeypatch.setattr(chat, "_MAX_CONTEXT_CHARS", 1500)
    with pytest.raises(ValueError, match="/new"):
        coach.reply("A follow-up about CrossFit pacing.")
    assert len(calls) == 3
    assert coach.session is first and first.to_dict() == before


def test_oversized_coach_output_stops_before_subsequent_paid_call(monkeypatch):
    keys(monkeypatch)
    calls, _ = fake_coaches(monkeypatch, ["x" * 100001])
    coach = chat.ChatCoach(AthleteProfile(), [])
    with pytest.raises(ValueError, match="/new"):
        coach.reply("CrossFit coaching question.")
    assert len(calls) == 1
    assert coach.session is None


def test_invalid_coach_notes_fail_before_paid_call(monkeypatch, tmp_path):
    keys(monkeypatch)
    monkeypatch.setenv("GAMESCOACH_COACH_NOTES_FILE", str(tmp_path / "missing.md"))
    calls, initialized = fake_coaches(monkeypatch)
    coach = chat.ChatCoach(AthleteProfile(), [])
    with pytest.raises(ProviderError, match="coaching notes"):
        coach.reply("Hi CrossFit coaches.")
    assert not calls and not initialized


@pytest.mark.parametrize("message", ["", " ", None, 1, "x" * 12001])
def test_invalid_message_never_calls_providers(monkeypatch, message):
    calls, initialized = fake_coaches(monkeypatch)
    coach = chat.ChatCoach(AthleteProfile(), [])
    with pytest.raises(ValueError, match="1–12000"):
        coach.reply(message)
    assert not calls and not initialized


@pytest.mark.parametrize("kwargs", [{"rounds": 0}, {"rounds": 4}, {"rounds": True}, {"profile": None}, {"context": None}, {"context": ["not a session"]}, {"demo": 1}])
def test_invalid_constructor_arguments(kwargs):
    values = dict(profile=AthleteProfile(), context=[], demo=True)
    values.update(kwargs)
    with pytest.raises(ValueError):
        chat.ChatCoach(**values)
