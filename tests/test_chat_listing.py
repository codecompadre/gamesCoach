from dataclasses import replace
import os

import pytest

from gamescoach.chat import ChatCoach
from gamescoach.engine import create_session
from gamescoach.models import AthleteProfile
from gamescoach.storage import Store, StoreError


def example_chat(identifier, created_at):
    sample = ChatCoach(AthleteProfile(), [], demo=True).reply("Example CrossFit chat")
    return replace(sample, id=identifier, created_at=created_at)


def test_latest_chat_uses_last_save_even_if_older_thread_started_first(tmp_path):
    store = Store(tmp_path)
    older = example_chat("111122223333", "2026-01-01T12:00:00+00:00")
    newer = example_chat("444455556666", "2026-10-14T12:00:00+00:00")
    store.save_chat(older)
    store.save_chat(newer)
    older_path = tmp_path / "sessions" / f"{older.id}.json"
    newer_path = tmp_path / "sessions" / f"{newer.id}.json"
    os.utime(older_path, ns=(10_000, 10_000))
    os.utime(newer_path, ns=(20_000, 20_000))
    assert [chat.id for chat in store.list_chats(demo=True)] == [newer.id, older.id]
    # Saving an older conversation after a follow-up makes it the latest active one.
    store.save_chat(older)
    assert [chat.id for chat in store.list_chats(demo=True)] == [older.id, newer.id]


def test_chat_picker_filters_live_demo_and_scans_beyond_recent_plans(tmp_path):
    store = Store(tmp_path)
    demo = example_chat("111122223333", "2026-01-01T12:00:00+00:00")
    live = replace(example_chat("444455556666", "2026-01-02T12:00:00+00:00"), demo=False)
    store.save_chat(demo)
    store.save_chat(live)
    for number in range(15):
        store.save_session(create_session("workout", f"CrossFit workout {number}", AthleteProfile(), demo=True))
    assert store.list_chats(demo=True) == [demo]
    assert store.list_chats(demo=False) == [live]


def test_empty_picker_does_not_create_data_directories(tmp_path):
    root = tmp_path / "not-created"
    assert Store(root).list_chats(demo=False) == []
    assert not root.exists()


def test_chat_picker_validates_mode_and_reports_corrupt_history(tmp_path):
    store = Store(tmp_path)
    with pytest.raises(StoreError, match="live or demo"):
        store.list_chats(demo="yes")
    (tmp_path / "sessions").mkdir()
    (tmp_path / "sessions" / "111122223333.json").write_text("invalid json")
    with pytest.raises(StoreError, match="JSON contents"):
        store.list_chats(demo=True)
