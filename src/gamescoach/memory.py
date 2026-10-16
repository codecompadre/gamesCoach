"""Explicit controls for the athlete's private, locally retained coaching data."""

import json
import os
from pathlib import Path
import stat
import tempfile

from .storage import Store, StoreError


_DEFAULTS = {"auto_references": True, "coach_notes": True}
_PLAN_KINDS = {"workout", "training", "nutrition", "competition"}
_MAX_SESSION_STATUS_BYTES = 16 * 1024 * 1024


def _root(store: Store | None) -> Path:
    # Do not resolve links: validate their lexical locations before opening them.
    root = Path(os.path.abspath((store or Store()).root))
    for path in reversed((root, *root.parents)):
        try:
            info = path.lstat()
        except FileNotFoundError:
            continue
        except OSError:
            raise StoreError("Cannot inspect local memory folders. Check their permissions.") from None
        if stat.S_ISLNK(info.st_mode):
            raise StoreError("Memory controls cannot use linked data folders. Choose a real GAMESCOACH_DATA_DIR folder.")
        if not stat.S_ISDIR(info.st_mode):
            raise StoreError("The local memory path must contain only real folders.")
    return root


def _check(path: Path, *, directory: bool = False) -> bool:
    try:
        info = path.lstat()
    except FileNotFoundError:
        return False
    except OSError:
        raise StoreError("Cannot inspect local memory files. Check their permissions.") from None
    expected = stat.S_ISDIR if directory else stat.S_ISREG
    if stat.S_ISLNK(info.st_mode) or not expected(info.st_mode):
        raise StoreError("Memory controls found an unsafe linked or non-regular app file or folder; no reset was started.")
    return True


def _files(root: Path, name: str) -> list[Path]:
    directory = root / name
    if not _check(directory, directory=True):
        return []
    try:
        paths = sorted(path for path in directory.iterdir() if path.suffix == ".json" or (name == "sessions" and path.name.startswith(".chat-") and path.suffix == ".tmp"))
    except OSError:
        raise StoreError("Cannot list local memory files. Check their permissions.") from None
    for path in paths:
        _check(path)
    return paths


def _settings(path: Path) -> dict:
    if not _check(path):
        return dict(_DEFAULTS)
    try:
        with path.open(encoding="utf-8") as handle:
            data = json.loads(handle.read(4097))
        return _validate_settings(data)
    except (OSError, UnicodeError, ValueError, RecursionError, StoreError):
        raise StoreError("Cannot read memory settings. Check memory.json, or use a full memory reset to replace damaged settings.") from None


def _validate_settings(settings: dict) -> dict:
    if not isinstance(settings, dict) or set(settings) != set(_DEFAULTS) or any(type(value) is not bool for value in settings.values()):
        raise StoreError("Memory settings require exactly auto_references and coach_notes, both true or false.")
    return dict(settings)


def memory_settings(store: Store | None = None) -> dict:
    """Return persisted controls; existing installations retain current behavior."""
    return _settings(_root(store) / "memory.json")


def save_memory_settings(settings: dict, store: Store | None = None) -> dict:
    """Atomically save strict boolean controls without storing credentials."""
    settings = _validate_settings(settings)
    root = _root(store)
    _check(root / "memory.json")
    temporary = None
    try:
        root.mkdir(parents=True, mode=0o700, exist_ok=True)
        _root(Store(root))
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=root, prefix=".memory-", suffix=".tmp", delete=False) as handle:
            temporary = Path(handle.name)
            json.dump(settings, handle, sort_keys=True, allow_nan=False)
            handle.write("\n")
        os.replace(temporary, root / "memory.json")
    except OSError:
        raise StoreError("Cannot save memory settings. Check local data directory permissions.") from None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return settings


def _inventory(root: Path) -> dict:
    sessions = _files(root, "sessions")
    guides = _files(root, "library")
    library = root / "library"
    _check(library / "sources", directory=True)
    _check(library / ".cache", directory=True)
    return {
        "sessions": sessions,
        "guides": guides,
        "profile": _check(root / "profile.json"),
        "watched_folder": _check(library / "sources" / "folder.json"),
        "prepared_cache": _check(library / ".cache" / "prepared.json"),
    }


def memory_status(store: Store | None = None) -> dict:
    """Summarize retention without requiring valid saved athlete records."""
    root = _root(store)
    inventory = _inventory(root)
    result = {
        "sessions": 0, "chats": 0, "plans": 0, "documents": 0,
        "guides": len(inventory["guides"]), "unknown_sessions": 0,
        "has_profile": inventory["profile"],
        "watched_folder": inventory["watched_folder"],
        "settings": _settings(root / "memory.json"),
    }
    for path in inventory["sessions"]:
        if path.suffix != ".json":
            continue
        result["sessions"] += 1
        try:
            if path.stat().st_size > _MAX_SESSION_STATUS_BYTES:
                raise ValueError
            with path.open(encoding="utf-8") as handle:
                data = json.load(handle)
            kind = data.get("kind") if isinstance(data, dict) else None
            if kind == "chat":
                result["chats"] += 1
            elif kind == "document":
                result["documents"] += 1
            elif isinstance(kind, str) and kind in _PLAN_KINDS:
                result["plans"] += 1
            else:
                result["unknown_sessions"] += 1
        except (OSError, UnicodeError, ValueError, RecursionError):
            result["unknown_sessions"] += 1
    return result


def reset_memory(scope: str, confirmation: str, store: Store | None = None) -> dict:
    """Delete selected app-owned retention only after an exact explicit confirmation.

    A full reset also disables automatic history references and external coach
    notes. Original PDFs, documents, notes, and API key configuration remain intact.
    """
    if scope not in ("history", "all") or not isinstance(scope, str):
        raise StoreError("Choose memory reset scope history or all.")
    if confirmation != "RESET" or not isinstance(confirmation, str):
        raise StoreError("Type RESET exactly to confirm clearing saved memory.")
    root = _root(store)
    inventory = _inventory(root)
    # Preflight every known target before any settings or retained data changes.
    _check(root / "memory.json")
    settings = {"auto_references": False, "coach_notes": False} if scope == "all" else _settings(root / "memory.json")
    deleted = {"sessions": 0, "guides": 0, "profile": False, "watched_folder": False, "prepared_cache": False}
    mutated = False
    try:
        if scope == "all":
            save_memory_settings(settings, Store(root))
            mutated = True
        for path in inventory["sessions"]:
            path.unlink()
            mutated = True
            if path.suffix == ".json":
                deleted["sessions"] += 1
        if scope == "all":
            for path in inventory["guides"]:
                path.unlink()
                mutated = True
                deleted["guides"] += 1
            for key, path in (
                ("profile", root / "profile.json"),
                ("watched_folder", root / "library" / "sources" / "folder.json"),
                ("prepared_cache", root / "library" / ".cache" / "prepared.json"),
            ):
                if inventory[key]:
                    path.unlink()
                    mutated = True
                    deleted[key] = True
            # The process may already hold prepared source text. Evict it as
            # well as the disk cache so the next coaching request starts clean.
            from .knowledge import _PREPARED
            _PREPARED.pop(str(root / "library"), None)
        status = memory_status(Store(root))
    except (OSError, StoreError):
        if mutated:
            raise StoreError("Memory reset was only partially completed. Some local data or settings changed; inspect memory status before retrying. Original source files and API keys were not changed.") from None
        raise StoreError("Memory reset could not start. Check local file permissions; no retained data was cleared.") from None
    return {"scope": scope, "deleted": deleted, "settings": settings, "status": status}
