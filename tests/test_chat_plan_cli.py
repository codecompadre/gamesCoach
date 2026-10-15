"""Exercise structured plan shortcuts through the persistent coach console."""

import io
import json

import pytest
from rich.console import Console

from gamescoach import chat, cli
from gamescoach.engine import create_session
from gamescoach.models import AthleteProfile
from gamescoach.storage import Store, StoreError


@pytest.fixture(autouse=True)
def private_settings(monkeypatch, tmp_path):
    monkeypatch.setenv("GAMESCOACH_DATA_DIR", str(tmp_path / "data"))
    for name in (
        "OPENAI_API_KEY", "GAMESCOACH_OPENAI_API_KEY", "GEMINI_API_KEY",
        "GOOGLE_API_KEY", "GAMESCOACH_GEMINI_API_KEY", "OPENAI_MODEL",
        "GEMINI_MODEL", "GAMESCOACH_COACH_NOTES_FILE",
    ):
        monkeypatch.delenv(name, raising=False)


def console_input(monkeypatch, messages):
    output = io.StringIO()
    console = Console(file=output, width=140, highlight=False)
    entries = iter(messages)
    monkeypatch.setattr(console, "input", lambda prompt: next(entries))
    return console, output


@pytest.mark.parametrize("kind", tuple(cli.DEFAULT_REQUESTS))
@pytest.mark.parametrize("custom", [False, True])
def test_shortcut_saves_plan_and_chat_and_displays_one_answer(monkeypatch, kind, custom):
    store = Store()
    request = "Use four days, bike intervals and familiar comfortable CrossFit movements."
    command = f"/{kind} {request}" if custom else f"/{kind}"
    console, output = console_input(monkeypatch, [command, "/quit"])
    rendered = []
    monkeypatch.setattr(cli, "render_coach", lambda console, content, **kwargs: rendered.append(content))

    cli._chat(console, store, demo=True, context_limit=0)

    records = store.list_sessions()
    assert len(records) == 2
    plan, = [entry for entry in records if entry.kind == kind]
    thread, = [entry for entry in records if entry.kind == "chat"]
    expected_request = request if custom else cli.DEFAULT_REQUESTS[kind]
    assert plan.request == expected_request
    assert thread.request == expected_request
    assert thread.messages[0].content == expected_request
    assert len(thread.messages) == 2
    assert plan.final_plan == thread.final_plan
    assert rendered == [plan.final_plan]
    assert plan.demo and thread.demo
    assert thread.context_ids == []
    assert f"Saved {cli.KIND_LABELS[kind]}:" in output.getvalue()
    assert "Saved chat:" in output.getvalue()


def test_structured_shortcut_help_is_local_and_does_not_start_a_plan(monkeypatch):
    store = Store()
    console, output = console_input(monkeypatch, ["/workout --help", "/training --help", "/nutrition --help", "/competition --help", "/quit"])

    def forbidden_reply(*args, **kwargs):
        raise AssertionError("Shortcut help must not call the coaches")

    monkeypatch.setattr(cli.ChatCoach, "reply", forbidden_reply)
    cli._chat(console, store, context_limit=0)
    assert store.list_sessions() == []
    assert "Live coaching needs" not in output.getvalue()
    for kind in cli.DEFAULT_REQUESTS:
        assert f"/{kind}" in output.getvalue()


def test_saved_plan_stays_in_dialogue_without_duplicate_reference_and_survives_resume(monkeypatch):
    store = Store()
    seed = create_session("training", "Earlier CrossFit week", AthleteProfile(), demo=True, rounds=1)
    seed.created_at = "2020-01-01T12:00:00+00:00"
    store.save_session(seed)
    console, _ = console_input(monkeypatch, ["/workout Easy bike CrossFit intervals", "How should I pace that?", "/quit"])
    cli._chat(console, store, demo=True)
    records = store.list_sessions()
    plan, = [entry for entry in records if entry.kind == "workout"]
    first, = [entry for entry in records if entry.kind == "chat"]
    assert len(records) == 3
    assert first.context_ids == [seed.id]
    assert len(first.messages) == 4
    assert first.messages[1].content == plan.final_plan

    console, _ = console_input(monkeypatch, ["/nutrition Fuel my CrossFit intervals", "How should I hydrate?", "/quit"])
    cli._chat(console, store, demo=True, resume=first.id)
    records = store.list_sessions()
    updated, = [entry for entry in records if entry.kind == "chat"]
    nutrition, = [entry for entry in records if entry.kind == "nutrition"]
    assert len(records) == 4
    assert updated.id == first.id and updated.created_at == first.created_at
    assert len(updated.messages) == 8
    assert updated.messages[:4] == first.messages
    assert updated.messages[5].content == nutrition.final_plan
    assert updated.context_ids == [seed.id]
    assert store.get_session(plan.id).to_dict() == plan.to_dict()


def test_plan_save_failure_still_saves_chat_and_allows_followup(monkeypatch):
    store = Store()
    console, output = console_input(monkeypatch, ["/workout Comfortable CrossFit intervals", "Can I use a bike?", "/quit"])

    def fail_plan_save(plan):
        raise StoreError("Cannot save separate plan")

    monkeypatch.setattr(store, "save_session", fail_plan_save)
    cli._chat(console, store, demo=True, context_limit=0)
    thread, = store.list_sessions()
    assert thread.kind == "chat" and len(thread.messages) == 4
    assert thread.messages[0].content == "Comfortable CrossFit intervals"
    assert thread.messages[2].content == "Can I use a bike?"
    assert "Cannot save separate plan" in output.getvalue()
    assert "Saved chat:" in output.getvalue()
    assert "Saved CrossFit workout:" not in output.getvalue()


def test_chat_save_failure_preserves_independent_saved_plan(monkeypatch):
    store = Store()
    console, output = console_input(monkeypatch, ["/competition Prepare for my CrossFit event", "/quit"])

    def fail_chat_save(thread):
        raise StoreError("Cannot save chat")

    monkeypatch.setattr(store, "save_chat", fail_chat_save)
    cli._chat(console, store, demo=True, context_limit=0)
    plan, = store.list_sessions()
    assert plan.kind == "competition"
    assert plan.request == "Prepare for my CrossFit event"
    assert "Saved CrossFit competition preparation:" in output.getvalue()
    assert "Cannot save chat" in output.getvalue()
    assert "Saved chat:" not in output.getvalue()


def test_live_shortcut_keeps_prior_constraints_with_same_three_call_review(monkeypatch):
    calls = []
    for classname, label in (("OpenAIProvider", "ChatGPT"), ("GeminiProvider", "Gemini")):
        class Provider:
            def __init__(self, key, model, name=label):
                self.name = name
                self.model = model

            def generate(self, *, instructions, prompt):
                calls.append((self.name, instructions, prompt))
                return f"Live mocked CrossFit reply {len(calls)}"

        monkeypatch.setattr(chat, classname, Provider)
    monkeypatch.setenv("OPENAI_API_KEY", "fake-openai-key")
    monkeypatch.setenv("GEMINI_API_KEY", "fake-gemini-key")
    store = Store()
    store.save_profile(AthleteProfile(goals="CrossFit Quarterfinals", limitations="Sensitive wrists"))
    console, _ = console_input(monkeypatch, ["I can train four days each week.", "/training Build my next CrossFit week", "/quit"])
    cli._chat(console, store, context_limit=0)

    assert len(calls) == 6
    assert [call[0] for call in calls] == ["ChatGPT", "Gemini", "ChatGPT"] * 2
    for _, _, prompt in calls[-3:]:
        supplied = json.loads(prompt.split("\n", 1)[1].split("\nYour turn:", 1)[0])
        assert supplied["plan_kind"] == "training"
        assert supplied["athlete"]["limitations"] == "Sensitive wrists"
        assert supplied["dialogue"][0]["content"] == "I can train four days each week."
    records = store.list_sessions()
    plan, = [entry for entry in records if entry.kind == "training"]
    thread, = [entry for entry in records if entry.kind == "chat"]
    assert len(thread.messages) == 4
    assert plan.final_plan == thread.final_plan == "Live mocked CrossFit reply 6"
