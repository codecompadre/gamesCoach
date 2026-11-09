"""Document lifecycle and confirmed factory reset regressions."""
from pathlib import Path

import pytest
from docx import Document

from gamescoach.documents import import_document
from gamescoach.knowledge import Library
from gamescoach.library_sources import configure_folder, sync_folder, folder_status
from gamescoach.memory import reset_memory, save_memory_settings
from gamescoach.models import AthleteProfile
from gamescoach.storage import Store, StoreError
from gamescoach.web import WebService, WebError


def test_mixed_folder_refresh_removal_and_unsupported_feedback(tmp_path):
    source = tmp_path / "CrossFit"
    source.mkdir()
    (source / "engine.txt").write_text("CrossFit aerobic intervals and recovery.")
    (source / "skills.md").write_text("CrossFit gymnastics mechanics and progression.")
    document = Document()
    document.add_paragraph("CrossFit strength training and fueling.")
    document.save(source / "strength.docx")
    (source / "legacy.doc").write_bytes(b"unsupported")
    library = Library(tmp_path / "app" / "library")
    result = configure_folder(source, library=library)
    assert result["imported"] == 3
    assert "legacy.doc" in result["warnings"][0]
    assert sync_folder(library=library)["unchanged"] == 3
    identifier = next(g["id"] for g in library.list_guides() if g["filename"] == "engine.txt")
    library.remove(identifier)
    assert sync_folder(library=library)["imported"] == 0
    (source / "engine.txt").write_text("CrossFit aerobic intervals revised with longer recovery.")
    assert sync_folder(library=library)["imported"] == 1
    assert len(library.list_guides()) == 3
    assert (source / "engine.txt").exists()


def test_delete_import_and_factory_preserve_sources_and_keys(tmp_path, monkeypatch):
    store = Store(tmp_path / "app")
    source = tmp_path / "original.txt"
    source.write_text("CrossFit training week.")
    monkeypatch.setenv("GAMESCOACH_GEMINI_API_KEY", "test-preserved")
    entry = import_document(source, AthleteProfile())
    store.save_session(entry)
    service = WebService(store, demo=True)
    service.mutate("/api/entries/delete", {"id": entry.id})
    assert source.read_text() == "CrossFit training week."
    assert not store.list_sessions()
    with pytest.raises(WebError):
        service.mutate("/api/entries/delete", {"id": "../../original"})
    store.save_session(entry)
    store.save_profile(AthleteProfile())
    configure_folder(tmp_path / "app" / "sessions", library=service.library)
    save_memory_settings({"auto_references": False, "coach_notes": False}, store)
    with pytest.raises(StoreError, match="RESET"):
        reset_memory("factory", "no", store)
    assert store.get_session(entry.id)
    result = reset_memory("factory", "RESET", store)
    assert not store.list_sessions() and store.load_profile() is None
    assert folder_status(library=service.library) is None
    assert not (store.root / "memory.json").exists()
    assert source.exists()
    import os
    assert os.environ["GAMESCOACH_GEMINI_API_KEY"] == "test-preserved"


def test_browser_automatically_uses_same_conversation_after_restart(tmp_path):
    store = Store(tmp_path / "app")
    service = WebService(store, demo=True)
    first = service.chat({"message": "CrossFit aerobic progression"})["session"]
    restarted = WebService(store, demo=True)
    assert restarted.state()["active_chat"] == first["id"]
    second = restarted.chat({"message": "CrossFit retest next week"})["session"]
    assert second["id"] == first["id"]
    assert len(second["messages"]) == 4
    assert len(store.list_chats(demo=True)) == 1


def test_deleting_attached_document_cleans_resumed_context(tmp_path):
    store = Store(tmp_path / "app")
    service = WebService(store, demo=True)
    first = service.chat({"message": "CrossFit aerobic development"})["session"]
    source = tmp_path / "week.md"
    source.write_text("CrossFit intervals with easy recovery.")
    imported = service.import_file({"path": str(source), "chat_id": first["id"]})
    identifier = imported["entry"]["id"]
    assert identifier in store.get_session(first["id"]).context_ids
    service.mutate("/api/entries/delete", {"id": identifier})
    resumed = store.get_session(first["id"])
    assert identifier not in resumed.context_ids
    service.chat({"message": "CrossFit recovery pacing", "id": resumed.id, "reference_ids": resumed.context_ids})
    assert source.exists()
