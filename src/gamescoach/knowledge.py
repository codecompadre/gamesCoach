"""A private, persistent PDF library with bounded, page-cited text retrieval."""

from collections import Counter
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
STOP_WORDS = set("a an and are as at be by can do for from have how i in is it me my of on or that the their this to we what when with you your crossfit".split())
FOUNDATION = "methodology constantly varied functional movement intensity mechanics consistency fitness programming aerobic strength competition recovery"


class KnowledgeError(ValueError):
    """A library error that never includes source document contents."""


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


class Library:
    def __init__(self, root: str | Path | None = None):
        self.root = Path(root) if root is not None else Store().root / "library"

    def list_guides(self) -> list[dict]:
        guides = []
        try:
            for path in sorted(self.root.glob("*.json")):
                with path.open(encoding="utf-8") as handle:
                    guide = json.load(handle)
                if not isinstance(guide, dict):
                    raise ValueError
                if not re.fullmatch(r"[a-f0-9]{16}", guide.get("id", "")) or path.stem != guide["id"]:
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
                guides.append(guide)
        except (OSError, ValueError, TypeError):
            raise KnowledgeError("Cannot read the coaching library. Check its local JSON files and permissions.") from None
        if len(guides) > MAX_GUIDES:
            raise KnowledgeError("The coaching library supports six guides. Remove unused guides before coaching.")
        return guides

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
        for guide in existing:
            if guide["id"] == identifier:
                return guide
        if len(existing) >= MAX_GUIDES:
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
        return guide

    def remove(self, identifier: str) -> None:
        if not re.fullmatch(r"[a-f0-9]{16}", identifier):
            raise KnowledgeError("Use a guide ID shown by library list.")
        try:
            (self.root / f"{identifier}.json").unlink()
        except OSError:
            raise KnowledgeError("Cannot remove that guide. Check its ID and local permissions.") from None

    def select(self, query: str) -> list[dict]:
        guides = self.list_guides()
        if not guides:
            return []
        terms = _terms(query)
        if terms & {"aerobic", "endurance", "conditioning", "cardio"}:
            terms.update(("aerobic", "endurance", "conditioning", "cardiovascular"))
        if terms & {"competition", "competitor", "competitors", "quarterfinals"}:
            terms.update(("competition", "competitors", "taper", "periodization", "specificity"))
        foundation = _terms(FOUNDATION)
        candidates = []
        for guide in guides:
            for number, passage in enumerate(guide["passages"]):
                candidates.append((guide, number, passage, _terms(passage["text"])))
        frequency = Counter(term for _, _, _, words in candidates for term in words)
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
        return selected
