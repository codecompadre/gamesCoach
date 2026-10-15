"""Upgrade retained PDF text once without losing existing athlete knowledge."""

import json
from types import SimpleNamespace

from pypdf import PdfWriter
from pypdf.errors import PdfReadError
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject
import pytest

from gamescoach import knowledge
from gamescoach.knowledge import KnowledgeError, Library, PDF_EXTRACTION_VERSION
from gamescoach.library_sources import configure_folder, sync_folder


@pytest.fixture(autouse=True)
def local_data(monkeypatch, tmp_path):
    monkeypatch.setenv("GAMESCOACH_DATA_DIR", str(tmp_path / "data"))


def pdf(path, text="CrossFit methodology supports aerobic capacity and recovery."):
    writer = PdfWriter()
    page = writer.add_blank_page(width=612, height=792)
    font = DictionaryObject({NameObject("/Type"): NameObject("/Font"), NameObject("/Subtype"): NameObject("/Type1"), NameObject("/BaseFont"): NameObject("/Helvetica")})
    page[NameObject("/Resources")] = DictionaryObject({NameObject("/Font"): DictionaryObject({NameObject("/F1"): font})})
    stream = DecodedStreamObject()
    stream.set_data(f"BT /F1 12 Tf 50 700 Td ({text}) Tj ET".encode("ascii"))
    page[NameObject("/Contents")] = writer._add_object(stream)
    writer.write(path)
    return path


def legacy_index(library, identifier):
    path = library.root / f"{identifier}.json"
    data = json.loads(path.read_text())
    data.pop("extraction_version", None)
    data["passages"] = [{"page": 1, "text": "Old incomplete CrossFit text."}]
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def test_unchanged_registered_pdf_refreshes_legacy_text_then_reuses_it(tmp_path, monkeypatch):
    folder = tmp_path / "CrossFit"
    folder.mkdir()
    source = pdf(folder / "Guide.pdf")
    configure_folder(folder)
    library = Library()
    identifier = library.list_guides()[0]["id"]
    index = legacy_index(library, identifier)
    config = library.root / "sources" / "folder.json"
    config_bytes, modified = config.read_bytes(), config.stat().st_mtime_ns
    assert "Old incomplete" in library.brief()[0]["text"]
    assert (library.root / ".cache" / "prepared.json").exists()

    assert sync_folder() == {"folder": str(folder), "imported": 1, "unchanged": 0, "guides": 1}
    refreshed = json.loads(index.read_text())
    assert refreshed["id"] == identifier
    assert refreshed["extraction_version"] == PDF_EXTRACTION_VERSION
    assert "aerobic capacity" in refreshed["passages"][0]["text"]
    assert "Old incomplete" not in json.dumps(library.brief())
    assert config.read_bytes() == config_bytes and config.stat().st_mtime_ns == modified
    assert len(list(library.root.glob("*.json"))) == 1

    def unexpected_parse(*args, **kwargs):
        pytest.fail("Current PDF extraction must be reused")

    monkeypatch.setattr(knowledge, "PdfReader", unexpected_parse)
    assert sync_folder()["unchanged"] == 1
    assert library.add(source) == refreshed


def test_manual_legacy_refresh_preserves_id_at_six_guide_capacity(tmp_path):
    library = Library()
    sources = [pdf(tmp_path / f"{number}.pdf", f"CrossFit methodology guide {number}.") for number in range(6)]
    guides = [library.add(source) for source in sources]
    identifier = guides[0]["id"]
    legacy_index(library, identifier)
    unchanged = {guide["id"]: (library.root / f'{guide["id"]}.json').read_bytes() for guide in guides[1:]}

    refreshed = library.add(sources[0])
    assert refreshed["id"] == identifier
    assert refreshed["extraction_version"] == PDF_EXTRACTION_VERSION
    assert len(library.list_guides()) == 6
    for identifier, data in unchanged.items():
        assert (library.root / f"{identifier}.json").read_bytes() == data


def test_failed_refresh_preserves_old_index_registration_and_prepared_knowledge(tmp_path, monkeypatch):
    folder = tmp_path / "CrossFit"
    folder.mkdir()
    pdf(folder / "Guide.pdf")
    configure_folder(folder)
    library = Library()
    index = legacy_index(library, library.list_guides()[0]["id"])
    old_brief = library.brief()
    config = library.root / "sources" / "folder.json"
    cache = library.root / ".cache" / "prepared.json"
    original = {path: path.read_bytes() for path in (index, config, cache)}

    def fail_text():
        raise PdfReadError("simulated extraction failure")

    # A later page can fail after earlier pages have already been extracted.
    reader = SimpleNamespace(is_encrypted=False, pages=[SimpleNamespace(extract_text=lambda: "Replacement CrossFit text."), SimpleNamespace(extract_text=fail_text)])
    monkeypatch.setattr(knowledge, "PdfReader", lambda *args, **kwargs: reader)
    with pytest.raises(KnowledgeError, match="Cannot read"):
        sync_folder()
    assert all(path.read_bytes() == data for path, data in original.items())
    assert library.brief() == old_brief


def test_removed_legacy_guide_is_not_reimported_without_a_source_change(tmp_path, monkeypatch):
    folder = tmp_path / "CrossFit"
    folder.mkdir()
    pdf(folder / "Guide.pdf")
    configure_folder(folder)
    library = Library()
    identifier = library.list_guides()[0]["id"]
    legacy_index(library, identifier)
    library.remove(identifier)

    def unexpected_parse(*args, **kwargs):
        pytest.fail("A removed unchanged source must stay removed")

    monkeypatch.setattr(knowledge, "PdfReader", unexpected_parse)
    assert sync_folder() == {"folder": str(folder), "imported": 0, "unchanged": 1, "guides": 0}
    assert library.list_guides() == []


@pytest.mark.parametrize("version", [0, -1, True, "1", None, {}])
def test_invalid_or_older_extraction_metadata_is_refreshed(tmp_path, version):
    source = pdf(tmp_path / "Guide.pdf")
    library = Library()
    guide = library.add(source)
    index = legacy_index(library, guide["id"])
    data = json.loads(index.read_text())
    data["extraction_version"] = version
    index.write_text(json.dumps(data))

    refreshed = library.add(source)
    assert refreshed["extraction_version"] == PDF_EXTRACTION_VERSION
    assert "aerobic capacity" in refreshed["passages"][0]["text"]


def test_a_newer_extraction_version_is_retained_without_downgrading(tmp_path, monkeypatch):
    source = pdf(tmp_path / "Guide.pdf")
    library = Library()
    guide = library.add(source)
    index = library.root / f'{guide["id"]}.json'
    guide["extraction_version"] = PDF_EXTRACTION_VERSION + 1
    index.write_text(json.dumps(guide))
    original = index.read_bytes()

    def unexpected_parse(*args, **kwargs):
        pytest.fail("A newer extraction version must not be downgraded")

    monkeypatch.setattr(knowledge, "PdfReader", unexpected_parse)
    assert library.add(source) == guide
    assert index.read_bytes() == original
