import json
from pathlib import Path
import stat

from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject
import pytest

from gamescoach import library_sources
from gamescoach.knowledge import KnowledgeError, Library
from gamescoach.library_sources import clear_folder, configure_folder, folder_status, sync_folder


@pytest.fixture(autouse=True)
def local_data(monkeypatch, tmp_path):
    monkeypatch.setenv("GAMESCOACH_DATA_DIR", str(tmp_path / "data"))


def pdf(path, text="CrossFit mechanics consistency and intensity."):
    writer = PdfWriter()
    page = writer.add_blank_page(width=612, height=792)
    font = DictionaryObject({NameObject("/Type"): NameObject("/Font"), NameObject("/Subtype"): NameObject("/Type1"), NameObject("/BaseFont"): NameObject("/Helvetica")})
    page[NameObject("/Resources")] = DictionaryObject({NameObject("/Font"): DictionaryObject({NameObject("/F1"): font})})
    stream = DecodedStreamObject()
    stream.set_data(f"BT /F1 12 Tf 50 700 Td ({text}) Tj ET".encode("ascii"))
    page[NameObject("/Contents")] = writer._add_object(stream)
    writer.write(path)
    return path


def folder(tmp_path):
    path = tmp_path / "CrossFit"
    path.mkdir()
    return path


def test_no_registration_is_noop_and_clear_preserves_indexes(tmp_path):
    assert folder_status() is None and sync_folder() is None
    clear_folder()
    source = folder(tmp_path)
    pdf(source / "Guide.pdf")
    configure_folder(source)
    clear_folder()
    assert folder_status() is None and sync_folder() is None
    assert len(Library().list_guides()) == 1


def test_register_imports_only_immediate_pdfs_and_saves_private_local_path(tmp_path):
    source = folder(tmp_path)
    pdf(source / "Methodology.pdf")
    pdf(source / "Competitors.PDF", "CrossFit competitors need aerobic conditioning and recovery.")
    (source / "ignore.txt").write_text("not a PDF")
    nested = source / "nested"
    nested.mkdir()
    pdf(nested / "Other.pdf")
    result = configure_folder(f'"{source}"')
    assert result == {"folder": str(source), "imported": 2, "unchanged": 0, "guides": 2}
    assert folder_status() == {"folder": str(source), "files": 2}
    stored = Library().root / "sources" / "folder.json"
    assert stat.S_IMODE(stored.stat().st_mode) == 0o600
    assert stat.S_IMODE(stored.parent.stat().st_mode) == 0o700
    assert stat.S_IMODE(Library().root.stat().st_mode) == 0o700
    assert str(source) not in json.dumps(Library().list_guides())


def test_windows_folder_uses_existing_wsl_normalizer(tmp_path, monkeypatch):
    source = folder(tmp_path)
    pdf(source / "Guide.pdf")
    seen = []
    normalizer = library_sources.document_path

    def local_windows_mount(value):
        normalized = normalizer(value)
        seen.append(normalized)
        return source

    monkeypatch.setattr(library_sources, "document_path", local_windows_mount)
    result = configure_folder(r'"C:\Users\YourName\Desktop\CrossFit"')
    assert seen == [Path("/mnt/c/Users/YourName/Desktop/CrossFit")]
    assert result["guides"] == 1


def test_restart_sync_does_not_reopen_unchanged_pdfs_or_rewrite_config(tmp_path, monkeypatch):
    source = folder(tmp_path)
    pdf(source / "Guide.pdf")
    configure_folder(source)
    stored = Library().root / "sources" / "folder.json"
    modified = stored.stat().st_mtime_ns

    def unexpected_add(*args, **kwargs):
        pytest.fail("unchanged PDFs must not be opened or reparsed")

    monkeypatch.setattr(Library, "add", unexpected_add)
    assert sync_folder()["unchanged"] == 1
    assert stored.stat().st_mtime_ns == modified


def test_new_and_changed_pdfs_are_retained_and_previous_edition_replaced(tmp_path):
    source = folder(tmp_path)
    path = pdf(source / "Guide.pdf")
    configure_folder(source)
    before = Library().list_guides()[0]["id"]
    pdf(path, "CrossFit methodology now includes aerobic conditioning and progressive recovery.")
    pdf(source / "Competitors.pdf", "Competitors prioritize weaknesses and specific practice.")
    result = sync_folder()
    assert result["imported"] == 2 and result["guides"] == 2
    assert before not in {guide["id"] for guide in Library().list_guides()}
    assert sync_folder()["unchanged"] == 2


def test_removed_guide_stays_removed_until_file_changes_or_explicit_registration(tmp_path):
    source = folder(tmp_path)
    path = pdf(source / "Guide.pdf")
    configure_folder(source)
    Library().remove(Library().list_guides()[0]["id"])
    assert sync_folder()["guides"] == 0
    assert configure_folder(source)["guides"] == 1
    Library().remove(Library().list_guides()[0]["id"])
    pdf(path, "A changed CrossFit methodology page with aerobic progressions.")
    assert sync_folder()["guides"] == 1


def test_missing_folder_or_file_never_drops_previously_imported_knowledge(tmp_path):
    source = folder(tmp_path)
    path = pdf(source / "Guide.pdf")
    configure_folder(source)
    path.unlink()
    assert sync_folder()["guides"] == 1
    source.rmdir()
    with pytest.raises(KnowledgeError, match="retained"):
        sync_folder()
    assert len(Library().list_guides()) == 1


def test_invalid_changed_pdf_preserves_old_index_and_registration(tmp_path):
    source = folder(tmp_path)
    path = pdf(source / "Guide.pdf")
    configure_folder(source)
    previous = Library().list_guides()
    stored = Library().root / "sources" / "folder.json"
    config = stored.read_bytes()
    path.write_bytes(b"broken PDF")
    with pytest.raises(KnowledgeError, match="Cannot read"):
        sync_folder()
    assert Library().list_guides() == previous
    assert stored.read_bytes() == config


def test_failed_later_pdf_rolls_back_new_indexes_without_removing_previous_editions(tmp_path):
    source = folder(tmp_path)
    first = pdf(source / "A.pdf", "CrossFit methodology foundation.")
    second = pdf(source / "B.pdf", "CrossFit competition principles.")
    configure_folder(source)
    previous = Library().list_guides()
    stored = Library().root / "sources" / "folder.json"
    config = stored.read_bytes()
    pdf(first, "Updated CrossFit methodology with aerobic conditioning and recovery.")
    second.write_bytes(b"invalid replacement PDF")
    with pytest.raises(KnowledgeError, match="Cannot read"):
        sync_folder()
    assert Library().list_guides() == previous
    assert stored.read_bytes() == config


def test_too_many_folder_pdfs_rejected_without_importing_any(tmp_path):
    source = folder(tmp_path)
    for number in range(7):
        pdf(source / f"{number}.pdf", f"CrossFit methodology guide {number}.")
    with pytest.raises(KnowledgeError, match="six"):
        configure_folder(source)
    assert Library().list_guides() == [] and folder_status() is None


def test_six_guide_capacity_preserves_old_edition_on_replacement(tmp_path):
    source = folder(tmp_path)
    for number in range(6):
        pdf(source / f"{number}.pdf", f"CrossFit methodology guide {number}.")
    configure_folder(source)
    previous = Library().list_guides()
    pdf(source / "0.pdf", "Changed CrossFit methodology source with competition principles.")
    with pytest.raises(KnowledgeError, match="six"):
        sync_folder()
    assert Library().list_guides() == previous


@pytest.mark.parametrize("invalid", ["{bad", '{"version": 1, "folder": "/tmp", "files": []}', '{"version": 1, "folder": "/tmp", "files": {"../unsafe.pdf": {}}}'])
def test_corrupt_configuration_can_be_cleared_without_touching_guides(tmp_path, invalid):
    source = folder(tmp_path)
    pdf(source / "Guide.pdf")
    configure_folder(source)
    stored = Library().root / "sources" / "folder.json"
    stored.write_text(invalid)
    with pytest.raises(KnowledgeError, match="library unwatch"):
        sync_folder()
    clear_folder()
    assert folder_status() is None and len(Library().list_guides()) == 1
