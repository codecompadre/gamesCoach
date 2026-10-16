"""A private loopback-only browser console for the existing coaching engine.

The browser is a local view of the same files used by the terminal. This module
does not store credentials, expose a LAN listener, or retry paid requests.
"""

from __future__ import annotations

import base64
import binascii
from dataclasses import replace
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import secrets
import tempfile
import threading
from urllib.parse import urlsplit
import webbrowser

from . import __version__
from .chat import ChatCoach
from .config import (
    DEFAULT_GEMINI_MODEL, DEFAULT_OPENAI_MODEL, GEMINI_KEY_NAMES,
    OPENAI_KEY_NAMES, credential_binding,
)
from .documents import MAX_FILE_BYTES, DocumentError, document_path, import_document
from .knowledge import MAX_PDF_BYTES, KnowledgeError, Library
from .library_sources import clear_folder, configure_folder, folder_status, sync_folder
from .memory import memory_settings, memory_status, reset_memory, save_memory_settings
from .models import AthleteProfile, CoachingSession
from .providers import ProviderError
from .storage import Store, StoreError


_STATIC = Path(__file__).with_name("static")
_MAX_BODY_BYTES = 36 * 1024 * 1024
_MAX_JSON_BYTES = 256 * 1024
_MAX_REFERENCES = 64
_SESSION_ID = re.compile(r"[a-f0-9]{12,32}\Z")
_DEFAULT_REQUESTS = {
    "workout": "Create a balanced CrossFit workout with warm-up, scaling, and cooldown.",
    "training": "Create a progressive CrossFit training plan with recovery and measurable goals.",
    "nutrition": "Suggest practical nutrition goals that support CrossFit training and recovery.",
    "competition": "Create a CrossFit competition preparation plan with pacing, skills, and tapering.",
}
_EXPECTED = (StoreError, ProviderError, KnowledgeError, DocumentError, ValueError)


class WebError(ValueError):
    """A request error that is safe to display in the local browser."""


def _object(value: object) -> dict:
    if not isinstance(value, dict):
        raise WebError("Send a JSON object.")
    return value


def _text(data: dict, key: str, *, optional: bool = False, maximum: int = 12000) -> str | None:
    value = data.get(key)
    if optional and value is None:
        return None
    if not isinstance(value, str) or not value.strip() or len(value) > maximum or "\x00" in value:
        raise WebError(f"Provide {key} as nonempty text of at most {maximum} characters.")
    return value.strip()


def _session_id(data: dict, key: str = "id", *, optional: bool = False) -> str | None:
    value = _text(data, key, optional=optional, maximum=32)
    if value is not None and not _SESSION_ID.fullmatch(value):
        raise WebError("Choose a saved conversation or plan ID.")
    return value


def _reference_ids(value: object) -> list[str]:
    if (
        not isinstance(value, list) or len(value) > _MAX_REFERENCES
        or not all(isinstance(item, str) and _SESSION_ID.fullmatch(item) for item in value)
        or len(set(value)) != len(value)
    ):
        raise WebError("Choose up to 64 distinct saved reference IDs.")
    return value


class WebService:
    """Application operations, shared under the HTTP server's mutation lock."""

    def __init__(self, store: Store, *, demo: bool) -> None:
        self.store = store
        self.demo = demo
        self.library = Library(store.root / "library")
        self.lock = threading.Lock()
        self.notices: list[str] = []
        # Opening the UI is local work only. A broken library must not prevent
        # opening Memory to remove it, so startup errors become visible notices.
        try:
            sync_folder(library=self.library)
            self.library.prepare()
        except (KnowledgeError, OSError, ValueError, RecursionError) as exc:
            self.notices.append(str(exc) if isinstance(exc, KnowledgeError) else "The local PDF library could not be prepared. Review Library or reset its retained memory.")

    def state(self) -> dict:
        notices = list(self.notices)

        def read(operation, fallback):
            try:
                return operation()
            except _EXPECTED as exc:
                notices.append(str(exc))
                return fallback
            except (RecursionError, UnicodeError):
                notices.append("A saved record is damaged or excessively nested. Review local memory or use a full reset.")
                return fallback

        profile = read(self.store.load_profile, None)
        entries = read(lambda: self.store.list_sessions(limit=10000), [])
        guides = read(self.library.list_guides, [])
        settings = read(lambda: memory_settings(self.store), {"auto_references": False, "coach_notes": False})
        return {
            "version": __version__,
            "demo": self.demo,
            "profile": profile.to_dict() if profile else None,
            "sessions": [
                {key: getattr(entry, key) for key in ("id", "created_at", "kind", "request", "demo")}
                for entry in entries
            ],
            "library": [{key: guide[key] for key in ("id", "filename", "pages")} for guide in guides],
            "folder": read(lambda: folder_status(library=self.library), None),
            "config": {
                "models": {
                    "ChatGPT": os.environ.get("OPENAI_MODEL", DEFAULT_OPENAI_MODEL),
                    "Gemini": os.environ.get("GEMINI_MODEL", DEFAULT_GEMINI_MODEL),
                },
                "keys": {
                    "ChatGPT": bool(credential_binding(OPENAI_KEY_NAMES)),
                    "Gemini": bool(credential_binding(GEMINI_KEY_NAMES)),
                },
                "notes": {
                    "configured": bool(os.environ.get("GAMESCOACH_COACH_NOTES_FILE")),
                    "enabled": settings["coach_notes"],
                },
            },
            "memory": read(lambda: memory_status(self.store), {
                "sessions": 0, "chats": 0, "plans": 0, "documents": 0,
                "guides": 0, "unknown_sessions": 0, "has_profile": profile is not None,
                "watched_folder": False, "settings": settings,
            }),
            "notices": notices,
        }

    def get_session(self, session_id: str) -> dict:
        return {"session": self.store.get_session(session_id).to_dict()}

    def _context(self, data: dict, session: CoachingSession | None, warnings: list[str]) -> list[CoachingSession]:
        explicit = "reference_ids" in data
        if explicit:
            identifiers = _reference_ids(data["reference_ids"])
        elif session is not None:
            identifiers = session.context_ids
        else:
            identifiers = []
        context = []
        for identifier in identifiers:
            if session is not None and identifier == session.id:
                raise WebError("The current conversation is already included; choose another reference.")
            try:
                context.append(self.store.get_session(identifier))
            except StoreError:
                if explicit:
                    raise WebError("One of the selected references is unavailable. Refresh History and choose it again.") from None
                warnings.append(f"Saved reference {identifier} is unavailable and was left out.")
        return context

    def _coach(self, profile: AthleteProfile, context: list[CoachingSession], session: CoachingSession | None = None) -> ChatCoach:
        return ChatCoach(profile, context, demo=self.demo, session=session, library=self.library, store=self.store)

    def chat(self, data: dict) -> dict:
        identifier = _session_id(data, optional=True)
        message = _text(data, "message")
        plan_kind = data.get("plan_kind")
        if plan_kind is not None and (not isinstance(plan_kind, str) or plan_kind not in _DEFAULT_REQUESTS):
            raise WebError("Choose workout, training, nutrition or competition.")
        if message.startswith("/"):
            command = re.fullmatch(r"/\s*([^\s]+)(?:\s+([\s\S]*))?", message)
            name, argument = (command.group(1).lower(), (command.group(2) or "").strip()) if command else ("", "")
            if name in _DEFAULT_REQUESTS:
                if argument.lower() in ("--help", "-h", "/help"):
                    raise WebError(f"/{name} [REQUEST] creates and saves a CrossFit {name} plan. Use the plan selector beside the message box, or add your request after the command.")
                if plan_kind is not None and plan_kind != name:
                    raise WebError("The selected plan type and slash command differ. Choose one plan type.")
                plan_kind = name
                message = argument or _DEFAULT_REQUESTS[name]
            else:
                raise WebError("Use the browser's New chat, History, Files, Library and Memory controls. Plan commands are /workout, /training, /nutrition and /competition.")
        profile = self.store.load_profile() or AthleteProfile()
        session = self.store.get_session(identifier) if identifier else None
        warnings: list[str] = []
        context = self._context(data, session, warnings)
        coach = self._coach(profile, context, session)
        # Never retry generation here. A failure leaves previously saved chats
        # untouched and the browser keeps the athlete's unsent text.
        completed = coach.reply(message, plan_kind=plan_kind) if plan_kind is not None else coach.reply(message)
        plan = coach.last_plan
        saved_plan = None
        if plan is not None:
            try:
                self.store.save_session(plan)
                saved_plan = plan
            except StoreError as exc:
                warnings.append(str(exc))
                warnings.append("The separate plan was not saved. Its reply remains in this conversation.")
        chat_saved = True
        try:
            self.store.save_chat(completed)
        except StoreError as exc:
            chat_saved = False
            warnings.append(str(exc))
            warnings.append("This reply is shown here but the conversation update was not saved. Copy it before leaving this page.")
            if saved_plan is not None:
                warnings.append("The separate plan is saved in History.")
        return {
            "session": completed.to_dict(),
            "plan": saved_plan.to_dict() if saved_plan else None,
            "saved": chat_saved,
            "context": {
                "reference_ids": [entry.id for entry in coach.context],
                "notice": coach.context_notice,
                "report": coach.context_report,
                "methodology_brief": coach.methodology_brief,
                "methodology_excerpts": coach.methodology_excerpts,
            },
            "warnings": warnings,
        }

    def profile(self, data: dict) -> dict:
        profile = AthleteProfile.from_dict(_object(data.get("profile")))
        self.store.save_profile(profile)
        return {"profile": profile.to_dict()}

    def import_file(self, data: dict) -> dict:
        path = _text(data, "path", maximum=4096)
        chat_id = _session_id(data, "chat_id", optional=True)
        profile = self.store.load_profile() or AthleteProfile()
        entry = import_document(path, profile)
        session = self.store.get_session(chat_id) if chat_id else None
        coach = None
        if session is not None:
            warnings: list[str] = []
            coach = self._coach(profile, self._context({}, session, warnings), session)
            coach.add_context(entry)
        self.store.save_session(entry)
        result = {"entry": entry.to_dict(), "warnings": []}
        if coach is not None:
            updated = replace(session, context_ids=[item.id for item in coach.context])
            try:
                self.store.save_chat(updated)
                result["session"] = updated.to_dict()
            except StoreError as exc:
                result["warnings"] = [str(exc), "The file is saved in History, but was not attached to this chat. Add it as a reference after fixing storage permissions."]
        return result

    def add_library(self, data: dict) -> dict:
        value = _text(data, "path", maximum=4096)
        path = document_path(value)
        if path.is_dir():
            result = configure_folder(value, library=self.library)
        else:
            guide = self.library.add(value)
            result = {"guide": {key: guide[key] for key in ("id", "filename", "pages")}}
        self.library.prepare()
        return result

    def upload(self, data: dict) -> dict:
        filename = _text(data, "filename", maximum=255)
        if (
            filename != Path(filename).name or "/" in filename or "\\" in filename
            or any(ord(char) < 32 for char in filename)
        ):
            raise WebError("Choose a file with a normal filename, without a folder path.")
        suffix = Path(filename).suffix.lower()
        if suffix not in (".pdf", ".docx", ".txt", ".md"):
            raise WebError("Choose a .pdf guide or a .docx, .txt or .md training file.")
        maximum = MAX_PDF_BYTES if suffix == ".pdf" else MAX_FILE_BYTES
        encoded = data.get("data")
        if not isinstance(encoded, str) or len(encoded) > 4 * ((maximum + 2) // 3):
            raise WebError("The selected file exceeds the 25 MB PDF or 10 MB training-file limit.")
        try:
            raw = base64.b64decode(encoded, validate=True)
        except (ValueError, binascii.Error):
            raise WebError("The upload could not be decoded. Choose the file again.") from None
        if not raw or len(raw) > maximum:
            raise WebError("Choose a nonempty file within the 25 MB PDF or 10 MB training-file limit.")
        # A private temporary directory preserves the display filename while
        # keeping the original upload out of the repository and cleaning it up.
        with tempfile.TemporaryDirectory(prefix="gamescoach-upload-") as directory:
            path = Path(directory) / filename
            path.write_bytes(raw)
            path.chmod(0o600)
            if suffix == ".pdf":
                return self.add_library({"path": str(path)})
            return self.import_file({"path": str(path), "chat_id": data.get("chat_id")})

    def references(self, data: dict) -> dict:
        identifier = _session_id(data)
        session = self.store.get_session(identifier)
        context = self._context({"reference_ids": data.get("reference_ids")}, session, [])
        coach = self._coach(self.store.load_profile() or AthleteProfile(), context, session)
        updated = replace(session, context_ids=[entry.id for entry in coach.context])
        self.store.save_chat(updated)
        return {"session": updated.to_dict()}

    def mutate(self, path: str, data: dict) -> dict:
        if path == "/api/chat":
            return self.chat(data)
        if path == "/api/profile":
            return self.profile(data)
        if path == "/api/import":
            return self.import_file(data)
        if path == "/api/upload":
            return self.upload(data)
        if path == "/api/library":
            return self.add_library(data)
        if path == "/api/library/remove":
            identifier = _text(data, "id", maximum=16)
            self.library.remove(identifier)
            self.library.prepare()
            return {"removed": identifier}
        if path == "/api/library/unwatch":
            clear_folder(library=self.library)
            return {"folder": None}
        if path == "/api/references":
            return self.references(data)
        if path == "/api/memory/settings":
            return {"settings": save_memory_settings(data, self.store)}
        if path == "/api/memory/reset":
            result = reset_memory(data.get("scope"), data.get("confirmation"), self.store)
            self.notices = []
            return result
        raise WebError("This action is unavailable. Refresh the browser page.")


class LocalHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address, handler, service: WebService) -> None:
        self.service = service
        self.csrf_token = secrets.token_urlsafe(32)
        super().__init__(address, handler)

    def handle_error(self, request, client_address) -> None:
        # Unexpected errors must not leak private profile text or credentials to
        # terminal logs. The handler returns a generic error whenever possible.
        pass


class LocalHandler(BaseHTTPRequestHandler):
    server: LocalHTTPServer
    server_version = "gamesCoach"
    sys_version = ""

    def log_message(self, format, *args) -> None:
        pass

    def setup(self) -> None:
        super().setup()
        self.connection.settimeout(15)

    def _headers(self, status: int, content_type: str, size: int) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(size))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Content-Security-Policy", "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self' data:; font-src 'self'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'")
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True

    def _json(self, status: int, data: dict) -> None:
        encoded = json.dumps(data, ensure_ascii=False, allow_nan=False).encode("utf-8")
        self._headers(status, "application/json; charset=utf-8", len(encoded))
        self.wfile.write(encoded)

    def _error(self, status: int, message: str) -> None:
        self._json(status, {"error": message})

    def _host(self) -> bool:
        port = self.server.server_address[1]
        return self.headers.get_all("Host", []) in ([f"127.0.0.1:{port}"], [f"localhost:{port}"])

    def _authorized(self, *, api: bool) -> bool:
        if not self._host():
            self._error(HTTPStatus.FORBIDDEN, "Open this app using its printed localhost address.")
            return False
        origins = self.headers.get_all("Origin", [])
        expected = "http://" + self.headers["Host"]
        if origins and origins != [expected]:
            self._error(HTTPStatus.FORBIDDEN, "Cross-origin requests are not allowed.")
            return False
        if api:
            tokens = self.headers.get_all("X-GamesCoach-Token", [])
            if len(tokens) != 1 or not secrets.compare_digest(tokens[0], self.server.csrf_token):
                self._error(HTTPStatus.FORBIDDEN, "Refresh the app page to authorize this local request.")
                return False
        return True

    def _path(self) -> str | None:
        try:
            parsed = urlsplit(self.path)
        except ValueError:
            self._error(HTTPStatus.BAD_REQUEST, "Invalid request path.")
            return None
        if parsed.scheme or parsed.netloc or parsed.query or parsed.fragment or "%" in parsed.path or "\\" in parsed.path:
            self._error(HTTPStatus.NOT_FOUND, "Page not found.")
            return None
        return parsed.path

    def do_GET(self) -> None:
        path = self._path()
        if path is None or not self._authorized(api=path.startswith("/api/")):
            return
        if path.startswith("/api/"):
            if not self.server.service.lock.acquire(blocking=False):
                self._error(HTTPStatus.CONFLICT, "Coaching or another local action is in progress. Wait for it to finish.")
                return
            try:
                if path == "/api/state":
                    result = self.server.service.state()
                elif re.fullmatch(r"/api/session/[a-f0-9]{12,32}", path):
                    result = self.server.service.get_session(path.rsplit("/", 1)[1])
                else:
                    self._error(HTTPStatus.NOT_FOUND, "Page not found.")
                    return
                self._json(HTTPStatus.OK, result)
            except _EXPECTED as exc:
                self._error(HTTPStatus.BAD_REQUEST, str(exc))
            except Exception:
                self._error(HTTPStatus.INTERNAL_SERVER_ERROR, "The local action failed. Your saved files were not intentionally changed. Check local data permissions and try again.")
            finally:
                self.server.service.lock.release()
            return
        allowed = {
            "/": ("index.html", "text/html; charset=utf-8"),
            "/index.html": ("index.html", "text/html; charset=utf-8"),
            "/app.js": ("app.js", "text/javascript; charset=utf-8"),
            "/styles.css": ("styles.css", "text/css; charset=utf-8"),
            "/app.css": ("app.css", "text/css; charset=utf-8"),
        }
        if path not in allowed:
            self._error(HTTPStatus.NOT_FOUND, "Page not found.")
            return
        name, mime = allowed[path]
        try:
            raw = (_STATIC / name).read_bytes()
        except OSError:
            self._error(HTTPStatus.NOT_FOUND, "The browser interface is missing. Reinstall gamesCoach and try again.")
            return
        if name == "index.html":
            raw = raw.replace(b"{{CSRF_TOKEN}}", self.server.csrf_token.encode("ascii"))
        self._headers(HTTPStatus.OK, mime, len(raw))
        self.wfile.write(raw)

    def do_POST(self) -> None:
        path = self._path()
        if path is None or not self._authorized(api=True):
            return
        if self.headers.get("Transfer-Encoding"):
            self._error(HTTPStatus.BAD_REQUEST, "Send a JSON request with a content length.")
            return
        lengths = self.headers.get_all("Content-Length", [])
        try:
            if len(lengths) != 1 or not lengths[0].isascii() or not lengths[0].isdigit():
                raise ValueError
            length = int(lengths[0])
        except ValueError:
            self._error(HTTPStatus.LENGTH_REQUIRED, "Send a JSON request with a valid content length.")
            return
        maximum = _MAX_BODY_BYTES if path == "/api/upload" else _MAX_JSON_BYTES
        if length > maximum:
            self._error(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "The request is too large. Use a 25 MB PDF or 10 MB training file; keep chat messages under 12000 characters.")
            return
        if self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower() != "application/json":
            self._error(HTTPStatus.UNSUPPORTED_MEDIA_TYPE, "Send application/json.")
            return
        try:
            raw = self.rfile.read(length)
            if len(raw) != length:
                raise ValueError
            data = _object(json.loads(raw.decode("utf-8"), parse_constant=lambda value: (_ for _ in ()).throw(ValueError())))
        except (ValueError, UnicodeError, TimeoutError, RecursionError):
            self._error(HTTPStatus.BAD_REQUEST, "The request must contain a complete valid JSON object.")
            return
        if not self.server.service.lock.acquire(blocking=False):
            self._error(HTTPStatus.CONFLICT, "Coaching or another local action is in progress. Wait for it to finish before changing memory or files.")
            return
        try:
            self._json(HTTPStatus.OK, self.server.service.mutate(path, data))
        except _EXPECTED as exc:
            self._error(HTTPStatus.BAD_REQUEST, str(exc))
        except Exception:
            self._error(HTTPStatus.INTERNAL_SERVER_ERROR, "The local action failed. No paid request will be retried automatically. Refresh History before trying again.")
        finally:
            self.server.service.lock.release()

    def do_OPTIONS(self) -> None:
        self._error(HTTPStatus.FORBIDDEN, "Cross-origin requests are not allowed.")


def create_server(store: Store | None = None, *, demo: bool = False, port: int = 0) -> LocalHTTPServer:
    """Build a testable local server; call serve_forever from its owner thread."""
    if type(demo) is not bool or type(port) is not int or not 0 <= port <= 65535:
        raise ValueError("Choose a valid demo mode and port from 0 to 65535.")
    service = WebService(store or Store(), demo=demo)
    return LocalHTTPServer(("127.0.0.1", port), LocalHandler, service)


def run_gui(*, demo: bool = False, port: int = 8765, open_browser: bool = True) -> int:
    """Run the personal browser UI until Ctrl+C in the launching terminal."""
    try:
        server = create_server(demo=demo, port=port)
    except OSError:
        raise StoreError("Cannot start the browser app on this port. Close the other instance or choose --port with a different number.") from None
    address = f"http://127.0.0.1:{server.server_address[1]}"
    print(f"gamesCoach {'OFFLINE DEMO' if demo else 'live coaching'}: {address}")
    print("Keep this terminal open. Press Ctrl+C here to stop the app.")
    if open_browser:
        try:
            webbrowser.open(address)
        except (OSError, webbrowser.Error):
            print("Open the address above in your Windows or Ubuntu browser.")
    try:
        server.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        print("\ngamesCoach browser app stopped.")
    finally:
        server.server_close()
    return 0
