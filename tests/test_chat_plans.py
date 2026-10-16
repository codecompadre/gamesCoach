"""Structured chat plans reuse the live conversation without another coaching round."""

from datetime import datetime, timezone
import json

import pytest

from gamescoach import chat
from gamescoach.models import AthleteProfile, CoachTurn, CoachingSession
from gamescoach.prompts import COACH_INSTRUCTIONS, TASK_GUIDANCE
from gamescoach.providers import ProviderError
from gamescoach.storage import Store


@pytest.fixture(autouse=True)
def isolated_settings(monkeypatch, tmp_path):
    monkeypatch.setenv("GAMESCOACH_DATA_DIR", str(tmp_path / "data"))
    for key in (
        "OPENAI_API_KEY", "GAMESCOACH_OPENAI_API_KEY", "GEMINI_API_KEY",
        "GOOGLE_API_KEY", "GAMESCOACH_GEMINI_API_KEY", "GAMESCOACH_COACH_NOTES_FILE",
        "OPENAI_MODEL", "GEMINI_MODEL",
    ):
        monkeypatch.delenv(key, raising=False)


def coaches(monkeypatch, responses=None):
    calls = []
    for classname, name in (("OpenAIProvider", "ChatGPT"), ("GeminiProvider", "Gemini")):
        class Coach:
            def __init__(self, key, model, coach_name=name):
                self.name = coach_name
                self.model = model

            def generate(self, *, instructions, prompt):
                calls.append((self.name, instructions, prompt))
                response = responses[len(calls) - 1] if responses else f"CrossFit response {len(calls)}"
                if isinstance(response, Exception):
                    raise response
                return response

        monkeypatch.setattr(chat, classname, Coach)
    monkeypatch.setenv("OPENAI_API_KEY", "fake-openai-key")
    monkeypatch.setenv("GEMINI_API_KEY", "fake-gemini-key")
    return calls


def data(prompt):
    return json.loads(prompt.split("\n", 1)[1].split("\nYour turn:", 1)[0])


def saved_workout():
    return CoachingSession(
        id="abcdef123456", created_at="2020-01-01T12:00:00+00:00",
        kind="workout", request="My current CrossFit week",
        profile=AthleteProfile(),
        turns=[CoachTurn("ChatGPT", "synthesis", "Tuesday: easy bike at conversational effort.")],
        final_plan="Tuesday: easy bike at conversational effort.",
    )


@pytest.mark.parametrize("kind", tuple(TASK_GUIDANCE))
def test_plan_uses_both_coaches_and_chat_context_then_roundtrips_as_separate_record(monkeypatch, tmp_path, kind):
    calls = coaches(monkeypatch)
    notes = tmp_path / "coach-notes.md"
    notes.write_text("Prioritize repeatable aerobic work for CrossFit performance.")
    monkeypatch.setenv("GAMESCOACH_COACH_NOTES_FILE", str(notes))
    source = {
        "guide_id": "abcdef1234567890", "filename": "CrossFit methodology.pdf",
        "pdf_page": 12, "text": "Mechanics, consistency, then intensity.",
    }

    class PreparedLibrary:
        def brief(self):
            return [dict(source)]

        def select(self, query):
            return [dict(source)]

    monkeypatch.setattr(chat, "Library", PreparedLibrary)
    profile = AthleteProfile(goals="CrossFit Quarterfinals", training_days_per_week=4)
    entry = saved_workout()
    coach = chat.ChatCoach(profile, [entry])
    first = coach.reply("Remember my wrists get uncomfortable during handstands.")
    # A resumed old thread retains its original creation time; a new plan must not.
    first.created_at = "2020-01-02T12:00:00+00:00"
    before = first.to_dict()
    started = datetime.now(timezone.utc)
    observed = []
    request = "Build an actionable CrossFit plan using my current training."
    result = coach.reply(request, on_turn=observed.append, plan_kind=kind)
    plan = coach.last_plan

    assert len(calls) == 6
    assert [item[0] for item in calls[-3:]] == ["Gemini", "ChatGPT", "Gemini"]
    assert len(observed) == 3
    for _, instructions, prompt in calls[-3:]:
        supplied = data(prompt)
        assert instructions.startswith(COACH_INSTRUCTIONS)
        assert TASK_GUIDANCE[kind] in instructions
        assert "STRUCTURED PLAN REQUEST" in instructions
        assert "standalone plan" in instructions
        assert supplied["plan_kind"] == kind
        assert supplied["athlete"] == profile.to_dict()
        assert supplied["dialogue"][:2] == [vars(message) for message in first.messages]
        assert supplied["dialogue"][-1]["content"] == request
        assert supplied["saved_entries"][0]["final_plan"] == entry.final_plan
        assert supplied["optional_coaching_notes"] == notes.read_text()
        assert supplied["methodology_brief"] == [source]
        assert supplied["methodology_excerpts"] == [source]
        assert "handstands" in supplied["dialogue"][0]["content"]

    assert first.to_dict() == before
    assert result.id == first.id and result.created_at == first.created_at
    assert result.messages[:2] == first.messages
    assert result.messages[-1].content == plan.final_plan == result.final_plan
    assert plan.kind == kind and plan.id != result.id
    assert datetime.fromisoformat(plan.created_at) >= started
    assert plan.request == request
    assert plan.profile == profile and plan.profile is not coach.profile
    assert plan.turns == result.turns[-3:] == observed
    assert plan.turns[0] is not result.turns[-3]
    assert plan.messages == [] and plan.context_ids == []
    assert plan.models == result.models
    assert plan.methodology_brief == result.methodology_brief == [source]
    assert plan.methodology_excerpts == result.methodology_excerpts == [source]
    assert coach.context == [entry]  # Saving/attaching the separate plan is the CLI's responsibility.
    store = Store()
    store.save_session(plan)
    store.save_chat(result)
    assert store.get_session(plan.id).to_dict() == plan.to_dict()
    assert store.get_session(result.id).to_dict() == result.to_dict()


@pytest.mark.parametrize("failure_stage", [0, 1, 2])
@pytest.mark.parametrize("next_kind", [None, "training"])
def test_failed_reply_keeps_previous_chat_and_last_successful_plan(monkeypatch, failure_stage, next_kind):
    responses = ["draft", "review", "saved plan"] + ["partial reply"] * failure_stage + [ProviderError("quota limit")]
    calls = coaches(monkeypatch, responses)
    coach = chat.ChatCoach(AthleteProfile(), [])
    first = coach.reply("Create a CrossFit workout.", plan_kind="workout")
    previous_plan = coach.last_plan
    chat_before, plan_before = first.to_dict(), previous_plan.to_dict()

    with pytest.raises(ProviderError, match="quota limit"):
        coach.reply("Adapt this to next week.", plan_kind=next_kind)

    assert coach.session is first and coach.last_plan is previous_plan
    assert first.to_dict() == chat_before
    assert previous_plan.to_dict() == plan_before
    assert len(calls) == 4 + failure_stage


def test_normal_success_clears_last_plan_without_creating_an_extra_record(monkeypatch):
    calls = coaches(monkeypatch)
    coach = chat.ChatCoach(AthleteProfile(), [])
    first = coach.reply("Create a CrossFit session.", plan_kind="workout")
    plan = coach.last_plan
    result = coach.reply("Explain why the aerobic work is easy.")
    assert coach.last_plan is None
    assert result.messages[:2] == first.messages
    assert plan.final_plan == first.final_plan
    assert len(calls) == 6
    assert "plan_kind" not in data(calls[-1][2])
    assert "STRUCTURED PLAN REQUEST" not in calls[-1][1]


def test_injected_library_is_used_for_initial_context_and_each_reply(monkeypatch):
    calls = coaches(monkeypatch)
    source = {
        "guide_id": "a" * 16, "filename": "Custom CrossFit library.pdf",
        "pdf_page": 4, "text": "Mechanics, consistency, intensity.",
    }

    class PreparedLibrary:
        def __init__(self):
            self.brief_calls = 0
            self.queries = []

        def brief(self):
            self.brief_calls += 1
            return [dict(source)]

        def select(self, query):
            self.queries.append(query)
            return [dict(source)]

    def forbidden_default_library():
        raise AssertionError("An injected library must not load the default data directory")

    library = PreparedLibrary()
    monkeypatch.setattr(chat, "Library", forbidden_default_library)
    coach = chat.ChatCoach(AthleteProfile(), [], library=library)
    coach.reply("Plan my CrossFit aerobic work.", plan_kind="workout")
    coach.reply("Explain the progression.")

    assert coach.library is library
    assert library.brief_calls == 3
    assert len(library.queries) == 2
    assert len(calls) == 6
    for _, _, prompt in calls:
        supplied = data(prompt)
        assert supplied["methodology_brief"] == [source]
        assert supplied["methodology_excerpts"] == [source]


@pytest.mark.parametrize("kind", ["", "chat", "document", "taxes", 1, [], {}])
@pytest.mark.parametrize("demo", [False, True])
def test_invalid_plan_kind_is_rejected_before_any_paid_call(monkeypatch, kind, demo):
    calls = coaches(monkeypatch)
    coach = chat.ChatCoach(AthleteProfile(), [], demo=demo)
    with pytest.raises(ValueError, match="Choose workout, training, nutrition or competition"):
        coach.reply("Create something.", plan_kind=kind)
    assert not calls
    assert coach.session is None and coach.last_plan is None


@pytest.mark.parametrize("kind", tuple(TASK_GUIDANCE))
@pytest.mark.parametrize("rounds,turn_count", [(1, 3), (2, 5), (3, 7)])
def test_demo_plan_is_structured_offline_and_labeled(monkeypatch, kind, rounds, turn_count):
    calls = coaches(monkeypatch)
    coach = chat.ChatCoach(AthleteProfile(name="Actual athlete"), [], rounds=rounds, demo=True)
    result = coach.reply("My specific CrossFit plan request.", plan_kind=kind)
    plan = coach.last_plan
    assert not calls
    assert plan.kind == kind and plan.demo and result.demo
    assert plan.final_plan == result.final_plan
    assert "OFFLINE DEMO" in plan.final_plan
    assert "not generated or tailored" in plan.final_plan
    assert "## Example CrossFit" in plan.final_plan
    assert len(plan.turns) == len(result.turns) == turn_count
    assert CoachingSession.from_dict(plan.to_dict()).to_dict() == plan.to_dict()
