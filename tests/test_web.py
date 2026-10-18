"""The browser is a loopback view of the same private coaching records."""

import base64
import io
from http.client import HTTPConnection
import json
from pathlib import Path
import threading

import pytest

from gamescoach import web
from gamescoach import chat as chat_module
from gamescoach.chat import ChatCoach
from gamescoach.engine import create_session
from gamescoach.models import AthleteProfile
from gamescoach.providers import ProviderError
from gamescoach.storage import Store, StoreError


@pytest.fixture
def browser(tmp_path, monkeypatch):
    monkeypatch.setenv("GAMESCOACH_DATA_DIR", str(tmp_path / "unused-default"))
    for key in (
        "OPENAI_API_KEY", "GAMESCOACH_OPENAI_API_KEY", "GEMINI_API_KEY",
        "GOOGLE_API_KEY", "GAMESCOACH_GEMINI_API_KEY", "GAMESCOACH_COACH_NOTES_FILE",
        "OPENAI_MODEL", "GEMINI_MODEL",
    ):
        monkeypatch.delenv(key, raising=False)
    static = tmp_path / "static"
    static.mkdir()
    (static / "index.html").write_text('<meta name="gamescoach-token" content="{{CSRF_TOKEN}}">')
    (static / "app.js").write_text("// local app")
    (static / "styles.css").write_text("body { color: green }")
    monkeypatch.setattr(web, "_STATIC", static)
    server = web.create_server(Store(tmp_path / "private-data"), demo=True)
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def request(server, method="GET", path="/api/state", payload=None, *, headers=None, body=None):
    connection = HTTPConnection("127.0.0.1", server.server_address[1], timeout=5)
    sent = {"X-GamesCoach-Token": server.csrf_token}
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
        sent["Content-Type"] = "application/json"
    if headers:
        for key, value in headers.items():
            if value is None:
                sent.pop(key, None)
            else:
                sent[key] = value
    try:
        connection.request(method, path, body=body, headers=sent)
        response = connection.getresponse()
        raw = response.read()
        data = json.loads(raw) if response.headers.get_content_type() == "application/json" else raw.decode()
        return response.status, data, dict(response.getheaders())
    finally:
        connection.close()


def post(server, path, payload):
    return request(server, "POST", path, payload)


def plan(server, kind="training"):
    entry = create_session(kind, "CrossFit aerobic progression", AthleteProfile(), demo=True)
    server.service.store.save_session(entry)
    return entry


def test_local_state_shows_presence_only_and_does_not_prepare_paid_coaching(browser, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "secret-value-must-stay-private")
    monkeypatch.setenv("GEMINI_API_KEY", "another-secret-value")
    monkeypatch.setenv("GAMESCOACH_COACH_NOTES_FILE", "/private/notes.md")
    status, state, headers = request(browser)
    assert status == 200
    assert browser.server_address[0] == "127.0.0.1"
    assert state["config"]["keys"] == {"ChatGPT": True, "Gemini": True}
    assert state["config"]["models"] == {"ChatGPT": "gpt-5-mini", "Gemini": "gemini-3.1-pro-preview"}
    assert state["demo"] is True and state["sessions"] == [] and state["profile"] is None
    assert "secret-value" not in json.dumps(state)
    assert "/private/notes.md" not in json.dumps(state)
    assert headers["Cache-Control"] == "no-store"
    assert headers["X-Content-Type-Options"] == "nosniff"
    assert "frame-ancestors 'none'" in headers["Content-Security-Policy"]
    assert "Access-Control-Allow-Origin" not in headers
    assert not (Path(__import__("os").environ["GAMESCOACH_DATA_DIR"])).exists()


def test_index_supplies_token_but_all_private_get_and_post_apis_require_it(browser):
    status, page, _ = request(browser, path="/", headers={"X-GamesCoach-Token": None})
    assert status == 200 and browser.csrf_token in page and "{{CSRF_TOKEN}}" not in page
    for method, path, payload in (("GET", "/api/state", None), ("GET", "/api/session/abcdef123456", None), ("POST", "/api/memory/reset", {"scope": "all", "confirmation": "RESET"})):
        assert request(browser, method, path, payload, headers={"X-GamesCoach-Token": None})[0] == 403
        assert request(browser, method, path, payload, headers={"X-GamesCoach-Token": "incorrect"})[0] == 403


@pytest.mark.parametrize("host", ["attacker.example", "127.0.0.1", "localhost", "127.0.0.1:1", "localhost.evil:8765", "0.0.0.0:8765"])
def test_host_validation_blocks_rebinding_and_unexpected_hosts(browser, host):
    assert request(browser, path="/", headers={"Host": host})[0] == 403
    assert request(browser, headers={"Host": host})[0] == 403


def test_post_origin_must_match_exact_local_origin_and_preflights_are_denied(browser):
    port = browser.server_address[1]
    for origin in ("https://example.com", "null", f"http://localhost:{port}", f"http://127.0.0.1:{port + 1}"):
        assert request(browser, "POST", "/api/profile", {"profile": {}}, headers={"Origin": origin})[0] == 403
    assert request(browser, "POST", "/api/profile", {"profile": {}}, headers={"Origin": f"http://127.0.0.1:{port}"})[0] == 200
    assert request(browser, "OPTIONS")[0] == 403
    assert request(browser, headers={"Host": f"localhost:{port}", "Origin": f"http://localhost:{port}"})[0] == 200


@pytest.mark.parametrize("path", ["/../web.py", "/%2e%2e/web.py", "/static/../storage.py", "/profile.json", "/.env", "/app.js?token=oops", "/styles.css/extra", "http://evil.example/api/state"])
def test_static_allowlist_never_reads_arbitrary_paths(browser, path):
    assert request(browser, path=path)[0] == 404


def test_body_validation_precedes_actions_and_bounded_uploads(browser, monkeypatch):
    assert request(browser, "POST", "/api/profile", body=b"[]", headers={"Content-Type": "application/json"})[0] == 400
    assert request(browser, "POST", "/api/profile", body=b'{"profile":NaN}', headers={"Content-Type": "application/json"})[0] == 400
    assert request(browser, "POST", "/api/profile", body=b"[" * 2000 + b"]" * 2000, headers={"Content-Type": "application/json"})[0] == 400
    assert request(browser, "POST", "/api/profile", body=b"{}", headers={"Content-Type": "text/plain"})[0] == 415
    assert request(browser, "POST", "/api/profile", body=b"", headers={"Content-Type": "application/json", "Content-Length": str(web._MAX_JSON_BYTES + 1)})[0] == 413
    assert request(browser, "POST", "/api/upload", body=b"", headers={"Content-Type": "application/json", "Content-Length": str(web._MAX_BODY_BYTES + 1)})[0] == 413
    assert request(browser, "POST", "/api/profile", body=b"{}", headers={"Content-Type": "application/json", "Transfer-Encoding": "chunked"})[0] == 400
    assert browser.service.store.load_profile() is None


def test_multiline_chat_resumes_saved_thread_without_implicitly_importing_old_history(browser):
    older = plan(browser)
    message = "My CrossFit week:\nMonday: easy bike\nWednesday: strength and intervals"
    status, first, _ = post(browser, "/api/chat", {"message": message})
    assert status == 200, first
    saved = first["session"]
    assert saved["request"] == message and saved["messages"][0]["content"] == message
    assert saved["context_ids"] == [] and first["context"]["reference_ids"] == []
    assert len(saved["turns"]) == 3 and first["plan"] is None and first["saved"] is True
    status, second, _ = post(browser, "/api/chat", {"id": saved["id"], "message": "How do I progress the bike work?"})
    assert status == 200 and second["session"]["id"] == saved["id"]
    assert len(second["session"]["messages"]) == 4 and len(second["session"]["turns"]) == 6
    assert len(browser.service.store.list_sessions()) == 2
    status, reopened, _ = request(browser, path=f'/api/session/{saved["id"]}')
    assert status == 200 and reopened["session"] == second["session"]
    assert older.id != saved["id"]


@pytest.mark.parametrize("kind", ["workout", "training", "nutrition", "competition"])
def test_structured_chat_saves_one_plan_and_one_chat_from_same_three_turns(browser, kind):
    status, result, _ = post(browser, "/api/chat", {"message": f"/{kind} CrossFit aerobic focus"})
    assert status == 200, result
    saved_plan, chat = result["plan"], result["session"]
    assert saved_plan["kind"] == kind and chat["kind"] == "chat"
    assert saved_plan["id"] != chat["id"]
    assert saved_plan["final_plan"] == chat["messages"][-1]["content"]
    assert len(saved_plan["turns"]) == len(chat["turns"]) == 3
    assert saved_plan["request"] == "CrossFit aerobic focus"
    assert len(browser.service.store.list_sessions()) == 2


def test_plan_selector_and_bare_slash_commands_and_help_are_local(browser, monkeypatch):
    assert post(browser, "/api/chat", {"message": "CrossFit training", "plan_kind": "workout"})[1]["plan"]["kind"] == "workout"
    assert post(browser, "/api/chat", {"message": "/nutrition"})[1]["plan"]["request"] == web._DEFAULT_REQUESTS["nutrition"]
    calls = []
    monkeypatch.setattr(ChatCoach, "reply", lambda *args, **kwargs: calls.append(args))
    for payload in ({"message": "/workout --help"}, {"message": "/quit"}, {"message": "/file C:\\private.txt"}, {"message": "/workout", "plan_kind": "training"}, {"message": "hi", "plan_kind": []}):
        assert post(browser, "/api/chat", payload)[0] == 400
    assert calls == []


def test_explicit_references_persist_and_can_be_removed_without_paid_reply(browser):
    older = plan(browser)
    _, first, _ = post(browser, "/api/chat", {"message": "CrossFit pacing", "reference_ids": [older.id]})
    identifier = first["session"]["id"]
    assert first["session"]["context_ids"] == [older.id]
    _, resumed, _ = post(browser, "/api/chat", {"id": identifier, "message": "What about recovery?"})
    assert resumed["session"]["context_ids"] == [older.id]
    status, cleared, _ = post(browser, "/api/references", {"id": identifier, "reference_ids": []})
    assert status == 200 and cleared["session"]["context_ids"] == []
    assert len(cleared["session"]["messages"]) == 4
    assert post(browser, "/api/references", {"id": identifier, "reference_ids": [identifier]})[0] == 400
    assert post(browser, "/api/references", {"id": identifier, "reference_ids": [older.id, older.id]})[0] == 400


def test_missing_resume_reference_is_reported_and_removed_but_explicit_missing_is_error(browser):
    older = plan(browser)
    _, first, _ = post(browser, "/api/chat", {"message": "CrossFit pacing", "reference_ids": [older.id]})
    (browser.service.store.root / "sessions" / f"{older.id}.json").unlink()
    status, resumed, _ = post(browser, "/api/chat", {"id": first["session"]["id"], "message": "Recovery?"})
    assert status == 200 and resumed["session"]["context_ids"] == []
    assert "unavailable" in resumed["warnings"][0]
    assert post(browser, "/api/chat", {"message": "CrossFit", "reference_ids": [older.id]})[0] == 400


def test_failing_provider_leaves_previous_saved_exchange_unchanged_without_retry(browser, monkeypatch):
    _, first, _ = post(browser, "/api/chat", {"message": "CrossFit pacing"})
    before = browser.service.store.get_session(first["session"]["id"]).to_dict()
    calls = []

    def fail(*args, **kwargs):
        calls.append(1)
        raise ProviderError("Gemini could not find the model. Check the configured model name and access.")

    monkeypatch.setattr(ChatCoach, "reply", fail)
    status, failed, _ = post(browser, "/api/chat", {"id": before["id"], "message": "CrossFit follow-up"})
    assert status == 400 and "configured model" in failed["error"]
    assert calls == [1]
    assert browser.service.store.get_session(before["id"]).to_dict() == before


def test_partial_plan_save_failure_still_saves_chat_and_reports_warning(browser, monkeypatch):
    def fail(session):
        raise StoreError("Cannot save plan. Check local data directory permissions.")

    monkeypatch.setattr(browser.service.store, "save_session", fail)
    status, result, _ = post(browser, "/api/chat", {"message": "/workout"})
    assert status == 200 and result["plan"] is None and result["saved"] is True
    assert "separate plan was not saved" in " ".join(result["warnings"])
    assert len(browser.service.store.list_sessions()) == 1


def test_partial_chat_save_failure_returns_reply_and_standalone_plan(browser, monkeypatch):
    def fail(session):
        raise StoreError("Cannot save conversation. Check local data directory permissions.")

    monkeypatch.setattr(browser.service.store, "save_chat", fail)
    status, result, _ = post(browser, "/api/chat", {"message": "/training"})
    assert status == 200 and result["plan"] and result["saved"] is False
    assert "not saved" in " ".join(result["warnings"])
    entry, = browser.service.store.list_sessions()
    assert entry.id == result["plan"]["id"]


def test_profile_import_upload_and_reference_controls_share_cli_storage(browser, tmp_path):
    profile = AthleteProfile(name="CrossFit Athlete", limitations="Wrist discomfort", goals="Quarterfinals")
    assert post(browser, "/api/profile", {"profile": profile.to_dict()})[0] == 200
    _, first, _ = post(browser, "/api/chat", {"message": "CrossFit aerobic capacity"})
    path = tmp_path / "Week 1.txt"
    path.write_text("Monday\nEasy bike: 30 minutes at talk-test pace.")
    status, imported, _ = post(browser, "/api/import", {"path": str(path), "chat_id": first["session"]["id"]})
    assert status == 200 and imported["entry"]["profile"] == profile.to_dict()
    assert imported["session"]["context_ids"] == [imported["entry"]["id"]]
    assert imported["entry"]["final_plan"] == path.read_text()
    encoded = base64.b64encode(b"Wednesday: CrossFit strength and easy recovery.").decode()
    status, uploaded, _ = post(browser, "/api/upload", {"filename": "Week 2.md", "data": encoded})
    assert status == 200 and uploaded["entry"]["request"].endswith("Week 2.md")
    assert "gamescoach-upload" not in json.dumps(uploaded)
    assert Store(browser.service.store.root).load_profile() == profile


@pytest.mark.parametrize("payload", [
    {"filename": "../outside.md", "data": "eA=="},
    {"filename": "C:\\private.md", "data": "eA=="},
    {"filename": "a.py", "data": "eA=="},
    {"filename": "a.txt", "data": "not base64"},
    {"filename": "a.txt", "data": ""},
])
def test_upload_rejects_paths_unsupported_extensions_and_invalid_content(browser, payload):
    assert post(browser, "/api/upload", payload)[0] == 400
    assert browser.service.store.list_sessions() == []


def test_reset_requires_exact_confirmation_and_full_reset_clears_custom_store_only(browser, tmp_path):
    plan(browser)
    browser.service.store.save_profile(AthleteProfile(name="Old athlete"))
    original = tmp_path / "original.md"
    original.write_text("CrossFit notes stay on disk.")
    assert post(browser, "/api/memory/reset", {"scope": "all", "confirmation": "yes"})[0] == 400
    assert len(browser.service.store.list_sessions()) == 1
    status, result, _ = post(browser, "/api/memory/reset", {"scope": "all", "confirmation": "RESET"})
    assert status == 200 and result["deleted"]["sessions"] == 1
    assert result["deleted"]["profile"] is True
    assert result["settings"] == {"auto_references": False, "coach_notes": False}
    state = request(browser)[1]
    assert state["sessions"] == [] and state["profile"] is None
    assert original.read_text() == "CrossFit notes stay on disk."
    assert not (tmp_path / "unused-default").exists()


def test_damaged_records_still_allow_state_and_full_reset(browser):
    root = browser.service.store.root
    (root / "sessions").mkdir(parents=True)
    (root / "sessions" / "abcdef123456.json").write_text("invalid private JSON")
    (root / "profile.json").write_text("invalid profile")
    status, state, _ = request(browser)
    assert status == 200 and state["notices"] and state["memory"]["unknown_sessions"] == 1
    assert "invalid private JSON" not in json.dumps(state)
    assert post(browser, "/api/memory/reset", {"scope": "all", "confirmation": "RESET"})[0] == 200
    assert request(browser)[1]["notices"] == []


def test_deeply_nested_corrupt_session_does_not_block_memory_reset(browser):
    folder = browser.service.store.root / "sessions"
    folder.mkdir(parents=True)
    (folder / "abcdef123456.json").write_text("[" * 2000 + "]" * 2000)
    status, state, _ = request(browser)
    assert status == 200 and state["notices"]
    assert post(browser, "/api/memory/reset", {"scope": "all", "confirmation": "RESET"})[0] == 200


def test_deeply_nested_corrupt_pdf_config_does_not_prevent_server_start(tmp_path):
    folder = tmp_path / "data" / "library" / "sources"
    folder.mkdir(parents=True)
    (folder / "folder.json").write_text("[" * 2000 + "]" * 2000)
    server = web.create_server(Store(tmp_path / "data"), demo=True)
    try:
        assert server.service.notices
        result = server.service.mutate("/api/memory/reset", {"scope": "all", "confirmation": "RESET"})
        assert result["deleted"]["watched_folder"] is True
    finally:
        server.server_close()


def test_active_coaching_prevents_reset_and_other_mutations_until_complete(browser, monkeypatch):
    entered = threading.Event()
    finish = threading.Event()
    original = ChatCoach.reply

    def waiting(self, *args, **kwargs):
        entered.set()
        assert finish.wait(5)
        return original(self, *args, **kwargs)

    monkeypatch.setattr(ChatCoach, "reply", waiting)
    results = []
    worker = threading.Thread(target=lambda: results.append(post(browser, "/api/chat", {"message": "CrossFit aerobic training"})))
    worker.start()
    try:
        assert entered.wait(3)
        for path, payload in (("/api/memory/reset", {"scope": "all", "confirmation": "RESET"}), ("/api/profile", {"profile": {}}), ("/api/chat", {"message": "another request"})):
            assert post(browser, path, payload)[0] == 409
        assert request(browser)[0] == 409
    finally:
        finish.set()
        worker.join(timeout=5)
    assert results[0][0] == 200
    assert len(browser.service.store.list_sessions()) == 1
    assert post(browser, "/api/memory/reset", {"scope": "history", "confirmation": "RESET"})[0] == 200
    assert browser.service.store.list_sessions() == []


def test_unexpected_errors_are_generic_and_do_not_log_or_return_private_values(browser, monkeypatch, capsys):
    def fail(*args):
        raise RuntimeError("SECRET_DO_NOT_RENDER")

    monkeypatch.setattr(browser.service, "mutate", fail)
    status, result, _ = post(browser, "/api/chat", {"message": "CrossFit"})
    assert status == 500 and "SECRET_DO_NOT_RENDER" not in json.dumps(result)
    assert "SECRET_DO_NOT_RENDER" not in capsys.readouterr().err


def pdf_data():
    from pypdf import PdfWriter
    from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

    writer = PdfWriter()
    page = writer.add_blank_page(width=612, height=792)
    font = DictionaryObject({NameObject("/Type"): NameObject("/Font"), NameObject("/Subtype"): NameObject("/Type1"), NameObject("/BaseFont"): NameObject("/Helvetica")})
    page[NameObject("/Resources")] = DictionaryObject({NameObject("/Font"): DictionaryObject({NameObject("/F1"): font})})
    stream = DecodedStreamObject()
    stream.set_data(b"BT /F1 12 Tf 50 700 Td (CrossFit mechanics consistency intensity and aerobic capacity.) Tj ET")
    page[NameObject("/Contents")] = writer._add_object(stream)
    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()


def test_uploaded_pdf_is_retained_without_upload_source_and_can_be_removed(browser):
    status, uploaded, _ = post(browser, "/api/upload", {"filename": "CrossFit Methodology.pdf", "data": base64.b64encode(pdf_data()).decode()})
    assert status == 200, uploaded
    guide = uploaded["guide"]
    assert guide["filename"] == "CrossFit Methodology.pdf" and guide["pages"] == 1
    assert set(guide) == {"id", "filename", "pages"}
    state = request(browser)[1]
    assert state["library"] == [guide] and state["memory"]["guides"] == 1
    assert "mechanics consistency" not in json.dumps(state)
    assert browser.service.library.brief()[0]["filename"] == guide["filename"]
    assert post(browser, "/api/library/remove", {"id": guide["id"]})[0] == 200
    assert browser.service.library.brief() == []


def test_registered_pdf_folder_uses_custom_store_and_unwatch_retains_guide(browser, tmp_path):
    folder = tmp_path / "CrossFit PDFs"
    folder.mkdir()
    source = folder / "Methodology.pdf"
    source.write_bytes(pdf_data())
    status, added, _ = post(browser, "/api/library", {"path": str(folder)})
    assert status == 200 and added["imported"] == 1
    state = request(browser)[1]
    assert state["folder"] == {"folder": str(folder), "files": 1}
    assert len(state["library"]) == 1
    assert post(browser, "/api/library/unwatch", {})[0] == 200
    state = request(browser)[1]
    assert state["folder"] is None and len(state["library"]) == 1
    assert post(browser, "/api/memory/reset", {"scope": "all", "confirmation": "RESET"})[0] == 200
    assert request(browser)[1]["library"] == [] and source.exists()


def test_memory_settings_validate_exact_booleans_and_gui_new_chat_still_uses_explicit_references(browser):
    assert post(browser, "/api/memory/settings", {"auto_references": "false", "coach_notes": False})[0] == 400
    status, settings, _ = post(browser, "/api/memory/settings", {"auto_references": True, "coach_notes": False})
    assert status == 200 and settings["settings"] == {"auto_references": True, "coach_notes": False}
    plan(browser)
    result = post(browser, "/api/chat", {"message": "CrossFit aerobic work"})[1]
    assert result["session"]["context_ids"] == []
    assert request(browser)[1]["memory"]["settings"] == settings["settings"]


def test_live_backend_reuses_engine_three_calls_gemini_leads_and_reset_disables_external_notes(browser, monkeypatch, tmp_path):
    calls = []
    notes = tmp_path / "external-notes.md"
    notes.write_text("STALE_OLD_CROSSFIT_TRAINING_EMPHASIS")
    monkeypatch.setenv("GAMESCOACH_COACH_NOTES_FILE", str(notes))
    monkeypatch.setenv("OPENAI_API_KEY", "fake-openai-key")
    monkeypatch.setenv("GEMINI_API_KEY", "fake-gemini-key")
    browser.service.demo = False
    for classname, name in (("OpenAIProvider", "ChatGPT"), ("GeminiProvider", "Gemini")):
        class FakeProvider:
            def __init__(self, key, model, provider_name=name):
                self.name = provider_name
                self.model = model

            def generate(self, *, instructions, prompt):
                calls.append((self.name, instructions, prompt))
                return f"CrossFit recommendation {len(calls)}"

        monkeypatch.setattr(chat_module, classname, FakeProvider)
    # The server's Store differs from the process environment root. Reset and
    # subsequent notes selection must both honor this exact personal data root.
    assert post(browser, "/api/memory/reset", {"scope": "all", "confirmation": "RESET"})[0] == 200
    status, result, _ = post(browser, "/api/chat", {"message": "CrossFit aerobic progression", "plan_kind": "training"})
    assert status == 200, result
    assert [name for name, _, _ in calls] == ["Gemini", "ChatGPT", "Gemini"]
    assert result["session"]["demo"] is False and result["plan"]["kind"] == "training"
    assert all("STALE_OLD_CROSSFIT_TRAINING_EMPHASIS" not in prompt for _, _, prompt in calls)
    assert notes.exists()


def test_corrupt_memory_settings_have_complete_state_fallback_and_reset_remains_available(browser):
    browser.service.store.root.mkdir(parents=True)
    (browser.service.store.root / "memory.json").write_text("broken private JSON")
    status, state, _ = request(browser)
    assert status == 200 and state["memory"]["sessions"] == 0 and state["notices"]
    assert post(browser, "/api/memory/reset", {"scope": "all", "confirmation": "RESET"})[0] == 200
    assert request(browser)[1]["notices"] == []


@pytest.mark.parametrize("kwargs", [{"port": -1}, {"port": 65536}, {"port": True}, {"demo": "yes"}])
def test_server_options_are_validated_before_binding(kwargs):
    with pytest.raises(ValueError):
        web.create_server(**kwargs)
