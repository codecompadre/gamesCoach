"""Exercise embedded CFF text extraction without private or downloaded PDFs."""

from fontTools.fontBuilder import FontBuilder
from fontTools.pens.t2CharStringPen import T2CharStringPen
from pypdf import PdfWriter
from pypdf.generic import (
    ArrayObject,
    DecodedStreamObject,
    DictionaryObject,
    NameObject,
    NumberObject,
)

from gamescoach.knowledge import Library


def test_library_reads_embedded_cff_encoding_without_missing_fonttools_warning(tmp_path, caplog):
    # Encode the B glyph at character code A. WinAnsi fallback would produce A,
    # so obtaining B proves that pypdf actually decoded the embedded CFF font.
    font = FontBuilder(1000, isTTF=False)
    font.setupGlyphOrder([".notdef", "B"])
    pen = T2CharStringPen(600, None)
    font.setupCFF(
        "RegressionCFF",
        {"FullName": "Regression CFF", "FamilyName": "Regression", "Weight": "Regular"},
        {name: pen.getCharString() for name in (".notdef", "B")},
        {},
    )
    encoding = [".notdef"] * 256
    encoding[65] = "B"
    font.font["CFF "].cff.topDictIndex[0].Encoding = encoding

    writer = PdfWriter()
    page = writer.add_blank_page(width=612, height=792)
    embedded = DecodedStreamObject()
    embedded.set_data(font.font["CFF "].compile(font.font))
    embedded[NameObject("/Subtype")] = NameObject("/Type1C")
    descriptor = DictionaryObject({
        NameObject("/Type"): NameObject("/FontDescriptor"),
        NameObject("/FontName"): NameObject("/RegressionCFF"),
        NameObject("/FontFile3"): writer._add_object(embedded),
    })
    pdf_font = DictionaryObject({
        NameObject("/Type"): NameObject("/Font"),
        NameObject("/Subtype"): NameObject("/Type1"),
        NameObject("/BaseFont"): NameObject("/RegressionCFF"),
        NameObject("/Encoding"): NameObject("/WinAnsiEncoding"),
        NameObject("/FontDescriptor"): writer._add_object(descriptor),
        NameObject("/FirstChar"): NumberObject(65),
        NameObject("/LastChar"): NumberObject(65),
        NameObject("/Widths"): ArrayObject([NumberObject(600)]),
    })
    page[NameObject("/Resources")] = DictionaryObject({
        NameObject("/Font"): DictionaryObject({NameObject("/F1"): writer._add_object(pdf_font)}),
    })
    content = DecodedStreamObject()
    content.set_data(b"BT /F1 12 Tf 50 700 Td (A) Tj ET")
    page[NameObject("/Contents")] = writer._add_object(content)
    source = tmp_path / "Embedded CFF.pdf"
    writer.write(source)

    guide = Library(tmp_path / "library").add(source)

    assert guide["passages"] == [{"page": 1, "text": "B"}]
    assert not any("fontTools is required" in record.getMessage() for record in caplog.records)
