"""Automatic retained methodology context reaches every coaching stage."""

import json

import pytest

from gamescoach import chat, engine
from gamescoach.knowledge import KnowledgeError, Library
from gamescoach.models import AthleteProfile, CoachingSession
from gamescoach.storage import Store


@pytest.fixture
def methodology(monkeypatch, tmp_path):
    monkeypatch.setenv("GAMESCOACH_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.delenv("GAMESCOACH_COACH_NOTES_FILE", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "test-openai")
    monkeypatch.setenv("GEMINI_API_KEY", "test-gemini")
    library = Library()
    library.root.mkdir(parents=True)
    for identifier, filename, passages in (
        ("a" * 16, "Methodology.pdf", [
            {"page": 1, "text": "CrossFit methodology: develop mechanics and consistency before intensity."},
            {"page": 2, "text": "Aerobic endurance develops through sustainable repeatable conditioning."},
        ]),
        ("b" * 16, "Competitors.pdf", [
            {"page": 1, "text": "Competition programming prioritizes weaknesses, recovery and progression."},
            {"page": 2, "text": "Strength and gymnastics skill practice needs quality and rest."},
        ]),
    ):
        (library.root / f"{identifier}.json").write_text(json.dumps({
            "id": identifier, "filename": filename, "pages": 2,
            "pages_without_text": 0, "passages": passages,
        }))
    return library


def simulated_coaches(monkeypatch, module):
    calls = []
    for classname, label in (("OpenAIProvider", "ChatGPT"), ("GeminiProvider", "Gemini")):
        class Coach:
            def __init__(self, key, model, name=label):
                self.name, self.model = name, model

            def generate(self, *, instructions, prompt):
                data = json.loads(prompt.split("\n", 1)[1].split("\nTask focus:", 1)[0].split("\nYour turn:", 1)[0])
                calls.append((self.name, instructions, data))
                return "Keep the intended CrossFit stimulus and scale to the athlete."

        monkeypatch.setattr(module, classname, Coach)
    return calls


@pytest.mark.parametrize("kind", ["workout", "training", "nutrition", "competition"])
def test_every_plan_stage_gets_automatic_core_knowledge_without_asking(methodology, monkeypatch, kind):
    ready = methodology.prepare()
    assert ready["guides"] == 2 and ready["pages"] == 4
    calls = simulated_coaches(monkeypatch, engine)
    result = engine.create_session(kind, "Help me improve my CrossFit performance.", AthleteProfile(), rounds=1)
    assert [name for name, _, _ in calls] == ["Gemini", "ChatGPT", "Gemini"]
    assert result.methodology_brief == methodology.brief()
    assert {item["filename"] for item in result.methodology_brief} == {"Methodology.pdf", "Competitors.pdf"}
    for _, instructions, data in calls:
        assert data["methodology_brief"] == result.methodology_brief
        assert data["methodology_excerpts"] == result.methodology_excerpts
        assert "do not wait for the athlete" in instructions
    store = Store()
    store.save_session(result)
    assert store.get_session(result.id).methodology_brief == result.methodology_brief


def test_core_knowledge_ready_before_chat_and_survives_new_resume_and_clearrefs(methodology, monkeypatch):
    calls = simulated_coaches(monkeypatch, chat)
    expected = methodology.brief()
    coach = chat.ChatCoach(AthleteProfile(), [])
    assert coach.methodology_brief == expected
    first = coach.reply("How should I pace my CrossFit workout?")
    store = Store()
    store.save_chat(first)
    resumed = chat.ChatCoach(AthleteProfile(), [], session=store.get_session(first.id))
    resumed.clear_context()
    second = resumed.reply("What should I focus on next week?")
    third = chat.ChatCoach(AthleteProfile(), []).reply("How do I structure gymnastics practice?")
    assert second.methodology_brief == third.methodology_brief == expected
    assert second.context_ids == []
    assert len(calls) == 9
    assert all(data["methodology_brief"] == expected for _, _, data in calls)
    # Older saved records acquire the current library on resume without migration.
    old = first.to_dict()
    del old["methodology_brief"]
    legacy = CoachingSession.from_dict(old)
    assert legacy.methodology_brief == []
    assert chat.ChatCoach(AthleteProfile(), [], session=legacy).methodology_brief == expected


def test_demo_does_not_load_or_claim_retained_model_knowledge(methodology, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Demo should not load live methodology context")

    monkeypatch.setattr(Library, "brief", forbidden)
    monkeypatch.setattr(Library, "select", forbidden)
    plan = engine.create_session("workout", "CrossFit", AthleteProfile(), demo=True)
    reply = chat.ChatCoach(AthleteProfile(), [], demo=True).reply("CrossFit")
    assert plan.methodology_brief == reply.methodology_brief == []


def test_library_failure_prevents_any_paid_generation(methodology, monkeypatch):
    plan_calls = simulated_coaches(monkeypatch, engine)
    chat_calls = simulated_coaches(monkeypatch, chat)
    def broken(*args, **kwargs):
        raise KnowledgeError("Cannot prepare the coaching library")

    monkeypatch.setattr(Library, "brief", broken)
    with pytest.raises(KnowledgeError):
        engine.create_session("workout", "CrossFit", AthleteProfile())
    with pytest.raises(KnowledgeError):
        chat.ChatCoach(AthleteProfile(), [])
    assert plan_calls == chat_calls == []


@pytest.mark.parametrize("brief", [None, [{}], [{"guide_id": "a" * 16, "filename": "Guide.pdf", "pdf_page": 1, "text": "x" * 901}]])
def test_invalid_saved_core_passages_are_rejected(brief):
    plan = engine.create_session("workout", "CrossFit", AthleteProfile(), demo=True).to_dict()
    plan["methodology_brief"] = brief
    with pytest.raises(ValueError, match="Methodology"):
        CoachingSession.from_dict(plan)
