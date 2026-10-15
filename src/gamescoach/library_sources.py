"""Automatically retain PDFs from one private, locally registered folder."""

import json
import os
from pathlib import Path
import re
import tempfile

from .documents import DocumentError, document_path
from .knowledge import KnowledgeError, Library, MAX_GUIDES, has_current_extraction

_VERSION = 1
_MAX_CONFIG_BYTES = 32768


def _config_path(library: Library) -> Path:
    # Only guide indexes belong in the library's top-level *.json namespace.
    return library.root / "sources" / "folder.json"


def _read_config(library: Library) -> dict | None:
    path = _config_path(library)
    try:
        with path.open(encoding="utf-8") as handle:
            text = handle.read(_MAX_CONFIG_BYTES + 1)
    except FileNotFoundError:
        return None
    except OSError:
        raise KnowledgeError("Cannot read the PDF folder setting. Check its local permissions or use library unwatch.") from None
    try:
        if len(text) > _MAX_CONFIG_BYTES:
            raise ValueError
        data = json.loads(text)
        if not isinstance(data, dict) or data.get("version") != _VERSION:
            raise ValueError
        folder, files = data["folder"], data["files"]
        if not isinstance(folder, str) or not folder or len(folder) > 4096 or "\x00" in folder:
            raise ValueError
        if not Path(folder).is_absolute() or not isinstance(files, dict) or len(files) > MAX_GUIDES:
            raise ValueError
        for name, entry in files.items():
            if not isinstance(name, str) or Path(name).name != name or "/" in name or "\\" in name or not name.lower().endswith(".pdf") or "\x00" in name:
                raise ValueError
            if not isinstance(entry, dict) or type(entry.get("size")) is not int or entry["size"] < 0:
                raise ValueError
            if type(entry.get("mtime_ns")) is not int or entry["mtime_ns"] < 0:
                raise ValueError
            if not isinstance(entry.get("guide_id"), str) or not re.fullmatch(r"[a-f0-9]{16}", entry["guide_id"]):
                raise ValueError
        return data
    except (ValueError, TypeError, KeyError):
        raise KnowledgeError("The saved PDF folder setting is invalid. Use library unwatch, then library watch PATH to register it again.") from None


def _save_config(library: Library, data: dict) -> None:
    path = _config_path(library)
    temporary = None
    try:
        library.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        library.root.chmod(0o700)
        path.parent.mkdir(mode=0o700, exist_ok=True)
        path.parent.chmod(0o700)
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, suffix=".tmp", delete=False) as handle:
            temporary = Path(handle.name)
            json.dump(data, handle, ensure_ascii=False)
            handle.write("\n")
        os.replace(temporary, path)
    except OSError:
        raise KnowledgeError("Cannot save the PDF folder setting. Check the coaching library's local permissions.") from None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _scan(folder: Path) -> list[Path]:
    try:
        if not folder.is_dir():
            raise OSError
        files = sorted((path for path in folder.iterdir() if path.suffix.lower() == ".pdf" and path.is_file()), key=lambda path: path.name.casefold())
    except OSError:
        raise KnowledgeError("Cannot access the registered PDF folder. Check that it exists and is mounted in Ubuntu (/mnt/c/ for Windows). Previously imported knowledge is retained.") from None
    if len(files) > MAX_GUIDES:
        raise KnowledgeError("The PDF folder contains more than six PDFs. Choose a folder containing at most six methodology guides.")
    return files


def _sync(library: Library, config: dict, *, force: bool = False) -> dict:
    folder = Path(config["folder"])
    files = _scan(folder)
    previous = config["files"]
    current = {}
    imported = unchanged = 0
    existing = {guide["id"]: guide for guide in library.list_guides()}
    existing_ids = set(existing)
    added_ids = set()
    try:
        for path in files:
            try:
                info = path.stat()
            except OSError:
                raise KnowledgeError("Cannot inspect a PDF in the registered folder. Check its local permissions; previously imported knowledge is retained.") from None
            old = previous.get(path.name)
            saved_guide = existing.get(old["guide_id"]) if old is not None else None
            unchanged_source = old is not None and old["size"] == info.st_size and old["mtime_ns"] == info.st_mtime_ns
            needs_refresh = saved_guide is not None and not has_current_extraction(saved_guide)
            if not force and unchanged_source and not needs_refresh:
                # Missing indexes may have been explicitly removed by the athlete.
                # Respect that choice until the source changes or is re-registered.
                current[path.name] = old
                unchanged += 1
                continue
            guide = library.add(path)
            existing[guide["id"]] = guide
            if guide["id"] not in existing_ids:
                added_ids.add(guide["id"])
            current[path.name] = {"size": info.st_size, "mtime_ns": info.st_mtime_ns, "guide_id": guide["id"]}
            imported += 1
    except KnowledgeError:
        # A later damaged PDF or capacity/write failure must not leave both an
        # old and a partially imported new edition in the coaches' context.
        for identifier in added_ids:
            try:
                library.remove(identifier)
            except KnowledgeError:
                raise KnowledgeError("PDF folder sync failed and its partial imports could not be removed. Check local library permissions and library list before coaching.") from None
        raise

    # Validate and save every changed PDF before dropping its previous edition.
    # At the six-guide limit Library.add raises and leaves old knowledge intact.
    kept_ids = {entry["guide_id"] for entry in current.values()}
    kept_ids.update(entry["guide_id"] for name, entry in previous.items() if name not in current)
    replace_ids = {
        previous[name]["guide_id"]
        for name, entry in current.items()
        if name in previous and previous[name]["guide_id"] != entry["guide_id"]
    } - kept_ids
    for identifier in replace_ids & existing_ids:
        library.remove(identifier)
    updated = {"version": _VERSION, "folder": config["folder"], "files": current}
    if updated != config or force:
        _save_config(library, updated)
    return {"folder": config["folder"], "imported": imported, "unchanged": unchanged, "guides": len(library.list_guides())}


def configure_folder(value: str | Path) -> dict:
    """Register and import a local PDF folder, accepting Windows/WSL paths."""
    try:
        folder = document_path(value).resolve()
    except (DocumentError, OSError, ValueError, RuntimeError):
        raise KnowledgeError("Enter an accessible PDF folder path. Windows folders in Ubuntu are normally under /mnt/c/.") from None
    library = Library()
    existing = _read_config(library)
    previous = existing["files"] if existing is not None and existing["folder"] == str(folder) else {}
    return _sync(library, {"version": _VERSION, "folder": str(folder), "files": previous}, force=True)


def sync_folder() -> dict | None:
    """Import changed PDFs and refresh legacy extraction once after an upgrade."""
    library = Library()
    config = _read_config(library)
    return None if config is None else _sync(library, config)


def folder_status() -> dict | None:
    """Return local registration details without opening the source folder."""
    library = Library()
    config = _read_config(library)
    if config is None:
        return None
    return {"folder": config["folder"], "files": len(config["files"])}


def clear_folder() -> None:
    """Stop automatic importing while retaining all indexed knowledge."""
    try:
        _config_path(Library()).unlink(missing_ok=True)
    except OSError:
        raise KnowledgeError("Cannot remove the PDF folder setting. Check its local permissions.") from None
