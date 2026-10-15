"""Check automatic knowledge preparation and inspection through the real CLI."""

import json
import os
from pathlib import Path
import subprocess
import sys

from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject
import pytest


GUIDE_ID = "0123456789abcdef"
GUIDE_TEXT = "CrossFit methodology applies mechanics, consistency, and intensity to functional movement."


@pytest.fixture
def cli_env(tmp_path):
    env = os.environ.copy()
    for name in (
        "OPENAI_API_KEY", "GAMESCOACH_OPENAI_API_KEY", "GEMINI_API_KEY",
        "GOOGLE_API_KEY", "GAMESCOACH_GEMINI_API_KEY", "GAMESCOACH_COACH_NOTES_FILE",
    ):
        env.pop(name, None)
    env["GAMESCOACH_DATA_DIR"] = str(tmp_path / "data")
    source = str(Path(__file__).resolve().parents[1] / "src")
    env["PYTHONPATH"] = source + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    return env


def run_cli(env, *args, input_text=None):
    return subprocess.run(
        [sys.executable, "-m", "gamescoach", *args], env=env, input=input_text,
        capture_output=True, text=True, timeout=15,
    )


def saved_guide(env, *, broken=False):
    library = Path(env["GAMESCOACH_DATA_DIR"]) / "library"
    library.mkdir(parents=True)
    destination = library / f"{GUIDE_ID}.json"
    destination.write_text("{broken" if broken else json.dumps({
        "id": GUIDE_ID,
        "filename": "Methodology.pdf",
        "added_at": "2026-10-15T00:00:00+00:00",
        "pages": 1,
        "pages_without_text": 0,
        "passages": [{"page": 1, "text": GUIDE_TEXT}],
    }), encoding="utf-8")
    return destination


def write_pdf(path, text="Competition programming uses aerobic capacity and recovery."):
    writer = PdfWriter()
    page = writer.add_blank_page(width=612, height=792)
    font = DictionaryObject({NameObject("/Type"): NameObject("/Font"), NameObject("/Subtype"): NameObject("/Type1"), NameObject("/BaseFont"): NameObject("/Helvetica")})
    page[NameObject("/Resources")] = DictionaryObject({NameObject("/Font"): DictionaryObject({NameObject("/F1"): font})})
    stream = DecodedStreamObject()
    stream.set_data(f"BT /F1 12 Tf 50 700 Td ({text}) Tj ET".encode("ascii"))
    page[NameObject("/Contents")] = writer._add_object(stream)
    writer.write(path)
    return path


@pytest.mark.parametrize("command,input_text", [
    ([], "q\n"),
    (["chat"], "/quit\n"),
    (["plan", "--demo", "--request", "CrossFit aerobic work"], None),
])
def test_menu_chat_and_plan_prepare_retained_guides_at_startup(cli_env, command, input_text):
    saved_guide(cli_env)
    result = run_cli(cli_env, *command, input_text=input_text)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "CrossFit knowledge ready: 1 guide" in result.stdout
    assert "1 PDF page" in result.stdout and "1 foundation passage" in result.stdout
    assert "automatically during live coaching" in result.stdout
    if not command:
        assert result.stdout.index("CrossFit knowledge ready") < result.stdout.index("Choose:")


def test_sources_are_available_before_any_live_chat_message(cli_env):
    saved_guide(cli_env)
    result = run_cli(cli_env, "chat", input_text="/sources\n/quit\n")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Automatic methodology brief" in result.stdout
    assert "Methodology.pdf" in result.stdout and "PDF p. 1" in result.stdout
    assert "mechanics, consistency, and intensity" in result.stdout
    assert "Saved chat:" not in result.stdout
    assert "Passages selected for the latest question" not in result.stdout


def test_learning_a_pdf_updates_sources_in_the_active_chat(cli_env, tmp_path):
    source = write_pdf(tmp_path / "Competitors.pdf")
    result = run_cli(cli_env, "chat", input_text=f'/learn "{source}"\n/sources\n/quit\n')
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Library ready: Competitors.pdf" in result.stdout
    assert "CrossFit knowledge ready: 1 guide" in result.stdout
    sources = result.stdout.split("Automatic methodology brief", 1)[1]
    assert "Competitors.pdf" in sources and "aerobic capacity and recovery" in sources
    assert "Saved chat:" not in result.stdout


def test_corrupt_library_warns_at_menu_but_allows_quit_and_terminal_removal(cli_env):
    broken = saved_guide(cli_env, broken=True)
    result = run_cli(cli_env, input_text="q\n")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Methodology knowledge could not be prepared" in result.stdout
    assert "library remove ID" in result.stdout and "Choose:" in result.stdout
    removed = run_cli(cli_env, "library", "remove", GUIDE_ID)
    assert removed.returncode == 0, removed.stdout + removed.stderr
    assert not broken.exists()


@pytest.mark.parametrize("command", [["--version"], ["history"], ["doctor"]])
def test_noncoaching_commands_do_not_prepare_or_fail_on_corrupt_library(cli_env, command):
    saved_guide(cli_env, broken=True)
    result = run_cli(cli_env, *command)
    assert result.returncode == (1 if command == ["doctor"] else 0)
    assert "Methodology knowledge" not in result.stdout
    assert "Cannot read the coaching library" not in result.stdout
    assert "CrossFit knowledge ready" not in result.stdout


def test_watched_folder_picks_up_a_new_pdf_on_next_start_and_unwatch_keeps_knowledge(cli_env, tmp_path):
    source = tmp_path / "CrossFit"
    source.mkdir()
    write_pdf(source / "Methodology.pdf", GUIDE_TEXT)
    registered = run_cli(cli_env, "library", "watch", str(source))
    assert registered.returncode == 0, registered.stdout + registered.stderr
    assert "Imported 1 PDF" in registered.stdout
    write_pdf(source / "Competitors.pdf")
    restarted = run_cli(cli_env, input_text="q\n")
    assert restarted.returncode == 0, restarted.stdout + restarted.stderr
    assert "PDF folder updated: 1 new or changed PDF imported" in restarted.stdout
    assert "CrossFit knowledge ready: 2 guides" in restarted.stdout
    source.rename(tmp_path / "temporarily_unavailable")
    unavailable = run_cli(cli_env, input_text="q\n")
    assert unavailable.returncode == 0, unavailable.stdout + unavailable.stderr
    assert "PDF folder update skipped" in unavailable.stdout
    assert "CrossFit knowledge ready: 2 guides" in unavailable.stdout
    assert run_cli(cli_env, "library", "unwatch").returncode == 0
    listed = run_cli(cli_env, "library", "list")
    assert "Methodology.pdf" in listed.stdout and "Competitors.pdf" in listed.stdout
    assert "PDF source folder" not in listed.stdout


@pytest.mark.parametrize("command,input_template", [
    ([], "10\n{folder}\nq\n"),
    (["chat"], "/learn {folder}\n/sources\n/quit\n"),
])
def test_menu_and_chat_accept_pdf_folders(cli_env, tmp_path, command, input_template):
    source = tmp_path / "CrossFit"
    source.mkdir()
    write_pdf(source / "Competitors.pdf")
    result = run_cli(cli_env, *command, input_text=input_template.format(folder=source))
    assert result.returncode == 0, result.stdout + result.stderr
    assert "PDF source folder" in result.stdout
    assert "CrossFit knowledge ready: 1 guide" in result.stdout
    if command:
        assert "Automatic methodology brief" in result.stdout
        assert "aerobic capacity and recovery" in result.stdout
