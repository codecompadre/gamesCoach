"""Read local training documents without uploading their original files."""

from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
import re
from uuid import uuid4
import zipfile

from docx import Document
from docx.oxml.exceptions import InvalidXmlError
from docx.opc.exceptions import OpcError
from docx.table import Table
from lxml.etree import XMLSyntaxError

from .models import AthleteProfile, CoachTurn, CoachingSession

MAX_FILE_BYTES = 10 * 1024 * 1024
MAX_EXPANDED_BYTES = 50 * 1024 * 1024
MAX_TEXT_CHARS = 60000


class DocumentError(ValueError):
    """A readable document import error that never includes file contents."""


def document_path(value: str | Path) -> Path:
    """Accept quoted paths and Windows Explorer's Copy as path in Ubuntu/WSL."""
    text = str(value).strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in ('"', "'"):
        text = text[1:-1]
    if not text:
        raise DocumentError("Enter the path to a .docx, .txt or .md training file.")
    windows = re.fullmatch(r"([A-Za-z]):[\\/](.*)", text)
    if windows:
        text = f"/mnt/{windows[1].lower()}/{windows[2].replace(chr(92), '/')}"
    return Path(text).expanduser()


def _blocks(container, depth: int = 0) -> list[str]:
    if depth > 10:
        raise DocumentError("The Word document has excessively nested tables. Simplify it before importing.")
    result = []
    for block in container.iter_inner_content():
        if isinstance(block, Table):
            rows = []
            for number, row in enumerate(block.rows, 1):
                values = []
                previous = None
                for cell in row.cells:
                    # Merged horizontal cells otherwise repeat the same workout text.
                    if cell._tc is previous:
                        values.append("")
                    else:
                        values.append(" / ".join(_blocks(cell, depth + 1)))
                    previous = cell._tc
                if any(values):
                    rows.append(f"Row {number}: " + " | ".join(values))
            if rows:
                result.append("Table:\n" + "\n".join(rows))
        elif block.text.strip():
            result.append(block.text.strip())
    return result


def _word_text(data: bytes) -> str:
    try:
        with zipfile.ZipFile(BytesIO(data)) as archive:
            entries = archive.infolist()
            if len(entries) > 2000 or sum(entry.file_size for entry in entries) > MAX_EXPANDED_BYTES:
                raise DocumentError("The Word file is too large when expanded. Import a smaller training document.")
            if any(entry.flag_bits & 1 for entry in entries):
                raise DocumentError("Password-protected Word files are not supported. Save an unprotected .docx copy.")
            if sum(entry.filename == "word/document.xml" for entry in entries) != 1:
                raise DocumentError("This file is not a valid Word .docx document.")
        return "\n\n".join(_blocks(Document(BytesIO(data))))
    except DocumentError:
        raise
    except (zipfile.BadZipFile, KeyError, ValueError, OSError, InvalidXmlError, OpcError, XMLSyntaxError):
        raise DocumentError("Cannot read this Word file. Open it in Word and save a normal, unprotected .docx copy.") from None


def read_document(value: str | Path) -> tuple[str, str]:
    path = document_path(value)
    suffix = path.suffix.lower()
    if suffix not in (".docx", ".txt", ".md"):
        raise DocumentError("Supported files are .docx, .txt and .md. For an older .doc file, use Word's Save As to create a .docx copy.")
    try:
        with path.open("rb") as handle:
            data = handle.read(MAX_FILE_BYTES + 1)
    except OSError:
        raise DocumentError("Cannot open the training file. Check its path and read permissions. Windows files in Ubuntu are normally under /mnt/c/.") from None
    if len(data) > MAX_FILE_BYTES:
        raise DocumentError("Training files must be at most 10 MB. Import only the week's relevant training material.")
    if suffix == ".docx":
        text = _word_text(data)
    else:
        try:
            encoding = "utf-16" if data.startswith((b"\xff\xfe", b"\xfe\xff")) else "utf-8-sig"
            text = data.decode(encoding).strip()
        except UnicodeError:
            raise DocumentError("Save the text file using UTF-8 encoding, then import it again.") from None
    if not text.strip():
        raise DocumentError("No readable workout text was found. Images and scanned pages are not read; add text or workout tables to the document.")
    if "\x00" in text:
        raise DocumentError("The file contains binary data. Save a plain-text or Word .docx copy.")
    if len(text) > MAX_TEXT_CHARS:
        raise DocumentError("The extracted text exceeds 60000 characters. Split it into individual training weeks.")
    return path.name, text


def import_document(value: str | Path, profile: AthleteProfile) -> CoachingSession:
    filename, text = read_document(value)
    return CoachingSession(
        id=uuid4().hex[:12],
        created_at=datetime.now(timezone.utc).isoformat(),
        kind="document",
        request=f"Imported training file: {filename}",
        profile=profile,
        turns=[CoachTurn("Local file", "import", text)],
        final_plan=text,
    )
