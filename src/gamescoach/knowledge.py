"""A private, persistent PDF library with bounded, page-cited text retrieval."""

from collections import Counter, OrderedDict
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
from io import BytesIO
import json
import math
import os
from pathlib import Path
import re
import tempfile

from pypdf import PdfReader
from pypdf.errors import PdfReadError

from .documents import document_path
from .storage import Store

MAX_PDF_BYTES = 25 * 1024 * 1024
MAX_PAGES = 600
MAX_TEXT = 2000000
MAX_GUIDES = 6
PASSAGE_CHARS = 1600
MAX_EXCERPTS = 8
MAX_CONTEXT_CHARS = 16000
MAX_BRIEF_CHARS = 8000
BRIEF_PASSAGE_CHARS = 900
CACHE_VERSION = 1
# Increment when extraction support changes enough to require reading source
# PDFs again. Derived retrieval caches have a separate version above.
PDF_EXTRACTION_VERSION = 1
MAX_CACHE_BYTES = 64 * 1024 * 1024
# A few libraries can be active in one process (for example, isolated tests).
_PREPARED = OrderedDict()
_PREPARED_ROOTS = 8
BRIEF_THEMES = (
    frozenset("methodology functional mechanics consistency intensity fitness".split()),
    frozenset("programming progression progressive stimulus periodization balance adaptation varied variance".split()),
    frozenset("aerobic endurance conditioning cardiovascular metabolic threshold intervals".split()),
    frozenset("strength skill skills gymnastics weightlifting technique power".split()),
    frozenset("competition competitor competitors taper specificity weaknesses testing".split()),
    frozenset("recovery nutrition sleep rest fatigue fueling fuel carbohydrate protein".split()),
)
STOP_WORDS = set("a an and are as at be by can do for from have how i in is it me my of on or that the their this to we what when with you your crossfit".split())
FOUNDATION = "methodology constantly varied functional movement intensity mechanics consistency fitness programming aerobic strength competition recovery"


class KnowledgeError(ValueError):
    """A library error that never includes source document contents."""


def has_current_extraction(guide: dict) -> bool:
    """Legacy indexes can be refreshed without downgrading a newer extractor."""
    version = guide.get("extraction_version")
    return type(version) is int and version >= PDF_EXTRACTION_VERSION


def _terms(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]{2,}", text.lower())) - STOP_WORDS


def _chunks(text: str) -> list[str]:
    result = []
    start = 0
    while start < len(text):
        end = min(start + PASSAGE_CHARS, len(text))
        if end < len(text):
            boundary = text.rfind(" ", start + PASSAGE_CHARS // 2, end)
            if boundary > start:
                end = boundary
        passage = text[start:end].strip()
        if passage:
            result.append(passage)
        if end == len(text):
            break
        start = max(start + 1, end - 160)
    return result


_FOUNDATION_TERMS = _terms(FOUNDATION)


def _validate_guide(guide: dict, identifier: str) -> None:
    if not isinstance(guide, dict) or not isinstance(guide.get("id"), str) or not re.fullmatch(r"[a-f0-9]{16}", guide["id"]) or guide["id"] != identifier:
        raise ValueError
    if not isinstance(guide.get("filename"), str) or not guide["filename"] or len(guide["filename"]) > 255:
        raise ValueError
    if type(guide.get("pages")) is not int or not 1 <= guide["pages"] <= MAX_PAGES:
        raise ValueError
    passages = guide.get("passages")
    if not isinstance(passages, list) or not passages or len(passages) > 5000:
        raise ValueError
    for passage in passages:
        if not isinstance(passage, dict) or type(passage.get("page")) is not int or not 1 <= passage["page"] <= guide["pages"]:
            raise ValueError
        if not isinstance(passage.get("text"), str) or not passage["text"].strip() or len(passage["text"]) > PASSAGE_CHARS:
            raise ValueError


def _brief_window(text: str, start: int = 0) -> str:
    end = min(start + BRIEF_PASSAGE_CHARS, len(text))
    if end < len(text):
        boundary = text.rfind(" ", start + BRIEF_PASSAGE_CHARS // 2, end)
        if boundary > start:
            end = boundary
    return text[start:end].strip()


def _make_brief(candidates: list, frequency: dict) -> list[dict]:
    # This is deliberately extractive. We retain source words, rather than
    # manufacture a model-authored interpretation that has never been checked.
    options = []
    for guide, number, passage, _ in candidates:
        starts = [0]
        if len(passage["text"]) > BRIEF_PASSAGE_CHARS:
            start = passage["text"].find(" ", len(passage["text"]) - BRIEF_PASSAGE_CHARS)
            if start >= 0:
                starts.append(start + 1)
        windows = []
        for start in starts:
            text = _brief_window(passage["text"], start)
            words = _terms(text)
            themes = {index for index, theme in enumerate(BRIEF_THEMES) if words & theme}
            strength = sum(math.log(1 + len(candidates) / frequency[term]) for term in words & _FOUNDATION_TERMS)
            windows.append((guide, number, passage["page"], text, themes, strength))
        options.extend(windows)
    selected = []
    picked = set()
    covered = set()

    def rank(option):
        return (len(option[4] - covered), len(option[4]), option[5])

    def include(option, reserve_each_guide=False):
        guide, number, page, text, themes, _ = option
        excerpt = {"guide_id": guide["id"], "filename": guide["filename"], "pdf_page": page, "text": text}
        # Reserve space for every guide, including documents whose text or
        # filenames contain characters that JSON must escape.
        if reserve_each_guide:
            allowance = (MAX_BRIEF_CHARS - 2) // len(identifiers) - 2
            while len(text) > 1 and len(json.dumps(excerpt, ensure_ascii=False)) > allowance:
                text = text[:max(1, len(text) * 3 // 4)].rstrip()
                excerpt["text"] = text
            if not text or len(json.dumps(excerpt, ensure_ascii=False)) > allowance:
                raise KnowledgeError("A PDF filename is too large for the retained brief. Remove that guide, rename its PDF, and import it again.")
            retained_words = _terms(text)
            themes = {index for index, theme in enumerate(BRIEF_THEMES) if retained_words & theme}
        if len(selected) >= MAX_EXCERPTS or len(json.dumps(selected + [excerpt], ensure_ascii=False)) > MAX_BRIEF_CHARS:
            return False
        selected.append(excerpt)
        picked.add((guide["id"], number))
        covered.update(themes)
        return True

    # Each book contributes a foundation, then remaining space covers subjects
    # that the first selections did not cover (where those subjects exist).
    identifiers = list(dict.fromkeys(option[0]["id"] for option in options))
    for identifier in identifiers:
        include(max((option for option in options if option[0]["id"] == identifier), key=rank), reserve_each_guide=True)
    while len(selected) < MAX_EXCERPTS:
        remaining = [option for option in options if (option[0]["id"], option[1]) not in picked and option[4] - covered]
        if not remaining:
            break
        if not include(max(remaining, key=rank)):
            break
    return selected


def _valid_brief(brief: list, guides: list) -> bool:
    if not isinstance(brief, list) or not 1 <= len(brief) <= MAX_EXCERPTS or len(json.dumps(brief, ensure_ascii=False)) > MAX_BRIEF_CHARS:
        return False
    by_id = {guide["id"]: guide for guide in guides}
    represented = set()
    for excerpt in brief:
        if not isinstance(excerpt, dict) or set(excerpt) != {"guide_id", "filename", "pdf_page", "text"}:
            return False
        guide = by_id.get(excerpt["guide_id"])
        if guide is None or excerpt["filename"] != guide["filename"] or type(excerpt["pdf_page"]) is not int or not isinstance(excerpt["text"], str) or not 1 <= len(excerpt["text"]) <= BRIEF_PASSAGE_CHARS:
            return False
        if not any(passage["page"] == excerpt["pdf_page"] and excerpt["text"] in passage["text"] for passage in guide["passages"]):
            return False
        represented.add(guide["id"])
    return represented == set(by_id)


class Library:
    def __init__(self, root: str | Path | None = None):
        self.root = Path(root) if root is not None else Store().root / "library"

    def _sources(self) -> tuple[tuple, ...]:
        try:
            sources = []
            for path in sorted(self.root.glob("*.json")):
                info = path.stat()
                sources.append((path.name, info.st_size, info.st_mtime_ns, info.st_ctime_ns))
            return tuple(sources)
        except OSError:
            raise KnowledgeError("Cannot read the coaching library. Check its local JSON files and permissions.") from None

    def list_guides(self) -> list[dict]:
        try:
            guides = []
            for path in sorted(self.root.glob("*.json")):
                with path.open(encoding="utf-8") as handle:
                    guide = json.load(handle)
                _validate_guide(guide, path.stem)
                guides.append(guide)
        except (OSError, ValueError, TypeError):
            raise KnowledgeError("Cannot read the coaching library. Check its local JSON files and permissions.") from None
        if len(guides) > MAX_GUIDES:
            raise KnowledgeError("The coaching library supports six guides. Remove unused guides before coaching.")
        return guides

    def _prepared(self) -> dict:
        key = str(self.root.resolve())
        sources = self._sources()
        cached = _PREPARED.get(key)
        if cached is not None and cached["sources"] == sources:
            _PREPARED.move_to_end(key)
            return cached
        cached = self._read_cache(sources)
        if cached is None:
            guides = self.list_guides()
            candidates = []
            for guide in guides:
                for number, passage in enumerate(guide["passages"]):
                    candidates.append((guide, number, passage, _terms(passage["text"])))
            frequency = Counter(term for _, _, _, words in candidates for term in words)
            cached = {
                "sources": sources,
                "guides": guides,
                "candidates": candidates,
                "frequency": frequency,
                "brief": _make_brief(candidates, frequency),
            }
            if sources:
                self._write_cache(cached)
        _PREPARED[key] = cached
        _PREPARED.move_to_end(key)
        while len(_PREPARED) > _PREPARED_ROOTS:
            _PREPARED.popitem(last=False)
        return cached

    def prepare(self) -> dict:
        """Warm retained local text/index/brief without contacting either model."""
        prepared = self._prepared()
        return {
            "guides": len(prepared["guides"]),
            "pages": sum(guide["pages"] for guide in prepared["guides"]),
            "brief_passages": len(prepared["brief"]),
        }

    def brief(self) -> list[dict]:
        """Return the reusable extractive core, with verifiable source citations."""
        return deepcopy(self._prepared()["brief"])

    def _read_cache(self, sources: tuple[tuple, ...]) -> dict | None:
        if not sources:
            return None
        try:
            path = self.root / ".cache" / "prepared.json"
            if path.stat().st_size > MAX_CACHE_BYTES:
                return None
            with path.open(encoding="utf-8") as handle:
                data = json.load(handle)
            if data.get("version") != CACHE_VERSION or data.get("sources") != [list(source) for source in sources]:
                return None
            guides = data["guides"]
            words = data["words"]
            brief = data["brief"]
            if not isinstance(guides, list) or len(guides) != len(sources) or len(guides) > MAX_GUIDES:
                return None
            candidates = []
            for guide, source in zip(guides, sources):
                _validate_guide(guide, Path(source[0]).stem)
                group = words[guide["id"]]
                if not isinstance(group, list) or len(group) != len(guide["passages"]):
                    return None
                for number, (passage, terms) in enumerate(zip(guide["passages"], group)):
                    if not isinstance(terms, list) or len(terms) > PASSAGE_CHARS or any(not isinstance(term, str) or not re.fullmatch(r"[a-z0-9]{2,1600}", term) for term in terms):
                        return None
                    candidates.append((guide, number, passage, set(terms)))
            frequency = data["frequency"]
            if not isinstance(frequency, dict) or any(not isinstance(term, str) or type(count) is not int or not 1 <= count <= len(candidates) for term, count in frequency.items()):
                return None
            # Cached scores and citations must still agree with the retained text.
            actual_frequency = Counter(term for _, _, _, terms in candidates for term in terms)
            if frequency != actual_frequency or not _valid_brief(brief, guides):
                return None
            return {"sources": sources, "guides": guides, "candidates": candidates, "frequency": frequency, "brief": brief}
        except (OSError, ValueError, TypeError, KeyError, AttributeError):
            # An interrupted, obsolete, or damaged cache is disposable; the
            # original imported indexes remain the source of truth.
            return None

    def _write_cache(self, prepared: dict) -> None:
        destination = self.root / ".cache"
        temporary = None
        try:
            destination.mkdir(mode=0o700, parents=True, exist_ok=True)
            destination.chmod(0o700)
            words = {guide["id"]: [] for guide in prepared["guides"]}
            for guide, _, _, terms in prepared["candidates"]:
                words[guide["id"]].append(sorted(terms))
            data = {
                "version": CACHE_VERSION,
                "sources": prepared["sources"],
                "guides": prepared["guides"],
                "words": words,
                "frequency": prepared["frequency"],
                "brief": prepared["brief"],
            }
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=destination, suffix=".tmp", delete=False) as handle:
                temporary = Path(handle.name)
                json.dump(data, handle, ensure_ascii=False)
                handle.write("\n")
            os.replace(temporary, destination / "prepared.json")
        except OSError:
            raise KnowledgeError("Cannot prepare the coaching library. Check its local cache directory permissions.") from None
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def _discard_cache(self) -> None:
        _PREPARED.pop(str(self.root.resolve()), None)
        try:
            (self.root / ".cache" / "prepared.json").unlink(missing_ok=True)
        except OSError:
            # The source signature still prevents reuse if a derived cache
            # cannot be removed from a directory whose permissions changed.
            pass

    def add(self, value: str | Path) -> dict:
        path = document_path(value)
        if path.suffix.lower() != ".pdf":
            raise KnowledgeError("The methodology library accepts .pdf files. Use /file for weekly Word or text workouts.")
        try:
            with path.open("rb") as handle:
                data = handle.read(MAX_PDF_BYTES + 1)
        except OSError:
            raise KnowledgeError("Cannot open the PDF. Check its path and that it is available locally.") from None
        if len(data) > MAX_PDF_BYTES:
            raise KnowledgeError("PDF guides must be at most 25 MB. Import a smaller text-based copy.")
        identifier = hashlib.sha256(data).hexdigest()[:16]
        existing = self.list_guides()
        previous = next((guide for guide in existing if guide["id"] == identifier), None)
        if previous is not None and has_current_extraction(previous):
            return previous
        if previous is None and len(existing) >= MAX_GUIDES:
            raise KnowledgeError("The coaching library supports six guides. Remove an unused guide first.")
        try:
            reader = PdfReader(BytesIO(data), strict=False)
            if reader.is_encrypted:
                raise KnowledgeError("Password-protected PDFs are not supported. Save an unprotected copy.")
            page_count = len(reader.pages)
            if not 1 <= page_count <= MAX_PAGES:
                raise KnowledgeError("Import a PDF containing 1–600 pages.")
            passages = []
            total = 0
            empty_pages = 0
            for page_number, page in enumerate(reader.pages, 1):
                text = (page.extract_text() or "").strip()
                if not text:
                    empty_pages += 1
                total += len(text)
                if total > MAX_TEXT:
                    raise KnowledgeError("This PDF has too much extracted text. Split it into smaller guides.")
                passages.extend({"page": page_number, "text": chunk} for chunk in _chunks(text))
            if not passages:
                raise KnowledgeError("No readable text was found. Scanned/image-only PDFs need OCR before import.")
        except KnowledgeError:
            raise
        except (PdfReadError, ValueError, KeyError, TypeError, OSError, IndexError, RecursionError):
            raise KnowledgeError("Cannot read this PDF. Save a normal, unprotected, text-based PDF copy.") from None
        guide = {
            "id": identifier,
            "filename": path.name,
            "added_at": datetime.now(timezone.utc).isoformat(),
            "pages": page_count,
            "pages_without_text": empty_pages,
            "passages": passages,
            "extraction_version": PDF_EXTRACTION_VERSION,
        }
        temporary = None
        try:
            self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=self.root, suffix=".tmp", delete=False) as handle:
                temporary = Path(handle.name)
                json.dump(guide, handle, ensure_ascii=False)
                handle.write("\n")
            os.replace(temporary, self.root / f"{identifier}.json")
        except OSError:
            raise KnowledgeError("Cannot save the coaching library. Check its data directory permissions.") from None
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
        self._discard_cache()
        return guide

    def remove(self, identifier: str) -> None:
        if not re.fullmatch(r"[a-f0-9]{16}", identifier):
            raise KnowledgeError("Use a guide ID shown by library list.")
        try:
            (self.root / f"{identifier}.json").unlink()
        except OSError:
            raise KnowledgeError("Cannot remove that guide. Check its ID and local permissions.") from None
        self._discard_cache()

    def select(self, query: str) -> list[dict]:
        prepared = self._prepared()
        guides = prepared["guides"]
        if not guides:
            return []
        terms = _terms(query)
        if terms & {"aerobic", "endurance", "conditioning", "cardio"}:
            terms.update(("aerobic", "endurance", "conditioning", "cardiovascular"))
        if terms & {"competition", "competitor", "competitors", "quarterfinals"}:
            terms.update(("competition", "competitors", "taper", "periodization", "specificity"))
        foundation = _FOUNDATION_TERMS
        candidates = prepared["candidates"]
        frequency = prepared["frequency"]
        def score(words, targets):
            return sum(math.log(1 + len(candidates) / frequency[term]) for term in words & targets)
        selected = []
        picked = set()
        def include(candidate):
            guide, number, passage, _ = candidate
            key = (guide["id"], number)
            if key in picked or len(selected) >= MAX_EXCERPTS:
                return
            excerpt = {"guide_id": guide["id"], "filename": guide["filename"], "pdf_page": passage["page"], "text": passage["text"]}
            if len(json.dumps(selected + [excerpt], ensure_ascii=False)) <= MAX_CONTEXT_CHARS:
                selected.append(excerpt)
                picked.add(key)
        # Keep every guide represented, even when the question uses different terms.
        for guide in guides:
            group = [candidate for candidate in candidates if candidate[0]["id"] == guide["id"]]
            include(max(group, key=lambda candidate: score(candidate[3], foundation)))
        page_match = re.search(r"\bpage\s+(\d+)\b", query, re.IGNORECASE)
        requested_page = int(page_match[1]) if page_match else None
        ranked = sorted(candidates, key=lambda candidate: (candidate[2]["page"] == requested_page, score(candidate[3], terms)), reverse=True)
        for candidate in ranked:
            if score(candidate[3], terms) > 0 or candidate[2]["page"] == requested_page:
                include(candidate)
        return deepcopy(selected)
