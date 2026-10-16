import json
import os
from pathlib import Path
import stat

import pytest

from gamescoach.engine import _coach_notes
from gamescoach.knowledge import Library
from gamescoach.library_sources import sync_folder
from gamescoach.memory import memory_settings, memory_status, reset_memory, save_memory_settings
from gamescoach.storage import Store, StoreError


@pytest.fixture
def store(monkeypatch, tmp_path):
    root = tmp_path / "private"
    monkeypatch.setenv("GAMESCOACH_DATA_DIR", str(root))
    monkeypatch.delenv("GAMESCOACH_COACH_NOTES_FILE", raising=False)
    return Store(root)


def write(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def seed(store):
    for identifier, kind in (("a" * 12, "chat"), ("b" * 12, "workout"), ("c" * 12, "document")):
        write(store.root / "sessions" / f"{identifier}.json", json.dumps({"kind": kind}))
    write(store.root / "sessions" / "broken.json", "broken retention")
    write(store.root / "sessions" / ".chat-interrupted.tmp", "old chat content")
    write(store.root / "profile.json", '{"name": "Athlete"}')
    guide = {
        "id": "a" * 16, "filename": "Methodology.pdf", "pages": 1,
        "passages": [{"page": 1, "text": "CrossFit mechanics consistency intensity aerobic conditioning."}],
    }
    write(store.root / "library" / f"{guide['id']}.json", json.dumps(guide))
    write(store.root / "library" / "sources" / "folder.json", "broken folder registration")
    Library(store.root / "library").prepare()


def test_defaults_are_read_only_and_settings_roundtrip_privately(store):
    assert memory_settings(store) == {"auto_references": True, "coach_notes": True}
    assert not store.root.exists()
    settings = {"auto_references": False, "coach_notes": True}
    assert save_memory_settings(settings, store) == settings
    assert memory_settings(Store(store.root)) == settings
    assert stat.S_IMODE((store.root / "memory.json").stat().st_mode) == 0o600
    assert stat.S_IMODE(store.root.stat().st_mode) == 0o700
    assert list(store.root.iterdir()) == [store.root / "memory.json"]


@pytest.mark.parametrize("settings", [
    None, [], {}, {"auto_references": True},
    {"auto_references": True, "coach_notes": True, "other": False},
    {"auto_references": 1, "coach_notes": True},
    {"auto_references": False, "coach_notes": "false"},
])
def test_invalid_settings_never_create_or_change_local_data(store, settings):
    with pytest.raises(StoreError, match="both true or false"):
        save_memory_settings(settings, store)
    assert not store.root.exists()


def test_summary_can_inspect_corrupted_sessions_profile_and_folder_registration(store):
    seed(store)
    assert memory_status(store) == {
        "sessions": 4, "chats": 1, "plans": 1, "documents": 1,
        "guides": 1, "unknown_sessions": 1, "has_profile": True,
        "watched_folder": True,
        "settings": {"auto_references": True, "coach_notes": True},
    }


def test_deeply_nested_corrupt_session_is_counted_and_full_reset_recovers(store):
    write(store.root / "sessions" / "abcdef123456.json", "[" * 10000 + "0" + "]" * 10000)
    status = memory_status(store)
    assert status["sessions"] == 1 and status["unknown_sessions"] == 1
    reset_memory("all", "RESET", store)
    assert memory_status(store)["sessions"] == 0


def test_deeply_nested_memory_settings_report_actionable_error_and_full_reset_recovers(store, monkeypatch):
    # Python versions differ in the JSON nesting limit; this also covers the
    # shorter settings-file parser when its decoder reaches that limit.
    write(store.root / "memory.json", "{}")
    with monkeypatch.context() as patch:
        patch.setattr("gamescoach.memory.json.loads", lambda *_: (_ for _ in ()).throw(RecursionError()))
        with pytest.raises(StoreError, match="full memory reset"):
            memory_settings(store)
    reset_memory("all", "RESET", store)
    assert memory_settings(store) == {"auto_references": False, "coach_notes": False}


@pytest.mark.parametrize("scope,confirmation", [
    ("history", "reset"), ("all", " RESET"), ("all", "RESET\n"),
    ("history", ""), ("everything", "RESET"), (None, "RESET"),
])
def test_reset_requires_exact_scope_and_confirmation_before_any_change(store, scope, confirmation):
    seed(store)
    before = {path: path.read_bytes() for path in store.root.rglob("*") if path.is_file()}
    with pytest.raises(StoreError):
        reset_memory(scope, confirmation, store)
    assert {path: path.read_bytes() for path in before} == before
    assert not (store.root / "memory.json").exists()


def test_history_reset_removes_chats_plans_imports_and_corruption_retaining_profile_library_settings(store):
    seed(store)
    settings = {"auto_references": False, "coach_notes": True}
    save_memory_settings(settings, store)
    profile = (store.root / "profile.json").read_bytes()
    guides = Library(store.root / "library").list_guides()
    folder = (store.root / "library" / "sources" / "folder.json").read_bytes()
    result = reset_memory("history", "RESET", store)
    assert result["deleted"] == {
        "sessions": 4, "guides": 0, "profile": False,
        "watched_folder": False, "prepared_cache": False,
    }
    assert list((store.root / "sessions").iterdir()) == []
    assert (store.root / "profile.json").read_bytes() == profile
    assert Library(store.root / "library").list_guides() == guides
    assert (store.root / "library" / "sources" / "folder.json").read_bytes() == folder
    assert result["status"]["sessions"] == 0
    assert memory_settings(store) == settings


def test_full_reset_starts_clean_and_prevents_notes_and_watched_pdfs_returning(store, tmp_path, monkeypatch):
    seed(store)
    originals = tmp_path / "originals"
    write(originals / "Guide.pdf", "original PDF left untouched")
    write(originals / "Week.docx", "original workout left untouched")
    write(originals / "coach-notes.md", "Retained old coaching emphasis.")
    monkeypatch.setenv("GAMESCOACH_COACH_NOTES_FILE", str(originals / "coach-notes.md"))
    monkeypatch.setenv("OPENAI_API_KEY", "test-openai-not-a-real-key")
    monkeypatch.setenv("GEMINI_API_KEY", "test-gemini-not-a-real-key")
    original_bytes = {path: path.read_bytes() for path in originals.iterdir()}
    # Unrelated files, including originals placed inside the data folder, survive.
    write(store.root / "personal.txt", "Keep me")
    write(store.root / "library" / "Original.pdf", "Keep this PDF too")
    library = Library(store.root / "library")
    assert library.brief()
    result = reset_memory("all", "RESET", store)
    assert result["deleted"] == {
        "sessions": 4, "guides": 1, "profile": True,
        "watched_folder": True, "prepared_cache": True,
    }
    assert result["status"] == {
        "sessions": 0, "chats": 0, "plans": 0, "documents": 0,
        "guides": 0, "unknown_sessions": 0, "has_profile": False,
        "watched_folder": False,
        "settings": {"auto_references": False, "coach_notes": False},
    }
    assert library.brief() == []  # This same instance previously prepared the guide.
    assert sync_folder() is None
    assert _coach_notes() == ""
    assert (store.root / "personal.txt").read_text() == "Keep me"
    assert (store.root / "library" / "Original.pdf").read_text() == "Keep this PDF too"
    assert {path: path.read_bytes() for path in originals.iterdir()} == original_bytes
    assert memory_settings(Store(store.root)) == {"auto_references": False, "coach_notes": False}
    assert os.environ["OPENAI_API_KEY"] == "test-openai-not-a-real-key"
    assert os.environ["GEMINI_API_KEY"] == "test-gemini-not-a-real-key"


def test_notes_can_be_enabled_again_explicitly_after_reset(store, tmp_path, monkeypatch):
    notes = tmp_path / "notes.md"
    notes.write_text("Prioritize easy aerobic CrossFit work.")
    monkeypatch.setenv("GAMESCOACH_COACH_NOTES_FILE", str(notes))
    reset_memory("all", "RESET", store)
    assert _coach_notes() == ""
    save_memory_settings({"auto_references": False, "coach_notes": True}, store)
    assert _coach_notes() == notes.read_text()


def test_notes_setting_uses_explicit_store_instead_of_default_environment(store, tmp_path, monkeypatch):
    notes = tmp_path / "notes.md"
    notes.write_text("Old coaching emphasis.")
    monkeypatch.setenv("GAMESCOACH_COACH_NOTES_FILE", str(notes))
    other_store = Store(tmp_path / "other-data")
    reset_memory("all", "RESET", other_store)
    assert _coach_notes(store=other_store) == ""
    assert _coach_notes() == notes.read_text()


def test_full_reset_can_replace_corrupt_memory_settings(store):
    seed(store)
    write(store.root / "memory.json", "not json")
    with pytest.raises(StoreError, match="memory settings"):
        memory_settings(store)
    reset_memory("all", "RESET", store)
    assert memory_settings(store) == {"auto_references": False, "coach_notes": False}


def test_history_reset_with_corrupt_settings_does_not_begin_deleting_data(store):
    seed(store)
    write(store.root / "memory.json", "not json")
    with pytest.raises(StoreError, match="memory settings"):
        reset_memory("history", "RESET", store)
    assert len(list((store.root / "sessions").glob("*.json"))) == 4


@pytest.mark.parametrize("relative,is_directory", [
    ("sessions", True), ("library", True), ("library/.cache", True),
    ("library/sources", True), ("profile.json", False),
    ("memory.json", False), ("sessions/linked.json", False),
    ("library/linked.json", False), ("library/.cache/prepared.json", False),
    ("library/sources/folder.json", False),
])
def test_symlinked_app_targets_are_rejected_before_any_deletion(store, tmp_path, relative, is_directory):
    write(store.root / "sessions" / "a.json", '{"kind":"chat"}')
    original = tmp_path / "outside"
    if is_directory:
        original.mkdir()
        write(original / "retained.json", "do not touch")
    else:
        original.write_text("do not touch")
    link = store.root / relative
    link.parent.mkdir(parents=True, exist_ok=True)
    if link.exists() and link.is_dir():
        # sessions exists only because of the independent retained record.
        (link / "a.json").unlink()
        link.rmdir()
        write(store.root / "profile.json", "retained profile")
    link.symlink_to(original, target_is_directory=is_directory)
    with pytest.raises(StoreError, match="unsafe linked"):
        reset_memory("all", "RESET", store)
    assert link.is_symlink()
    assert not (store.root / "memory.json").exists() or (store.root / "memory.json").is_symlink()
    if is_directory:
        assert (original / "retained.json").read_text() == "do not touch"
    else:
        assert original.read_text() == "do not touch"
    if relative != "sessions":
        assert (store.root / "sessions" / "a.json").exists()


def test_symlinked_root_or_ancestor_is_not_followed(tmp_path):
    actual = tmp_path / "actual"
    write(actual / "data" / "sessions" / "a.json", "private athlete data")
    linked = tmp_path / "linked"
    linked.symlink_to(actual, target_is_directory=True)
    for root in (linked, linked / "data"):
        with pytest.raises(StoreError, match="linked data folders"):
            reset_memory("all", "RESET", Store(root))
    assert (actual / "data" / "sessions" / "a.json").read_text() == "private athlete data"


def test_write_failure_preserves_previous_memory_settings_and_cleans_temp_file(store, monkeypatch):
    initial = {"auto_references": True, "coach_notes": False}
    save_memory_settings(initial, store)
    monkeypatch.setattr("gamescoach.memory.os.replace", lambda *_: (_ for _ in ()).throw(OSError("disk full")))
    with pytest.raises(StoreError, match="Cannot save memory settings"):
        save_memory_settings({"auto_references": False, "coach_notes": True}, store)
    assert memory_settings(store) == initial
    assert list(store.root.iterdir()) == [store.root / "memory.json"]


def test_partial_reset_failure_reports_changed_state_and_does_not_touch_sources(store, tmp_path, monkeypatch):
    seed(store)
    original = tmp_path / "source.pdf"
    original.write_text("Original PDF")
    unlink = Path.unlink

    def fail_on_second_session(path, *args, **kwargs):
        if path.name == "b" * 12 + ".json":
            raise OSError("permissions changed")
        return unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", fail_on_second_session)
    with pytest.raises(StoreError, match="only partially completed"):
        reset_memory("all", "RESET", store)
    assert memory_status(store)["sessions"] == 3
    assert memory_settings(store) == {"auto_references": False, "coach_notes": False}
    assert original.read_text() == "Original PDF"
