"""Private local profile and plan storage; no credentials are stored here."""

import json
import os
from datetime import datetime
from pathlib import Path
import re
import tempfile

from .models import AthleteProfile, CoachingSession


class StoreError(Exception):
    """An actionable local storage error."""


class Store:
    def __init__(self, root: str | Path | None = None) -> None:
        if root is None:
            root = os.environ.get("GAMESCOACH_DATA_DIR") or Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share") / "gamescoach"
        self.root = Path(root).expanduser()

    def _prepare(self) -> None:
        try:
            self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
            (self.root / "sessions").mkdir(mode=0o700, exist_ok=True)
        except OSError:
            raise StoreError("Cannot create local data folders. Check GAMESCOACH_DATA_DIR and its permissions.") from None

    @staticmethod
    def _read(path: Path) -> dict:
        try:
            with path.open(encoding="utf-8") as handle:
                return json.load(handle)
        except (OSError, ValueError):
            raise StoreError(f"Cannot read {path.name}. Check file permissions and JSON contents.") from None

    def load_profile(self) -> AthleteProfile | None:
        path = self.root / "profile.json"
        if not path.exists():
            return None
        try:
            return AthleteProfile.from_dict(self._read(path))
        except ValueError as exc:
            raise StoreError(f"Invalid saved profile: {exc}") from None

    def save_profile(self, profile: AthleteProfile) -> None:
        self._prepare()
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=self.root, delete=False) as handle:
                temporary = Path(handle.name)
                json.dump(profile.to_dict(), handle, indent=2, allow_nan=False)
                handle.write("\n")
            os.replace(temporary, self.root / "profile.json")
        except (OSError, ValueError):
            raise StoreError("Cannot save profile. Check local data directory permissions.") from None
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def save_session(self, session: CoachingSession) -> None:
        self._prepare()
        path = self.root / "sessions" / f"{session.id}.json"
        owned = False
        try:
            # Exclusive creation avoids overwriting a prior plan, even on an ID collision.
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            owned = True
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(session.to_dict(), handle, indent=2, allow_nan=False)
                handle.write("\n")
        except FileExistsError:
            raise StoreError("A plan with this ID already exists; it was not overwritten.") from None
        except (OSError, ValueError):
            if owned:
                path.unlink(missing_ok=True)
            raise StoreError("Cannot save plan. Check local data directory permissions.") from None

    def get_session(self, session_id: str) -> CoachingSession:
        if not re.fullmatch(r"[a-f0-9]{12,32}", session_id):
            raise StoreError("Invalid plan ID. Use an ID listed by gamescoach history.")
        path = self.root / "sessions" / f"{session_id}.json"
        if not path.exists():
            raise StoreError("Plan not found. Check gamescoach history for saved IDs.")
        try:
            session = CoachingSession.from_dict(self._read(path))
        except ValueError as exc:
            raise StoreError(f"Invalid saved plan {session_id}: {exc}") from None
        if session.id != session_id:
            raise StoreError("Saved plan ID does not match its filename.")
        return session

    def save_chat(self, session: CoachingSession) -> None:
        """Atomically persist the latest complete exchange without replacing plans."""
        if session.kind != "chat":
            raise StoreError("Only conversations can be updated with save_chat.")
        self._prepare()
        folder = self.root / "sessions"
        path = folder / f"{session.id}.json"
        if path.exists() and self.get_session(session.id).kind != "chat":
            raise StoreError("This ID belongs to an existing plan; it was not overwritten.")
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=folder, prefix=".chat-", suffix=".tmp", delete=False) as handle:
                temporary = Path(handle.name)
                json.dump(session.to_dict(), handle, indent=2, allow_nan=False)
                handle.write("\n")
            os.replace(temporary, path)
        except (OSError, ValueError):
            raise StoreError("Cannot save conversation. Check local data directory permissions.") from None
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def list_sessions(self, limit: int = 10) -> list[CoachingSession]:
        if type(limit) is not int or limit < 1:
            raise StoreError("History limit must be a positive integer.")
        try:
            files = list((self.root / "sessions").glob("*.json"))
        except OSError:
            raise StoreError("Cannot list local plans. Check local data directory permissions.") from None
        sessions = [self.get_session(path.stem) for path in files]
        sessions.sort(key=lambda s: s.created_at, reverse=True)
        return sessions[:limit]

    def list_chats(self, *, demo: bool) -> list[CoachingSession]:
        """Find all compatible chats, with the most recently saved one first."""
        if type(demo) is not bool:
            raise StoreError("Choose live or demo conversations.")
        try:
            matches = []
            for path in (self.root / "sessions").glob("*.json"):
                session = self.get_session(path.stem)
                if session.kind == "chat" and session.demo == demo:
                    matches.append((path.stat().st_mtime_ns, datetime.fromisoformat(session.created_at).timestamp(), session))
            matches.sort(key=lambda item: (item[0], item[1]), reverse=True)
            return [item[2] for item in matches]
        except OSError:
            raise StoreError("Cannot list saved conversations. Check local data directory permissions.") from None
