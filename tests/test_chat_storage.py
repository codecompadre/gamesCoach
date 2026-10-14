from dataclasses import replace
import json
import stat

import pytest

from gamescoach.engine import create_session
from gamescoach.models import AthleteProfile, ChatMessage, CoachTurn, CoachingSession
from gamescoach.storage import Store, StoreError


def conversation():
    return CoachingSession(
        id="abcdef123456",
        created_at="2026-10-14T12:00:00+00:00",
        kind="chat",
        request="How should I pace a CrossFit workout?",
        profile=AthleteProfile(),
        turns=[CoachTurn("ChatGPT", "chat-answer", "Start at a sustainable effort.")],
        final_plan="Start at a sustainable effort.",
        demo=True,
        messages=[ChatMessage("user", "How should I pace a CrossFit workout?"), ChatMessage("assistant", "Start at a sustainable effort.")],
    )


def test_chat_updates_same_file_without_losing_earlier_exchanges(tmp_path):
    store = Store(tmp_path)
    first = conversation()
    store.save_chat(first)
    second = replace(first,
        messages=first.messages + [ChatMessage("user", "What should I record?"), ChatMessage("assistant", "Record repeatable splits.")],
        turns=first.turns + [CoachTurn("ChatGPT", "chat-answer", "Record repeatable splits.")],
        final_plan="Record repeatable splits.",
    )
    store.save_chat(second)
    assert store.get_session(first.id).to_dict() == second.to_dict()
    assert len(store.list_sessions()) == 1
    assert stat.S_IMODE((tmp_path / "sessions" / f"{first.id}.json").stat().st_mode) == 0o600
    assert not list((tmp_path / "sessions").glob("*.tmp"))


def test_failed_atomic_update_preserves_previous_chat(tmp_path, monkeypatch):
    store = Store(tmp_path)
    first = conversation()
    store.save_chat(first)
    before = (tmp_path / "sessions" / f"{first.id}.json").read_bytes()
    def fail(*args):
        raise OSError("simulated write failure")
    monkeypatch.setattr("gamescoach.storage.os.replace", fail)
    with pytest.raises(StoreError, match="Cannot save conversation"):
        store.save_chat(first)
    assert (tmp_path / "sessions" / f"{first.id}.json").read_bytes() == before
    assert not list((tmp_path / "sessions").glob("*.tmp"))


def test_chat_cannot_overwrite_a_saved_workout(tmp_path):
    store = Store(tmp_path)
    plan = create_session("workout", "CrossFit workout", AthleteProfile(), demo=True)
    store.save_session(plan)
    before = store.get_session(plan.id).to_dict()
    with pytest.raises(StoreError, match="not overwritten"):
        store.save_chat(replace(conversation(), id=plan.id))
    assert store.get_session(plan.id).to_dict() == before
    with pytest.raises(StoreError, match="Only conversations"):
        store.save_chat(plan)


def test_legacy_plan_json_remains_readable(tmp_path):
    store = Store(tmp_path)
    plan = create_session("workout", "CrossFit workout", AthleteProfile(), demo=True)
    store.save_session(plan)
    path = tmp_path / "sessions" / f"{plan.id}.json"
    legacy = json.loads(path.read_text())
    del legacy["messages"]
    path.write_text(json.dumps(legacy))
    assert store.get_session(plan.id).messages == []


@pytest.mark.parametrize("messages", [[], [ChatMessage("user", "Unanswered")], [ChatMessage("assistant", "Wrong"), ChatMessage("user", "Order")]])
def test_invalid_chat_exchanges_cannot_be_saved(messages):
    with pytest.raises(ValueError):
        replace(conversation(), messages=messages)


def test_chat_message_role_and_summary_validation():
    with pytest.raises(ValueError):
        ChatMessage("system", "Attempted instruction")
    with pytest.raises(ValueError):
        replace(conversation(), final_plan="Does not match latest response")
