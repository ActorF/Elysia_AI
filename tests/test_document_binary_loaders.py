"""Verify bounded, path-free PDF and DOCX binary document loading."""

from __future__ import annotations

from dataclasses import replace
from hashlib import sha256
from io import BytesIO
from struct import pack, pack_into
import warnings
from xml.etree import ElementTree
from zipfile import ZIP_DEFLATED, ZIP_STORED, ZipFile

import pytest
from pypdf import PdfWriter
from pypdf._page import PageObject
from pypdf.errors import DependencyError, LimitReachedError, PdfReadError
from pypdf.generic import (
    ArrayObject,
    DecodedStreamObject,
    DictionaryObject,
    NameObject,
    NullObject,
    NumberObject,
)

from attachments.domain import AttachmentScope
import documents.docx as docx_module
import documents.pdf as pdf_module
from documents.docx import DocxDocumentLoader
from documents.domain import (
    MAX_DOCUMENT_TITLE_CODE_POINTS,
    DocumentLoadLimits,
    DocumentSource,
)
from documents.exceptions import (
    DocumentContentLimitError,
    DocumentCorruptError,
    DocumentEmptyError,
    DocumentEncryptedError,
    DocumentLoadFailedError,
    DocumentReadError,
    DocumentTooLargeError,
    DocumentUnsupportedFeatureError,
    DocumentUnsupportedFormatError,
)
from documents.pdf import PdfDocumentLoader


_DOCX_MEDIA_TYPE = (
    "application/vnd.openxmlformats-officedocument."
    "wordprocessingml.document"
)
_CONTENT_TYPES_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
</Types>
"""
_PACKAGE_RELATIONSHIPS_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
</Relationships>
"""
_EMPTY_DOCUMENT_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:body><w:p><w:r><w:t>   </w:t></w:r></w:p><w:sectPr/></w:body>
</w:document>
"""


def _source(
    data: bytes,
    *,
    file_name: str,
    media_type: str,
) -> DocumentSource:
    """Build path-free metadata whose content ID matches one byte fixture."""

    digest = sha256(data).hexdigest()
    return DocumentSource(
        scope=AttachmentScope(kind="chat", id="chat_binary_loader"),
        link_id="attachment_binary_loader",
        file_id=f"file_{digest}",
        file_name=file_name,
        media_type=media_type,
        size_bytes=len(data),
    )


def _pdf_bytes(
    page_texts: tuple[str | None, ...],
    *,
    title: str | None = None,
    password: str | None = None,
) -> bytes:
    """Generate a small standards-conforming PDF without private test files."""

    writer = PdfWriter()
    font = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
        }
    )
    font_reference = writer._add_object(font)
    for page_text in page_texts:
        page = writer.add_blank_page(width=612, height=792)
        if page_text is None:
            continue
        escaped = (
            page_text.replace("\\", "\\\\")
            .replace("(", "\\(")
            .replace(")", "\\)")
        )
        page[NameObject("/Resources")] = DictionaryObject(
            {
                NameObject("/Font"): DictionaryObject(
                    {NameObject("/F1"): font_reference}
                )
            }
        )
        content = DecodedStreamObject()
        content.set_data(
            f"BT /F1 12 Tf 72 720 Td ({escaped}) Tj ET".encode("ascii")
        )
        page[NameObject("/Contents")] = writer._add_object(content)
    if title is not None:
        writer.add_metadata({"/Title": title})
    if password is not None:
        # RC4 keeps the fixture independent of optional AES dependencies while
        # still exercising the loader's all-encryption rejection policy.
        writer.encrypt(password, algorithm="RC4-40")
    output = BytesIO()
    writer.write(output)
    return output.getvalue()


def _pdf_text_operations(text: str) -> bytes:
    """Encode one Helvetica text run for an in-memory content stream."""

    escaped = (
        text.replace("\\", "\\\\")
        .replace("(", "\\(")
        .replace(")", "\\)")
    )
    return f"BT /F1 12 Tf 72 720 Td ({escaped}) Tj ET".encode("ascii")


def _pdf_with_forms_bytes(
    *,
    page_content: bytes,
    form_contents: tuple[bytes, ...],
    page_forms: tuple[tuple[str, int], ...],
    nested_forms: tuple[tuple[tuple[str, int], ...], ...] | None = None,
) -> bytes:
    """Build one page whose Form graph can repeat, nest, or cycle."""

    writer = PdfWriter()
    font = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
        }
    )
    font_reference = writer._add_object(font)
    forms: list[DecodedStreamObject] = []
    form_references = []
    for form_content in form_contents:
        form = DecodedStreamObject()
        form.set_data(form_content)
        form[NameObject("/Type")] = NameObject("/XObject")
        form[NameObject("/Subtype")] = NameObject("/Form")
        form[NameObject("/BBox")] = ArrayObject(
            [NumberObject(0), NumberObject(0), NumberObject(100), NumberObject(100)]
        )
        forms.append(form)
        form_references.append(writer._add_object(form))

    links_by_form = nested_forms or tuple(() for _ in forms)
    for form, links in zip(forms, links_by_form, strict=True):
        resources = DictionaryObject(
            {
                NameObject("/Font"): DictionaryObject(
                    {NameObject("/F1"): font_reference}
                )
            }
        )
        if links:
            resources[NameObject("/XObject")] = DictionaryObject(
                {
                    NameObject(name): form_references[index]
                    for name, index in links
                }
            )
        form[NameObject("/Resources")] = resources

    page = writer.add_blank_page(width=100, height=100)
    page_resources = DictionaryObject(
        {
            NameObject("/Font"): DictionaryObject(
                {NameObject("/F1"): font_reference}
            )
        }
    )
    if page_forms:
        page_resources[NameObject("/XObject")] = DictionaryObject(
            {
                NameObject(name): form_references[index]
                for name, index in page_forms
            }
        )
    page[NameObject("/Resources")] = page_resources
    page_stream = DecodedStreamObject()
    page_stream.set_data(page_content)
    page[NameObject("/Contents")] = writer._add_object(page_stream)
    output = BytesIO()
    writer.write(output)
    return output.getvalue()


def _pdf_with_compressed_font_maps(
    *,
    font_count: int,
    map_size: int,
) -> bytes:
    """Build one page whose declared fonts carry compressed Unicode maps."""

    writer = PdfWriter()
    fonts = DictionaryObject()
    payload = b" " * map_size
    for index in range(font_count):
        character_map = DecodedStreamObject()
        character_map.set_data(payload)
        character_map_reference = writer._add_object(
            character_map.flate_encode()
        )
        font = DictionaryObject(
            {
                NameObject("/Type"): NameObject("/Font"),
                NameObject("/Subtype"): NameObject("/Type1"),
                NameObject("/BaseFont"): NameObject("/Helvetica"),
                NameObject("/ToUnicode"): character_map_reference,
            }
        )
        fonts[NameObject(f"/F{index}")] = writer._add_object(font)

    page = writer.add_blank_page(width=100, height=100)
    page[NameObject("/Resources")] = DictionaryObject(
        {NameObject("/Font"): fonts}
    )
    content = DecodedStreamObject()
    content.set_data(_pdf_text_operations("x"))
    page[NameObject("/Contents")] = writer._add_object(content)
    output = BytesIO()
    writer.write(output)
    return output.getvalue()


def _pdf_with_custom_font_map(
    character_map_data: bytes,
    page_content: bytes,
) -> bytes:
    """Build one page using a caller-defined ToUnicode map and operations."""

    writer = PdfWriter()
    character_map = DecodedStreamObject()
    character_map.set_data(character_map_data)
    font = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
            NameObject("/ToUnicode"): writer._add_object(character_map),
        }
    )
    page = writer.add_blank_page(width=100, height=100)
    page[NameObject("/Resources")] = DictionaryObject(
        {
            NameObject("/Font"): DictionaryObject(
                {NameObject("/F1"): writer._add_object(font)}
            )
        }
    )
    content = DecodedStreamObject()
    content.set_data(page_content)
    page[NameObject("/Contents")] = writer._add_object(content)
    output = BytesIO()
    writer.write(output)
    return output.getvalue()


def _pdf_with_difference_font(
    glyph_name: str,
    *,
    inside_form: bool,
) -> bytes:
    """Build a page or Form using one custom encoding-difference name."""

    writer = PdfWriter()
    encoding = DictionaryObject(
        {
            NameObject("/BaseEncoding"): NameObject("/WinAnsiEncoding"),
            NameObject("/Differences"): ArrayObject(
                [NumberObject(127), NameObject(glyph_name)]
            ),
        }
    )
    font = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
            NameObject("/Encoding"): encoding,
        }
    )
    font_reference = writer._add_object(font)
    text_content = b"BT /F1 12 Tf <7f> Tj ET"
    page = writer.add_blank_page(width=100, height=100)
    if not inside_form:
        page[NameObject("/Resources")] = DictionaryObject(
            {
                NameObject("/Font"): DictionaryObject(
                    {NameObject("/F1"): font_reference}
                )
            }
        )
        content = DecodedStreamObject()
        content.set_data(text_content)
        page[NameObject("/Contents")] = writer._add_object(content)
    else:
        form = DecodedStreamObject()
        form.set_data(text_content)
        form[NameObject("/Type")] = NameObject("/XObject")
        form[NameObject("/Subtype")] = NameObject("/Form")
        form[NameObject("/BBox")] = ArrayObject(
            [NumberObject(0), NumberObject(0), NumberObject(100), NumberObject(100)]
        )
        form[NameObject("/Resources")] = DictionaryObject(
            {
                NameObject("/Font"): DictionaryObject(
                    {NameObject("/F1"): font_reference}
                )
            }
        )
        page[NameObject("/Resources")] = DictionaryObject(
            {
                NameObject("/XObject"): DictionaryObject(
                    {NameObject("/Fm1"): writer._add_object(form)}
                )
            }
        )
        content = DecodedStreamObject()
        content.set_data(b"/Fm1 Do")
        page[NameObject("/Contents")] = writer._add_object(content)

    output = BytesIO()
    writer.write(output)
    return output.getvalue()


def _docx_bytes(
    document_xml: bytes,
    *,
    relationships_xml: bytes = _PACKAGE_RELATIONSHIPS_XML,
    content_types_xml: bytes = _CONTENT_TYPES_XML,
    main_relationships_xml: bytes | None = None,
    styles_xml: bytes | None = None,
    core_xml: bytes | None = None,
    extra_members: tuple[tuple[str, bytes], ...] = (),
    compression: int = ZIP_DEFLATED,
) -> bytes:
    """Build one deterministic in-memory OPC package for loader tests."""

    package_relationships = relationships_xml
    selected_content_types = content_types_xml
    if core_xml is not None:
        package_relationships = package_relationships.replace(
            b"</Relationships>",
            b'<Relationship Id="rIdCore" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/></Relationships>',
        )
        selected_content_types = selected_content_types.replace(
            b"</Types>",
            b'<Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/></Types>',
        )
    selected_main_relationships = main_relationships_xml
    if styles_xml is not None and selected_main_relationships is None:
        selected_main_relationships = b"""<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rIdStyles" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/></Relationships>"""
    if styles_xml is not None:
        selected_content_types = selected_content_types.replace(
            b"</Types>",
            b'<Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/></Types>',
        )

    output = BytesIO()
    with ZipFile(output, mode="w", compression=compression) as archive:
        archive.writestr("[Content_Types].xml", selected_content_types)
        archive.writestr("_rels/.rels", package_relationships)
        archive.writestr("word/document.xml", document_xml)
        if selected_main_relationships is not None:
            archive.writestr(
                "word/_rels/document.xml.rels",
                selected_main_relationships,
            )
        if styles_xml is not None:
            archive.writestr("word/styles.xml", styles_xml)
        if core_xml is not None:
            archive.writestr("docProps/core.xml", core_xml)
        for name, payload in extra_members:
            archive.writestr(name, payload)
    result = output.getvalue()
    # ``zipfile`` normalizes a backslash while authoring on Windows.  Rewrite
    # the equal-length local and central name fields after close so the fixture
    # represents the hostile raw archive spelling the loader must inspect.
    for name, _payload in extra_members:
        if "\\" in name:
            canonical = name.replace("\\", "/").encode("utf-8")
            result = result.replace(canonical, name.encode("utf-8"))
    return result


def _rewrite_eocd_entry_count(data: bytes, entry_count: int) -> bytes:
    """Forge both legacy EOCD counts while preserving the real records."""

    result = bytearray(data)
    eocd_offset = result.rfind(b"PK\x05\x06")
    assert eocd_offset >= 0
    pack_into("<HH", result, eocd_offset + 8, entry_count, entry_count)
    return bytes(result)


def _zip64_declared_count_bytes(entry_count: int) -> bytes:
    """Build bounded Zip64 trailer metadata with no materialized entries."""

    prefix = b"PK\x03\x04"
    zip64_offset = len(prefix)
    zip64_end = pack(
        "<4sQ2H2L4Q",
        b"PK\x06\x06",
        44,
        45,
        45,
        0,
        0,
        entry_count,
        entry_count,
        0,
        len(prefix),
    )
    locator = pack("<4sLQL", b"PK\x06\x07", 0, zip64_offset, 1)
    legacy_end = pack(
        "<4s4H2LH",
        b"PK\x05\x06",
        0,
        0,
        0xFFFF,
        0xFFFF,
        0xFFFFFFFF,
        0xFFFFFFFF,
        0,
    )
    return prefix + zip64_end + locator + legacy_end


def test_pdf_loader_preserves_title_page_numbers_and_empty_pages() -> None:
    """PDF extraction keeps real page locations without inventing blank blocks."""

    data = _pdf_bytes(("First page", None, "Third page"), title="Guide")
    loaded = PdfDocumentLoader().load(
        _source(data, file_name="guide.pdf", media_type="application/pdf"),
        data,
        DocumentLoadLimits(),
    )

    assert loaded.document_format == "pdf"
    assert loaded.page_count == 3
    assert loaded.title is not None
    assert loaded.title.text == "Guide"
    assert loaded.title.source == "embedded"
    assert [block.ordinal for block in loaded.blocks] == [0, 1]
    assert [block.page_number for block in loaded.blocks] == [1, 3]
    assert [block.kind for block in loaded.blocks] == ["paragraph", "paragraph"]
    assert "First page" in (loaded.blocks[0].text or "")
    assert "Third page" in (loaded.blocks[1].text or "")


def test_pdf_loader_rejects_every_encrypted_document() -> None:
    """A password-bearing PDF fails before any page text can be disclosed."""

    data = _pdf_bytes(("Secret",), password="password")

    with pytest.raises(DocumentEncryptedError) as captured:
        PdfDocumentLoader().load(
            _source(
                data,
                file_name="private-course.pdf",
                media_type="application/pdf",
            ),
            data,
            DocumentLoadLimits(),
        )

    assert "private-course.pdf" not in str(captured.value)


def test_pdf_loader_rejects_strictly_corrupt_input_without_path_leak() -> None:
    """Malformed PDF bytes map to one stable error without the display name."""

    data = b"%PDF-this is not a complete PDF"

    with pytest.raises(DocumentCorruptError) as captured:
        PdfDocumentLoader().load(
            _source(
                data,
                file_name="sensitive-name.pdf",
                media_type="application/pdf",
            ),
            data,
            DocumentLoadLimits(),
        )

    assert "sensitive-name.pdf" not in str(captured.value)


def test_pdf_loader_rejects_empty_or_image_only_documents() -> None:
    """A valid PDF with no extractable text reports the explicit empty result."""

    data = _pdf_bytes((None,))

    with pytest.raises(DocumentEmptyError):
        PdfDocumentLoader().load(
            _source(data, file_name="scan.pdf", media_type="application/pdf"),
            data,
            DocumentLoadLimits(),
        )


def test_pdf_loader_enforces_page_and_text_budgets() -> None:
    """Page traversal and published Unicode cannot exceed caller budgets."""

    data = _pdf_bytes(("Hello", "Again"))
    source = _source(data, file_name="bounded.pdf", media_type="application/pdf")

    with pytest.raises(DocumentContentLimitError):
        PdfDocumentLoader().load(
            source,
            data,
            replace(DocumentLoadLimits(), max_pages=1),
        )
    with pytest.raises(DocumentContentLimitError):
        PdfDocumentLoader().load(
            source,
            data,
            replace(
                DocumentLoadLimits(),
                max_text_code_points=4,
                max_cell_code_points=4,
            ),
        )


def test_pdf_loader_enforces_source_size_and_snapshot_identity() -> None:
    """Input admission distinguishes configured size from byte-identity failure."""

    data = _pdf_bytes(("Bounded",))
    source = _source(data, file_name="bounded.pdf", media_type="application/pdf")

    with pytest.raises(DocumentTooLargeError):
        PdfDocumentLoader().load(
            source,
            data,
            replace(DocumentLoadLimits(), max_source_bytes=len(data) - 1),
        )
    with pytest.raises(DocumentReadError):
        PdfDocumentLoader().load(source, data + b"x", DocumentLoadLimits())


def test_pdf_loader_revalidates_its_exact_route() -> None:
    """Direct adapter calls allow casefolded suffixes but not route mismatch."""

    data = _pdf_bytes(("Routed",))
    loaded = PdfDocumentLoader().load(
        _source(data, file_name="GUIDE.PDF", media_type="application/pdf"),
        data,
        DocumentLoadLimits(),
    )

    assert loaded.blocks[0].text is not None
    with pytest.raises(DocumentUnsupportedFormatError):
        PdfDocumentLoader().load(
            _source(data, file_name="guide.txt", media_type="application/pdf"),
            data,
            DocumentLoadLimits(),
        )
    with pytest.raises(DocumentUnsupportedFormatError):
        PdfDocumentLoader().load(
            _source(data, file_name="guide.pdf", media_type="text/plain"),
            data,
            DocumentLoadLimits(),
        )


def test_pdf_loader_counts_form_bytes_per_actual_invocation() -> None:
    """Repeated Form references consume expansion budget every time they run."""

    form_content = _pdf_text_operations("FORM")
    page_content = b"/Fm1 Do /Fm1 Do /Fm1 Do"
    data = _pdf_with_forms_bytes(
        page_content=page_content,
        form_contents=(form_content,),
        page_forms=(("/Fm1", 0),),
    )
    source = _source(data, file_name="forms.pdf", media_type="application/pdf")
    exact_expansion = len(page_content) + (3 * len(form_content))

    loaded = PdfDocumentLoader().load(
        source,
        data,
        replace(DocumentLoadLimits(), max_expanded_bytes=exact_expansion),
    )

    assert "FORM" in (loaded.blocks[0].text or "")
    with pytest.raises(DocumentContentLimitError):
        PdfDocumentLoader().load(
            source,
            data,
            replace(
                DocumentLoadLimits(),
                max_expanded_bytes=exact_expansion - 1,
            ),
        )


def test_pdf_loader_counts_repeated_form_operation_work() -> None:
    """A small Form invoked repeatedly cannot bypass the traversal budget."""

    form_content = _pdf_text_operations("X")
    page_content = b"/Fm1 Do /Fm1 Do /Fm1 Do /Fm1 Do"
    data = _pdf_with_forms_bytes(
        page_content=page_content,
        form_contents=(form_content,),
        page_forms=(("/Fm1", 0),),
    )

    with pytest.raises(DocumentContentLimitError):
        PdfDocumentLoader().load(
            _source(data, file_name="work.pdf", media_type="application/pdf"),
            data,
            replace(DocumentLoadLimits(), max_xml_elements=23),
        )


def test_pdf_loader_rejects_form_before_partial_page_success() -> None:
    """An oversized Form makes the whole load fail despite earlier safe text."""

    page_content = _pdf_text_operations("SAFE") + b" /Fm1 Do"
    form_content = _pdf_text_operations("X" * 200)
    data = _pdf_with_forms_bytes(
        page_content=page_content,
        form_contents=(form_content,),
        page_forms=(("/Fm1", 0),),
    )

    with pytest.raises(DocumentContentLimitError):
        PdfDocumentLoader().load(
            _source(data, file_name="partial.pdf", media_type="application/pdf"),
            data,
            replace(
                DocumentLoadLimits(),
                max_expanded_bytes=len(form_content) - 1,
            ),
        )


def test_pdf_loader_rejects_nested_and_cyclic_forms() -> None:
    """Form depth and active-reference cycles both fail closed."""

    nested_data = _pdf_with_forms_bytes(
        page_content=b"/Outer Do",
        form_contents=(_pdf_text_operations("INNER"), b"/Inner Do"),
        page_forms=(("/Outer", 1),),
        nested_forms=((), (("/Inner", 0),)),
    )
    with pytest.raises(DocumentContentLimitError):
        PdfDocumentLoader().load(
            _source(
                nested_data,
                file_name="nested.pdf",
                media_type="application/pdf",
            ),
            nested_data,
            replace(DocumentLoadLimits(), max_xml_depth=1),
        )

    cyclic_data = _pdf_with_forms_bytes(
        page_content=b"/Cycle Do",
        form_contents=((b"/Self Do"),),
        page_forms=(("/Cycle", 0),),
        nested_forms=((("/Self", 0),),),
    )
    with pytest.raises(DocumentContentLimitError):
        PdfDocumentLoader().load(
            _source(
                cyclic_data,
                file_name="cycle.pdf",
                media_type="application/pdf",
            ),
            cyclic_data,
            DocumentLoadLimits(),
        )


def test_pdf_loader_visitor_guard_cannot_be_swallowed_by_form() -> None:
    """Oversized Form text cannot degrade into a partial ``SAFE`` result."""

    page_content = _pdf_text_operations("SAFE") + b" /Fm1 Do"
    data = _pdf_with_forms_bytes(
        page_content=page_content,
        form_contents=(_pdf_text_operations("TOO-LONG-FORM-TEXT"),),
        page_forms=(("/Fm1", 0),),
    )

    with pytest.raises(DocumentContentLimitError):
        PdfDocumentLoader().load(
            _source(data, file_name="visitor.pdf", media_type="application/pdf"),
            data,
            replace(
                DocumentLoadLimits(),
                max_text_code_points=8,
                max_cell_code_points=8,
            ),
        )


@pytest.mark.parametrize(
    ("native_error", "expected_error"),
    (
        (LimitReachedError, DocumentContentLimitError),
        (DependencyError, DocumentUnsupportedFeatureError),
        (PdfReadError, DocumentCorruptError),
        (RuntimeError, DocumentLoadFailedError),
    ),
)
def test_pdf_loader_promotes_nested_form_failures_without_partial_success(
    monkeypatch: pytest.MonkeyPatch,
    native_error: type[Exception],
    expected_error: type[Exception],
) -> None:
    """A nested Form failure cannot be logged and reduced to earlier text."""

    data = _pdf_with_forms_bytes(
        page_content=_pdf_text_operations("SAFE") + b" /Outer Do",
        form_contents=(_pdf_text_operations("INNER"), b"/Inner Do"),
        page_forms=(("/Outer", 1),),
        nested_forms=((), (("/Inner", 0),)),
    )
    original_dispatch = PageObject._extract_text__xform

    def fail_inner_form(
        page: PageObject,
        *args: object,
        **kwargs: object,
    ) -> str | None:
        """Inject a native failure only after the outer Form starts."""

        operands = kwargs.get("operands")
        if (
            isinstance(operands, list)
            and operands
            and str(operands[0]) == "/Inner"
        ):
            raise native_error("private nested parser detail")
        return original_dispatch(page, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(PageObject, "_extract_text__xform", fail_inner_form)
    with pytest.raises(expected_error) as captured:
        PdfDocumentLoader().load(
            _source(data, file_name="nested-error.pdf", media_type="application/pdf"),
            data,
            DocumentLoadLimits(),
        )

    assert "private nested parser detail" not in str(captured.value)
    assert "SAFE" not in str(captured.value)


def test_pdf_loader_stops_repeated_form_text_before_page_materialization(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Repeated small Form callbacks exhaust their cumulative page allowance."""

    form_content = _pdf_text_operations("X" * 60)
    data = _pdf_with_forms_bytes(
        page_content=b" ".join([b"/Fm1 Do"] * 1_000),
        form_contents=(form_content,),
        page_forms=(("/Fm1", 0),),
    )
    callback_count = 0
    original_visit = pdf_module._PdfTextGuard.visit

    def count_visit(
        guard: object,
        text: object,
        *args: object,
    ) -> None:
        """Count callbacks while preserving the production budget behavior."""

        nonlocal callback_count
        if isinstance(text, str) and text:
            callback_count += 1
        original_visit(guard, text, *args)  # type: ignore[arg-type]

    monkeypatch.setattr(pdf_module._PdfTextGuard, "visit", count_visit)
    with pytest.raises(DocumentContentLimitError):
        PdfDocumentLoader().load(
            _source(data, file_name="repeated.pdf", media_type="application/pdf"),
            data,
            replace(
                DocumentLoadLimits(),
                max_text_code_points=100,
                max_cell_code_points=100,
            ),
        )

    assert callback_count < 10


@pytest.mark.parametrize(
    "data",
    (
        _pdf_with_forms_bytes(
            page_content=b"/Fm1 Do /Fm1 Do",
            form_contents=(_pdf_text_operations("Exact"),),
            page_forms=(("/Fm1", 0),),
        ),
        _pdf_with_forms_bytes(
            page_content=b"/Outer Do",
            form_contents=(_pdf_text_operations("Nested"), b"/Inner Do"),
            page_forms=(("/Outer", 1),),
            nested_forms=((), (("/Inner", 0),)),
        ),
    ),
)
def test_pdf_loader_accepts_exact_form_text_budget(data: bytes) -> None:
    """Repeated and nested Forms are charged once at each published page."""

    source = _source(data, file_name="exact.pdf", media_type="application/pdf")
    baseline = PdfDocumentLoader().load(source, data, DocumentLoadLimits())
    text = baseline.blocks[0].text
    assert text is not None

    loaded = PdfDocumentLoader().load(
        source,
        data,
        replace(
            DocumentLoadLimits(),
            max_text_code_points=len(text),
            max_cell_code_points=len(text),
        ),
    )

    assert loaded.blocks[0].text == text


def test_pdf_loader_treats_null_contents_as_an_empty_page() -> None:
    """A legal null content object is skipped without changing page numbering."""

    writer = PdfWriter()
    empty_page = writer.add_blank_page(width=100, height=100)
    empty_page[NameObject("/Contents")] = NullObject()
    text_page = writer.add_blank_page(width=100, height=100)
    font = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
        }
    )
    font_reference = writer._add_object(font)
    text_page[NameObject("/Resources")] = DictionaryObject(
        {
            NameObject("/Font"): DictionaryObject(
                {NameObject("/F1"): font_reference}
            )
        }
    )
    stream = DecodedStreamObject()
    stream.set_data(_pdf_text_operations("Second"))
    text_page[NameObject("/Contents")] = writer._add_object(stream)
    output = BytesIO()
    writer.write(output)
    mixed_data = output.getvalue()

    loaded = PdfDocumentLoader().load(
        _source(mixed_data, file_name="mixed.pdf", media_type="application/pdf"),
        mixed_data,
        DocumentLoadLimits(),
    )

    assert [block.page_number for block in loaded.blocks] == [2]

    empty_writer = PdfWriter()
    only_page = empty_writer.add_blank_page(width=100, height=100)
    only_page[NameObject("/Contents")] = NullObject()
    empty_output = BytesIO()
    empty_writer.write(empty_output)
    empty_data = empty_output.getvalue()
    with pytest.raises(DocumentEmptyError):
        PdfDocumentLoader().load(
            _source(empty_data, file_name="null.pdf", media_type="application/pdf"),
            empty_data,
            DocumentLoadLimits(),
        )


def test_pdf_loader_counts_distinct_pages_without_identity_cache_alias() -> None:
    """Different temporary page streams retain their own exact byte charges."""

    first_content = _pdf_text_operations("A")
    second_content = _pdf_text_operations("SECOND")
    data = _pdf_bytes(("A", "SECOND"))
    source = _source(data, file_name="pages.pdf", media_type="application/pdf")
    exact_expansion = len(first_content) + len(second_content)
    loaded = PdfDocumentLoader().load(
        source,
        data,
        replace(DocumentLoadLimits(), max_expanded_bytes=exact_expansion),
    )

    assert len(loaded.blocks) == 2
    assert len(second_content) > len(first_content)
    with pytest.raises(DocumentContentLimitError):
        PdfDocumentLoader().load(
            source,
            data,
            replace(
                DocumentLoadLimits(),
                max_expanded_bytes=exact_expansion - 1,
            ),
        )


def test_pdf_loader_rejects_token_dense_stream_before_operation_parse(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Tiny operators hit the lexical ceiling before pypdf builds their list."""

    page_content = b"q Q " * 30_001
    data = _pdf_with_forms_bytes(
        page_content=page_content,
        form_contents=(),
        page_forms=(),
    )

    def fail_if_operations_are_materialized(_stream: object) -> object:
        """Prove the pre-parser ceiling runs before the lazy property."""

        raise AssertionError("ContentStream.operations was reached")

    monkeypatch.setattr(
        pdf_module.ContentStream,
        "operations",
        property(fail_if_operations_are_materialized),
    )
    with pytest.raises(DocumentContentLimitError):
        PdfDocumentLoader().load(
            _source(data, file_name="tokens.pdf", media_type="application/pdf"),
            data,
            DocumentLoadLimits(),
        )


def test_pdf_loader_skips_inline_image_samples_during_token_preflight() -> None:
    """Binary inline-image bytes cannot masquerade as PDF syntax tokens."""

    image_samples = bytes(range(256)) * 12
    page_content = (
        _pdf_text_operations("VISIBLE")
        + b" q BI /W 32 /H 32 /BPC 8 /CS /RGB ID "
        + image_samples
        + b" EI Q"
    )
    data = _pdf_with_forms_bytes(
        page_content=page_content,
        form_contents=(),
        page_forms=(),
    )

    loaded = PdfDocumentLoader().load(
        _source(data, file_name="inline.pdf", media_type="application/pdf"),
        data,
        DocumentLoadLimits(),
    )

    assert "VISIBLE" in (loaded.blocks[0].text or "")


def test_pdf_resource_inheritance_is_bounded_and_cycle_safe() -> None:
    """Resource lookup cannot traverse an arbitrary or cyclic parent graph."""

    resources = DictionaryObject()
    ancestor = DictionaryObject(
        {NameObject("/Resources"): resources}
    )
    parent = DictionaryObject({NameObject("/Parent"): ancestor})
    child = DictionaryObject({NameObject("/Parent"): parent})

    assert pdf_module._pdf_resources(
        child,
        replace(DocumentLoadLimits(), max_xml_depth=2),
    ) is resources
    with pytest.raises(DocumentContentLimitError):
        pdf_module._pdf_resources(
            child,
            replace(DocumentLoadLimits(), max_xml_depth=1),
        )

    first = DictionaryObject()
    second = DictionaryObject()
    first[NameObject("/Parent")] = second
    second[NameObject("/Parent")] = first
    with pytest.raises(DocumentContentLimitError):
        pdf_module._pdf_resources(first, DocumentLoadLimits())


def test_pdf_resource_inheritance_rejects_invalid_parent() -> None:
    """A non-dictionary parent is corruption rather than an absent resource."""

    child = DictionaryObject(
        {NameObject("/Parent"): NumberObject(1)}
    )

    with pytest.raises(DocumentCorruptError):
        pdf_module._pdf_resources(child, DocumentLoadLimits())


def test_pdf_loader_aggregates_compressed_font_map_expansion() -> None:
    """Many individually bounded font maps share one document-wide budget."""

    data = _pdf_with_compressed_font_maps(font_count=5, map_size=50_000)

    with pytest.raises(DocumentContentLimitError):
        PdfDocumentLoader().load(
            _source(data, file_name="font-maps.pdf", media_type="application/pdf"),
            data,
            replace(DocumentLoadLimits(), max_expanded_bytes=100_000),
        )


def test_pdf_font_stream_allows_exact_zero_byte_boundary() -> None:
    """An empty font stream consumes no bytes at an exactly full budget."""

    limits = replace(DocumentLoadLimits(), max_expanded_bytes=1)
    budget = pdf_module._PdfWorkBudget(limits, expanded_bytes=1)
    empty_stream = DecodedStreamObject()
    empty_stream.set_data(b"")

    assert pdf_module._decode_font_stream(empty_stream, budget) == b""
    nonempty_stream = DecodedStreamObject()
    nonempty_stream.set_data(b"x")
    with pytest.raises(DocumentContentLimitError):
        pdf_module._decode_font_stream(nonempty_stream, budget)


def test_pdf_loader_bounds_font_entries_before_inspection() -> None:
    """A resource dictionary cannot make pypdf construct unlimited fonts."""

    fonts = DictionaryObject(
        {
            NameObject(f"/F{index}"): DictionaryObject()
            for index in range(pdf_module._MAX_PDF_FONT_RESOURCE_VISITS + 1)
        }
    )
    resources = DictionaryObject({NameObject("/Font"): fonts})

    with pytest.raises(DocumentContentLimitError):
        pdf_module._preflight_font_resources(
            resources,
            pdf_module._PdfWorkBudget(DocumentLoadLimits()),
        )


@pytest.mark.parametrize("name_key", ("/BaseFont", "/Subtype"))
def test_pdf_loader_bounds_copied_font_names(name_key: str) -> None:
    """Parser-copied font identity names cannot carry unbounded text."""

    font = DictionaryObject(
        {
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
        }
    )
    font[NameObject(name_key)] = NameObject(
        "/" + "x" * pdf_module._MAX_PDF_FONT_NAME_CODE_POINTS
    )
    resources = DictionaryObject(
        {
            NameObject("/Font"): DictionaryObject(
                {NameObject("/F1"): font}
            )
        }
    )

    with pytest.raises(DocumentContentLimitError):
        pdf_module._preflight_font_resources(
            resources,
            pdf_module._PdfWorkBudget(DocumentLoadLimits()),
        )


@pytest.mark.parametrize("dictionary_encoding", (False, True))
def test_pdf_loader_bounds_named_font_encodings(
    dictionary_encoding: bool,
) -> None:
    """Repeated codec lookup cannot amplify an attacker-sized encoding name."""

    oversized_name = NameObject(
        "/" + "x" * pdf_module._MAX_PDF_FONT_NAME_CODE_POINTS
    )
    encoding: NameObject | DictionaryObject = oversized_name
    if dictionary_encoding:
        encoding = DictionaryObject(
            {NameObject("/BaseEncoding"): oversized_name}
        )
    font = DictionaryObject(
        {
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
            NameObject("/Encoding"): encoding,
        }
    )
    resources = DictionaryObject(
        {
            NameObject("/Font"): DictionaryObject(
                {NameObject("/F1"): font}
            )
        }
    )

    with pytest.raises(DocumentContentLimitError):
        pdf_module._preflight_font_resources(
            resources,
            pdf_module._PdfWorkBudget(DocumentLoadLimits()),
        )


def test_pdf_loader_charges_each_core_font_alias_baseline() -> None:
    """Many ordinary aliases cannot retain uncounted encoding and width maps."""

    font = DictionaryObject(
        {
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
        }
    )
    alias_count = (
        pdf_module._MAX_PDF_FONT_SEMANTIC_ENTRIES
        // pdf_module._PDF_FONT_BASELINE_SEMANTIC_ENTRIES
    ) + 1
    resources = DictionaryObject(
        {
            NameObject("/Font"): DictionaryObject(
                {
                    NameObject(f"/F{index}"): font
                    for index in range(alias_count)
                }
            )
        }
    )

    with pytest.raises(DocumentContentLimitError):
        pdf_module._preflight_font_resources(
            resources,
            pdf_module._PdfWorkBudget(DocumentLoadLimits()),
        )


def test_pdf_loader_bounds_compact_unicode_map_ranges() -> None:
    """A tiny CMap range cannot materialize thousands of dictionary entries."""

    character_map = DecodedStreamObject()
    character_map.set_data(
        b"1 beginbfrange\n<0000> <ffff> <0000>\nendbfrange"
    )
    font = DictionaryObject(
        {
            NameObject("/Subtype"): NameObject("/Type0"),
            NameObject("/ToUnicode"): character_map,
        }
    )
    resources = DictionaryObject(
        {
            NameObject("/Font"): DictionaryObject(
                {NameObject("/F1"): font}
            )
        }
    )

    with pytest.raises(DocumentContentLimitError):
        pdf_module._preflight_font_resources(
            resources,
            pdf_module._PdfWorkBudget(
                replace(DocumentLoadLimits(), max_xml_elements=1_000)
            ),
        )


def test_pdf_loader_recharges_unicode_maps_for_every_font_alias() -> None:
    """Repeated aliases cannot reuse one large CMap outside aggregate work."""

    character_map = DecodedStreamObject()
    character_map.set_data(
        b"1 beginbfrange\n<0000> <ffff> <0000>\nendbfrange"
    )
    font = DictionaryObject(
        {
            NameObject("/Subtype"): NameObject("/Type0"),
            NameObject("/ToUnicode"): character_map,
        }
    )
    resources = DictionaryObject(
        {
            NameObject("/Font"): DictionaryObject(
                {
                    NameObject("/F1"): font,
                    NameObject("/F2"): font,
                }
            )
        }
    )

    with pytest.raises(DocumentContentLimitError):
        pdf_module._preflight_font_resources(
            resources,
            pdf_module._PdfWorkBudget(
                replace(DocumentLoadLimits(), max_xml_elements=100_000)
            ),
        )


def test_pdf_loader_rejects_ambiguous_cmap_markers() -> None:
    """Marker substrings cannot activate mappings only in pypdf normalization."""

    destination = b"0041" * 64
    data = _pdf_with_custom_font_map(
        b"xbeginbfcharx\n<7f> <"
        + destination
        + b">\nxendbfcharx",
        b"BT /F1 12 Tf <7f> Tj ET",
    )

    with pytest.raises(DocumentCorruptError):
        PdfDocumentLoader().load(
            _source(data, file_name="ambiguous-map.pdf", media_type="application/pdf"),
            data,
            DocumentLoadLimits(),
        )


def test_pdf_loader_rejects_ambiguous_cmap_hex_whitespace() -> None:
    """Tabs inside CMap hex cannot split into mappings only for pypdf."""

    with pytest.raises(DocumentCorruptError):
        pdf_module._analyze_to_unicode(
            b"1 beginbfchar\n<01> <0041\t02\t0042\t03>\nendbfchar"
        )


def test_pdf_loader_bounds_compact_cid_width_ranges() -> None:
    """A compact CID width range is charged by expanded entry count."""

    descendant = DictionaryObject(
        {
            NameObject("/W"): ArrayObject(
                [NumberObject(0), NumberObject(65_535), NumberObject(500)]
            )
        }
    )
    font = DictionaryObject(
        {
            NameObject("/Subtype"): NameObject("/Type0"),
            NameObject("/DescendantFonts"): ArrayObject([descendant]),
        }
    )
    resources = DictionaryObject(
        {
            NameObject("/Font"): DictionaryObject(
                {NameObject("/F1"): font}
            )
        }
    )

    with pytest.raises(DocumentContentLimitError):
        pdf_module._preflight_font_resources(
            resources,
            pdf_module._PdfWorkBudget(
                replace(DocumentLoadLimits(), max_xml_elements=1_000)
            ),
        )


def test_pdf_loader_bounds_type3_character_procedures() -> None:
    """Type3 procedure names share the font semantic traversal budget."""

    char_procs = DictionaryObject(
        {
            NameObject(f"/G{index}"): DictionaryObject()
            for index in range(1_001)
        }
    )
    font = DictionaryObject(
        {
            NameObject("/Subtype"): NameObject("/Type3"),
            NameObject("/CharProcs"): char_procs,
        }
    )
    resources = DictionaryObject(
        {
            NameObject("/Font"): DictionaryObject(
                {NameObject("/F1"): font}
            )
        }
    )

    with pytest.raises(DocumentContentLimitError):
        pdf_module._preflight_font_resources(
            resources,
            pdf_module._PdfWorkBudget(
                replace(DocumentLoadLimits(), max_xml_elements=1_000)
            ),
        )


def test_pdf_loader_counts_cr_delimited_type1_mappings() -> None:
    """CR-only Type1 mapping lines consume the same work pypdf performs."""

    font_file = DecodedStreamObject()
    font_file.set_data(
        b"/Encoding\r" + (b"dup 0 /A put\r" * 1_001)
    )
    descriptor = DictionaryObject(
        {NameObject("/FontFile"): font_file}
    )
    font = DictionaryObject(
        {
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/FontDescriptor"): descriptor,
        }
    )
    resources = DictionaryObject(
        {
            NameObject("/Font"): DictionaryObject(
                {NameObject("/F1"): font}
            )
        }
    )

    with pytest.raises(DocumentContentLimitError):
        pdf_module._preflight_font_resources(
            resources,
            pdf_module._PdfWorkBudget(
                replace(DocumentLoadLimits(), max_xml_elements=1_000)
            ),
        )


@pytest.mark.parametrize(
    "text_operation",
    (
        b"<" + (b"7f" * 100) + b"> Tj",
        b"<" + (b"7f" * 100) + b"> '",
        b"0 0 <" + (b"7f" * 100) + b'> "',
        b"[<" + (b"7f" * 100) + b">] TJ",
    ),
)
def test_pdf_loader_rejects_text_mapping_before_materialization(
    monkeypatch: pytest.MonkeyPatch,
    text_operation: bytes,
) -> None:
    """Every text-show operator is bounded before its CMap output is built."""

    destination = b"0041" * 64
    cmap = (
        b"1 beginbfchar\n<7f> <"
        + destination
        + b">\nendbfchar"
    )
    data = _pdf_with_custom_font_map(
        cmap,
        b"BT /F1 12 Tf " + text_operation + b" ET",
    )
    observed_lengths: list[int] = []
    original_visit = pdf_module._PdfTextGuard.visit

    def observe_visit(
        guard: object,
        text: object,
        *args: object,
    ) -> None:
        """Record emitted fragments before preserving production behavior."""

        if isinstance(text, str) and text:
            observed_lengths.append(len(text))
        original_visit(guard, text, *args)  # type: ignore[arg-type]

    monkeypatch.setattr(pdf_module._PdfTextGuard, "visit", observe_visit)
    with pytest.raises(DocumentContentLimitError):
        PdfDocumentLoader().load(
            _source(data, file_name="wide-map.pdf", media_type="application/pdf"),
            data,
            replace(
                DocumentLoadLimits(),
                max_text_code_points=10,
                max_cell_code_points=10,
            ),
        )

    assert not observed_lengths


@pytest.mark.parametrize("inside_form", (False, True))
def test_pdf_loader_bounds_unknown_encoding_difference_names(
    monkeypatch: pytest.MonkeyPatch,
    inside_form: bool,
) -> None:
    """Unknown glyph names are measured before page or Form materialization."""

    data = _pdf_with_difference_font(
        "/" + ("x" * 100),
        inside_form=inside_form,
    )
    observed_lengths: list[int] = []
    original_visit = pdf_module._PdfTextGuard.visit

    def observe_visit(
        guard: object,
        text: object,
        *args: object,
    ) -> None:
        """Record emitted fragments before preserving production behavior."""

        if isinstance(text, str) and text:
            observed_lengths.append(len(text))
        original_visit(guard, text, *args)  # type: ignore[arg-type]

    monkeypatch.setattr(pdf_module._PdfTextGuard, "visit", observe_visit)
    with pytest.raises(DocumentContentLimitError):
        PdfDocumentLoader().load(
            _source(
                data,
                file_name="difference-name.pdf",
                media_type="application/pdf",
            ),
            data,
            replace(
                DocumentLoadLimits(),
                max_text_code_points=10,
                max_cell_code_points=10,
            ),
        )

    assert not observed_lengths


@pytest.mark.parametrize(
    "malformed_operation",
    (
        b"[65 65] Tj",
        b"[65 65] '",
        b"0 0 [65 65] \"",
        b"65 TJ",
    ),
)
@pytest.mark.parametrize("inside_form", (False, True))
def test_pdf_loader_rejects_malformed_text_operands_before_processing(
    monkeypatch: pytest.MonkeyPatch,
    malformed_operation: bytes,
    inside_form: bool,
) -> None:
    """Malformed page and Form operands cannot bypass pre-expansion checks."""

    content = b"BT /F1 12 Tf " + malformed_operation + b" ET"
    data = _pdf_with_forms_bytes(
        page_content=b"/Fm1 Do" if inside_form else content,
        form_contents=(content,) if inside_form else (),
        page_forms=(("/Fm1", 0),) if inside_form else (),
    )
    observed_lengths: list[int] = []
    original_visit = pdf_module._PdfTextGuard.visit

    def observe_visit(
        guard: object,
        text: object,
        *args: object,
    ) -> None:
        """Record emitted fragments before preserving production behavior."""

        if isinstance(text, str) and text:
            observed_lengths.append(len(text))
        original_visit(guard, text, *args)  # type: ignore[arg-type]

    monkeypatch.setattr(pdf_module._PdfTextGuard, "visit", observe_visit)
    with pytest.raises(DocumentCorruptError):
        PdfDocumentLoader().load(
            _source(
                data,
                file_name="malformed-text.pdf",
                media_type="application/pdf",
            ),
            data,
            DocumentLoadLimits(),
        )

    assert not observed_lengths


def test_docx_loader_preserves_ordered_title_heading_paragraph_and_table() -> None:
    """OOXML body order and ragged table cell values survive raw loading."""

    document_xml = b"""<?xml version="1.0" encoding="UTF-8"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:body>
    <w:p><w:pPr><w:pStyle w:val="Title"/></w:pPr><w:r><w:t>Local Guide</w:t></w:r></w:p>
    <w:p><w:pPr><w:pStyle w:val="Heading1"/></w:pPr><w:r><w:t>Overview</w:t></w:r></w:p>
    <w:p><w:r><w:t xml:space="preserve">One </w:t><w:tab/><w:t>paragraph</w:t><w:br/><w:t>line</w:t></w:r></w:p>
    <w:tbl>
      <w:tr><w:tc><w:p><w:r><w:t>A</w:t></w:r></w:p></w:tc><w:tc><w:p><w:r><w:t>B</w:t></w:r></w:p></w:tc></w:tr>
      <w:tr><w:tc><w:p><w:r><w:t>C</w:t></w:r></w:p></w:tc></w:tr>
    </w:tbl>
    <w:p><w:r><w:t>After table</w:t></w:r></w:p>
    <w:sectPr/>
  </w:body>
</w:document>
"""
    data = _docx_bytes(document_xml)
    loaded = DocxDocumentLoader().load(
        _source(data, file_name="guide.docx", media_type=_DOCX_MEDIA_TYPE),
        data,
        DocumentLoadLimits(),
    )

    assert loaded.document_format == "docx"
    assert loaded.page_count is None
    assert loaded.title is not None
    assert loaded.title.text == "Local Guide"
    assert loaded.title.source == "heading"
    assert [block.kind for block in loaded.blocks] == [
        "title",
        "heading",
        "paragraph",
        "table",
        "paragraph",
    ]
    assert loaded.blocks[1].heading_level == 1
    assert loaded.blocks[2].text == "One \tparagraph\nline"
    assert loaded.blocks[3].table is not None
    assert loaded.blocks[3].table.rows == (("A", "B"), ("C",))
    assert loaded.blocks[4].text == "After table"


def test_docx_loader_prefers_embedded_core_title() -> None:
    """Core-properties title provenance wins while title blocks remain intact."""

    document_xml = b"""<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:pPr><w:pStyle w:val="Title"/></w:pPr><w:r><w:t>Body Title</w:t></w:r></w:p></w:body></w:document>"""
    core_xml = b"""<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:title>Embedded Title</dc:title></cp:coreProperties>"""
    data = _docx_bytes(document_xml, core_xml=core_xml)

    loaded = DocxDocumentLoader().load(
        _source(data, file_name="title.docx", media_type=_DOCX_MEDIA_TYPE),
        data,
        DocumentLoadLimits(),
    )

    assert loaded.title is not None
    assert loaded.title.text == "Embedded Title"
    assert loaded.title.source == "embedded"
    assert loaded.blocks[0].text == "Body Title"


def test_docx_loader_rejects_password_container_and_empty_body() -> None:
    """Encrypted Office wrappers and content-free DOCX packages are distinct."""

    ole_data = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + (b"\x00" * 64)
    with pytest.raises(DocumentEncryptedError):
        DocxDocumentLoader().load(
            _source(ole_data, file_name="locked.docx", media_type=_DOCX_MEDIA_TYPE),
            ole_data,
            DocumentLoadLimits(),
        )

    empty_data = _docx_bytes(_EMPTY_DOCUMENT_XML)
    with pytest.raises(DocumentEmptyError):
        DocxDocumentLoader().load(
            _source(empty_data, file_name="empty.docx", media_type=_DOCX_MEDIA_TYPE),
            empty_data,
            DocumentLoadLimits(),
        )


def test_docx_loader_rejects_dtd_entity_and_deep_xml() -> None:
    """DTD expansion and excessive nesting fail before content publication."""

    entity_xml = b"""<?xml version="1.0"?><!DOCTYPE w:document [<!ENTITY x "boom">]><w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>&x;</w:t></w:r></w:p></w:body></w:document>"""
    entity_data = _docx_bytes(entity_xml)
    with pytest.raises(DocumentCorruptError):
        DocxDocumentLoader().load(
            _source(entity_data, file_name="entity.docx", media_type=_DOCX_MEDIA_TYPE),
            entity_data,
            DocumentLoadLimits(),
        )

    nested = "".join("<w:customXml>" for _ in range(8))
    closed = "".join("</w:customXml>" for _ in range(8))
    deep_xml = (
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>'
        + nested
        + "<w:p><w:r><w:t>Deep</w:t></w:r></w:p>"
        + closed
        + "</w:body></w:document>"
    ).encode("utf-8")
    deep_data = _docx_bytes(deep_xml)
    with pytest.raises(DocumentContentLimitError):
        DocxDocumentLoader().load(
            _source(deep_data, file_name="deep.docx", media_type=_DOCX_MEDIA_TYPE),
            deep_data,
            replace(DocumentLoadLimits(), max_xml_depth=5),
        )


def test_docx_loader_rejects_zip_bomb_shape_and_too_many_entries() -> None:
    """Central-directory expansion, ratio, and entry-count limits fail closed."""

    bomb_data = _docx_bytes(
        b"<w:document xmlns:w=\"http://schemas.openxmlformats.org/wordprocessingml/2006/main\"><w:body><w:p><w:r><w:t>Safe</w:t></w:r></w:p></w:body></w:document>",
        extra_members=(("word/bomb.bin", b"A" * 100_000),),
    )
    with pytest.raises(DocumentContentLimitError):
        DocxDocumentLoader().load(
            _source(bomb_data, file_name="bomb.docx", media_type=_DOCX_MEDIA_TYPE),
            bomb_data,
            DocumentLoadLimits(),
        )

    ordinary_data = _docx_bytes(
        b"<w:document xmlns:w=\"http://schemas.openxmlformats.org/wordprocessingml/2006/main\"><w:body><w:p><w:r><w:t>Safe</w:t></w:r></w:p></w:body></w:document>"
    )
    with pytest.raises(DocumentContentLimitError):
        DocxDocumentLoader().load(
            _source(
                ordinary_data,
                file_name="entries.docx",
                media_type=_DOCX_MEDIA_TYPE,
            ),
            ordinary_data,
            replace(DocumentLoadLimits(), max_package_entries=2),
        )


def test_docx_loader_walks_real_central_records_before_zipfile(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A forged-low EOCD count cannot defer a many-entry spike to ZipFile."""

    data = _docx_bytes(
        b"<w:document xmlns:w=\"http://schemas.openxmlformats.org/wordprocessingml/2006/main\"><w:body><w:p><w:r><w:t>Safe</w:t></w:r></w:p></w:body></w:document>",
        extra_members=tuple((f"word/e{index}.bin", b"") for index in range(8)),
    )
    forged = _rewrite_eocd_entry_count(data, 2)
    zipfile_called = False

    def fail_if_zipfile_is_opened(*_args: object, **_kwargs: object) -> object:
        """Record an unsafe constructor call before raising immediately."""

        nonlocal zipfile_called
        zipfile_called = True
        raise AssertionError("ZipFile constructor was reached")

    monkeypatch.setattr(docx_module, "ZipFile", fail_if_zipfile_is_opened)
    with pytest.raises(DocumentContentLimitError):
        DocxDocumentLoader().load(
            _source(
                forged,
                file_name="forged-count.docx",
                media_type=_DOCX_MEDIA_TYPE,
            ),
            forged,
            replace(DocumentLoadLimits(), max_package_entries=2),
        )

    assert not zipfile_called


def test_docx_loader_preflights_legacy_and_zip64_declared_counts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Oversized legacy and Zip64 declarations fail before eager parsing."""

    ordinary = _docx_bytes(
        b"<w:document xmlns:w=\"http://schemas.openxmlformats.org/wordprocessingml/2006/main\"><w:body><w:p><w:r><w:t>Safe</w:t></w:r></w:p></w:body></w:document>"
    )
    fixtures = (
        _rewrite_eocd_entry_count(ordinary, 5_000),
        _zip64_declared_count_bytes(5_000),
    )

    def fail_if_zipfile_is_opened(*_args: object, **_kwargs: object) -> object:
        """Prove both declaration checks precede the standard constructor."""

        raise AssertionError("ZipFile constructor was reached")

    monkeypatch.setattr(docx_module, "ZipFile", fail_if_zipfile_is_opened)
    for data in fixtures:
        with pytest.raises(DocumentContentLimitError):
            DocxDocumentLoader().load(
                _source(
                    data,
                    file_name="declared.docx",
                    media_type=_DOCX_MEDIA_TYPE,
                ),
                data,
                DocumentLoadLimits(),
            )


def test_docx_loader_rejects_missing_zip64_locator() -> None:
    """Legacy sentinel fields without the required Zip64 locator are corrupt."""

    data = b"PK\x03\x04" + pack(
        "<4s4H2LH",
        b"PK\x05\x06",
        0,
        0,
        0xFFFF,
        0xFFFF,
        0xFFFFFFFF,
        0xFFFFFFFF,
        0,
    )

    with pytest.raises(DocumentCorruptError):
        DocxDocumentLoader().load(
            _source(data, file_name="zip64.docx", media_type=_DOCX_MEDIA_TYPE),
            data,
            DocumentLoadLimits(),
        )


@pytest.mark.parametrize(
    "member_name",
    ("../escape.xml", "/absolute.xml", "word\\confused.xml"),
)
def test_docx_loader_rejects_unsafe_member_names(member_name: str) -> None:
    """Archive member spellings cannot create path or separator ambiguity."""

    data = _docx_bytes(
        b"<w:document xmlns:w=\"http://schemas.openxmlformats.org/wordprocessingml/2006/main\"><w:body><w:p><w:r><w:t>Safe</w:t></w:r></w:p></w:body></w:document>",
        extra_members=((member_name, b"ignored"),),
    )

    with pytest.raises(DocumentCorruptError):
        DocxDocumentLoader().load(
            _source(data, file_name="unsafe.docx", media_type=_DOCX_MEDIA_TYPE),
            data,
            DocumentLoadLimits(),
        )


def test_docx_loader_rejects_duplicate_members_and_external_main_part() -> None:
    """Ambiguous members and a remotely targeted main document are invalid."""

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        duplicate_data = _docx_bytes(
            _EMPTY_DOCUMENT_XML,
            extra_members=(("word/document.xml", _EMPTY_DOCUMENT_XML),),
        )
    with pytest.raises(DocumentCorruptError):
        DocxDocumentLoader().load(
            _source(
                duplicate_data,
                file_name="duplicate.docx",
                media_type=_DOCX_MEDIA_TYPE,
            ),
            duplicate_data,
            DocumentLoadLimits(),
        )

    external_relationships = b"""<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="https://example.invalid/document.xml" TargetMode="External"/></Relationships>"""
    external_data = _docx_bytes(
        _EMPTY_DOCUMENT_XML,
        relationships_xml=external_relationships,
    )
    with pytest.raises(DocumentCorruptError):
        DocxDocumentLoader().load(
            _source(
                external_data,
                file_name="external.docx",
                media_type=_DOCX_MEDIA_TYPE,
            ),
            external_data,
            DocumentLoadLimits(),
        )


def test_docx_loader_enforces_table_output_budgets() -> None:
    """Table cells, columns, and cell text obey the shared domain limits."""

    document_xml = b"""<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:tbl><w:tr><w:tc><w:p><w:r><w:t>One</w:t></w:r></w:p></w:tc><w:tc><w:p><w:r><w:t>Two</w:t></w:r></w:p></w:tc></w:tr></w:tbl></w:body></w:document>"""
    data = _docx_bytes(document_xml)
    source = _source(data, file_name="table.docx", media_type=_DOCX_MEDIA_TYPE)

    with pytest.raises(DocumentContentLimitError):
        DocxDocumentLoader().load(
            source,
            data,
            replace(
                DocumentLoadLimits(),
                max_table_cells=1,
                max_table_columns=1,
            ),
        )
    with pytest.raises(DocumentContentLimitError):
        DocxDocumentLoader().load(
            source,
            data,
            replace(DocumentLoadLimits(), max_cell_code_points=2),
        )


def test_docx_loader_charges_paragraph_fragments_before_concatenation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ordinary paragraph text stops at its first over-budget fragment."""

    document_xml = (
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        '<w:body><w:p>'
        + ("<w:r><w:t>X</w:t></w:r>" * 100)
        + "</w:p></w:body></w:document>"
    ).encode("utf-8")
    data = _docx_bytes(document_xml, compression=ZIP_STORED)
    calls = 0
    original_add_text = docx_module._OutputBudget.add_text

    def counted_add_text(
        budget: docx_module._OutputBudget,
        fragment: str,
    ) -> None:
        """Count fragments while retaining the production budget contract."""

        nonlocal calls
        calls += 1
        original_add_text(budget, fragment)

    monkeypatch.setattr(docx_module._OutputBudget, "add_text", counted_add_text)
    with pytest.raises(DocumentContentLimitError):
        DocxDocumentLoader().load(
            _source(data, file_name="fragments.docx", media_type=_DOCX_MEDIA_TYPE),
            data,
            replace(
                DocumentLoadLimits(),
                max_text_code_points=2,
                max_cell_code_points=2,
            ),
        )

    assert calls == 3


def test_docx_loader_flattens_nested_cell_paragraphs_only_once() -> None:
    """Malformed nested paragraphs cannot multiply descendant cell text."""

    visible_text = "ABCD" * 25_000
    nested_open = "<w:p>" * 110
    nested_close = "</w:p>" * 110
    document_xml = (
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:tbl><w:tr><w:tc>'
        + nested_open
        + "<w:r><w:t>"
        + visible_text
        + "</w:t></w:r>"
        + nested_close
        + "</w:tc></w:tr></w:tbl></w:body></w:document>"
    ).encode("utf-8")
    data = _docx_bytes(document_xml, compression=ZIP_STORED)
    source = _source(data, file_name="nested-cell.docx", media_type=_DOCX_MEDIA_TYPE)

    loaded = DocxDocumentLoader().load(
        source,
        data,
        replace(
            DocumentLoadLimits(),
            max_text_code_points=len(visible_text),
            max_cell_code_points=len(visible_text),
        ),
    )

    assert loaded.blocks[0].table is not None
    assert loaded.blocks[0].table.rows == ((visible_text,),)
    with pytest.raises(DocumentContentLimitError):
        DocxDocumentLoader().load(
            source,
            data,
            replace(
                DocumentLoadLimits(),
                max_text_code_points=len(visible_text) - 1,
                max_cell_code_points=len(visible_text) - 1,
            ),
        )


def test_docx_loader_requires_exact_semantic_namespaces() -> None:
    """Foreign names cannot inject text, deletion, style, package, or title data."""

    document_xml = b"""<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006" xmlns:x="urn:foreign" mc:Ignorable="x" mc:ProcessContent="x:del"><w:body><w:p><w:pPr><x:pStyle w:val="Heading1"/><w:pStyle x:val="Heading1"/></w:pPr><x:t>INJECTED</x:t><x:del><w:r><w:t>Visible</w:t></w:r></x:del><w:del><w:r><w:t>Deleted</w:t><x:t>Foreign deleted text</x:t></w:r></w:del></w:p><x:p><w:r><w:t>Foreign paragraph</w:t></w:r></x:p><w:p><w:pPr><w:pStyle w:val="Body"/></w:pPr><w:r><w:t>Body</w:t></w:r></w:p></w:body></w:document>"""
    styles_xml = b"""<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006" xmlns:x="urn:foreign" mc:Ignorable="x"><w:style w:type="paragraph" w:styleId="Body"><x:name w:val="Heading1"/><w:name x:val="Heading1"/></w:style></w:styles>"""
    core_xml = b"""<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006" xmlns:x="urn:foreign" mc:Ignorable="x"><x:title>Foreign title</x:title></cp:coreProperties>"""
    relationships_xml = b"""<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships" xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006" xmlns:x="urn:foreign" mc:Ignorable="x"><x:Relationship Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="https://example.invalid/injected.xml" TargetMode="External"/><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/></Relationships>"""
    content_types_xml = b"""<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types" xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006" xmlns:x="urn:foreign" mc:Ignorable="x"><x:Override PartName="/word/document.xml" ContentType="application/vnd.ms-word.document.macroEnabled.main+xml"/><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>"""
    data = _docx_bytes(
        document_xml,
        relationships_xml=relationships_xml,
        content_types_xml=content_types_xml,
        styles_xml=styles_xml,
        core_xml=core_xml,
    )

    loaded = DocxDocumentLoader().load(
        _source(data, file_name="namespaces.docx", media_type=_DOCX_MEDIA_TYPE),
        data,
        DocumentLoadLimits(),
    )

    assert [block.text for block in loaded.blocks] == ["Visible", "Body"]
    assert [block.kind for block in loaded.blocks] == ["paragraph", "paragraph"]
    assert loaded.title is None


def test_docx_loader_uses_exact_schema_contexts_for_semantic_values() -> None:
    """Invented local wrappers cannot inject text, metadata, or package routes."""

    document_xml = b"""<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:pPr><w:pStyle w:val="Body"/></w:pPr><w:invented><w:r><w:t>INJECTED PARAGRAPH</w:t></w:r></w:invented><w:r><w:t>Visible</w:t></w:r></w:p><w:tbl><w:tr><w:tc><w:invented><w:p><w:r><w:t>INJECTED CELL</w:t></w:r></w:p></w:invented><w:p><w:r><w:t>Cell</w:t></w:r></w:p></w:tc></w:tr></w:tbl></w:body></w:document>"""
    styles_xml = b"""<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:style w:type="paragraph" w:styleId="Body"><w:invented><w:name w:val="Heading1"/><w:outlineLvl w:val="0"/></w:invented></w:style></w:styles>"""
    core_xml = b"""<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" xmlns:dc="http://purl.org/dc/elements/1.1/"><cp:invented><dc:title>INJECTED TITLE</dc:title></cp:invented></cp:coreProperties>"""
    relationships_xml = b"""<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Invented><Relationship Id="bad" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="https://example.invalid/injected.xml" TargetMode="External"/></Invented><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/></Relationships>"""
    content_types_xml = b"""<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Invented><Override PartName="/word/document.xml" ContentType="application/vnd.ms-word.document.macroEnabled.main+xml"/></Invented><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>"""
    data = _docx_bytes(
        document_xml,
        relationships_xml=relationships_xml,
        content_types_xml=content_types_xml,
        styles_xml=styles_xml,
        core_xml=core_xml,
    )

    loaded = DocxDocumentLoader().load(
        _source(data, file_name="schema-context.docx", media_type=_DOCX_MEDIA_TYPE),
        data,
        DocumentLoadLimits(),
    )

    assert loaded.title is None
    assert loaded.blocks[0].kind == "paragraph"
    assert loaded.blocks[0].text == "Visible"
    assert loaded.blocks[1].table is not None
    assert loaded.blocks[1].table.rows == (("Cell",),)


def test_docx_loader_ignores_unrelated_conventional_metadata_parts() -> None:
    """Fixed-name styles and core decoys have no authority without relations."""

    document_xml = b"""<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:pPr><w:pStyle w:val="Body"/></w:pPr><w:r><w:t>Body</w:t></w:r></w:p></w:body></w:document>"""
    styles_decoy = b"""<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:style w:type="paragraph" w:styleId="Body"><w:name w:val="Heading1"/></w:style></w:styles>"""
    core_decoy = b"""<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:title>Decoy title</dc:title></cp:coreProperties>"""
    data = _docx_bytes(
        document_xml,
        extra_members=(
            ("word/styles.xml", styles_decoy),
            ("docProps/core.xml", core_decoy),
        ),
    )

    loaded = DocxDocumentLoader().load(
        _source(data, file_name="unrelated-parts.docx", media_type=_DOCX_MEDIA_TYPE),
        data,
        DocumentLoadLimits(),
    )

    assert loaded.blocks[0].kind == "paragraph"
    assert loaded.title is None


def test_docx_loader_follows_relocated_styles_and_core_relationships() -> None:
    """Authorized optional parts may use safe nonconventional package names."""

    document_xml = b"""<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:pPr><w:pStyle w:val="Body"/></w:pPr><w:r><w:t>Heading</w:t></w:r></w:p></w:body></w:document>"""
    package_relationships = b"""<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/><Relationship Id="rIdCore" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="metadata/properties.xml"/></Relationships>"""
    main_relationships = b"""<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rIdStyles" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="../custom/styles.xml"/></Relationships>"""
    content_types = b"""<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/><Override PartName="/custom/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/><Override PartName="/metadata/properties.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/></Types>"""
    styles_xml = b"""<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:style w:type="paragraph" w:styleId="Body"><w:name w:val="Heading1"/></w:style></w:styles>"""
    core_xml = b"""<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:title>Related title</dc:title></cp:coreProperties>"""
    data = _docx_bytes(
        document_xml,
        relationships_xml=package_relationships,
        content_types_xml=content_types,
        extra_members=(
            ("word/_rels/document.xml.rels", main_relationships),
            ("custom/styles.xml", styles_xml),
            ("metadata/properties.xml", core_xml),
        ),
    )

    loaded = DocxDocumentLoader().load(
        _source(data, file_name="relocated-parts.docx", media_type=_DOCX_MEDIA_TYPE),
        data,
        DocumentLoadLimits(),
    )

    assert loaded.blocks[0].kind == "heading"
    assert loaded.title is not None
    assert loaded.title.text == "Related title"


def test_docx_loader_rejects_external_optional_part_relationship() -> None:
    """Optional metadata cannot bypass the internal package boundary."""

    relationships_xml = b"""<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/><Relationship Id="rIdCore" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="https://example.invalid/core.xml" TargetMode="External"/></Relationships>"""
    data = _docx_bytes(_EMPTY_DOCUMENT_XML, relationships_xml=relationships_xml)

    with pytest.raises(DocumentCorruptError):
        DocxDocumentLoader().load(
            _source(data, file_name="external-core.docx", media_type=_DOCX_MEDIA_TYPE),
            data,
            DocumentLoadLimits(),
        )


def test_binary_loader_identity_and_routes_are_stable() -> None:
    """Binary adapters expose exact registry identities and parser versions."""

    pdf_loader = PdfDocumentLoader()
    docx_loader = DocxDocumentLoader()

    assert pdf_loader.loader_id == "pdf-pypdf"
    assert pdf_loader.loader_version == "1.0.0+pypdf-6.19.0"
    assert pdf_loader.routes == frozenset({(".pdf", "application/pdf")})
    assert docx_loader.loader_id == "docx-ooxml"
    assert docx_loader.loader_version == "1.0.0"
    assert docx_loader.routes == frozenset({(".docx", _DOCX_MEDIA_TYPE)})


def test_docx_loader_applies_markup_compatibility_selection() -> None:
    """MC branches and ignorable wrappers expose only consumer-visible text."""

    document_xml = b"""<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006" xmlns:x="urn:unsupported" mc:Ignorable="x"><w:body><w:p><mc:AlternateContent><mc:Choice Requires="x"><w:r><w:t>UNSUPPORTED CHOICE</w:t></w:r></mc:Choice><mc:Fallback><w:r><w:t>Fallback</w:t></w:r></mc:Fallback></mc:AlternateContent></w:p><w:p><mc:AlternateContent><mc:Choice Requires="w"><w:r><w:t>Word choice</w:t></w:r></mc:Choice><mc:Fallback><w:r><w:t>UNUSED FALLBACK</w:t></w:r></mc:Fallback></mc:AlternateContent></w:p><w:p><x:hidden><w:r><w:t>IGNORED</w:t></w:r></x:hidden><w:r><w:t>Visible</w:t></w:r></w:p><w:p mc:ProcessContent="x:wrapper"><x:wrapper><w:r><w:t>Processed</w:t></w:r></x:wrapper></w:p></w:body></w:document>"""
    data = _docx_bytes(document_xml)

    loaded = DocxDocumentLoader().load(
        _source(data, file_name="compatibility.docx", media_type=_DOCX_MEDIA_TYPE),
        data,
        DocumentLoadLimits(),
    )

    assert [block.text for block in loaded.blocks] == [
        "Fallback",
        "Word choice",
        "Visible",
        "Processed",
    ]


def test_docx_loader_accepts_root_alternate_content() -> None:
    """A selected MC root may replace itself with one Word document root."""

    document_xml = b"""<mc:AlternateContent xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006" xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><mc:Choice Requires="w"><w:document><w:body><w:p><w:r><w:t>Selected root</w:t></w:r></w:p></w:body></w:document></mc:Choice></mc:AlternateContent>"""
    data = _docx_bytes(document_xml)

    loaded = DocxDocumentLoader().load(
        _source(data, file_name="root-choice.docx", media_type=_DOCX_MEDIA_TYPE),
        data,
        DocumentLoadLimits(),
    )

    assert loaded.blocks[0].text == "Selected root"


def test_docx_loader_uses_child_local_ignorable_and_wildcard_rules() -> None:
    """A wrapper's own MC rules decide whether its descendants are exposed."""

    document_xml = b"""<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006"><w:body><w:p><x:hidden xmlns:x="urn:hidden" mc:Ignorable="x" mc:MustUnderstand="x"><w:r><w:t>INJECTED</w:t></w:r></x:hidden><w:r><w:t>Visible</w:t></w:r></w:p><w:p><x:wrapper xmlns:x="urn:processed" mc:Ignorable="x" mc:ProcessContent="x:*"><w:r><w:t>Processed</w:t></w:r></x:wrapper></w:p></w:body></w:document>"""
    data = _docx_bytes(document_xml)

    loaded = DocxDocumentLoader().load(
        _source(data, file_name="local-rules.docx", media_type=_DOCX_MEDIA_TYPE),
        data,
        DocumentLoadLimits(),
    )

    assert [block.text for block in loaded.blocks] == ["Visible", "Processed"]


def test_docx_loader_inherits_process_content_through_redeclaration() -> None:
    """A nested Ignorable declaration keeps ancestor ProcessContent active."""

    document_xml = b"""<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006" xmlns:x="urn:extension" mc:Ignorable="x" mc:ProcessContent="x:wrapper"><w:body><w:p mc:Ignorable="x"><x:wrapper><w:r><w:t>HIDDEN</w:t></w:r></x:wrapper><w:r><w:t>Visible</w:t></w:r></w:p></w:body></w:document>"""
    data = _docx_bytes(document_xml)

    loaded = DocxDocumentLoader().load(
        _source(data, file_name="reset-rules.docx", media_type=_DOCX_MEDIA_TYPE),
        data,
        DocumentLoadLimits(),
    )

    assert loaded.blocks[0].text == "HIDDENVisible"


@pytest.mark.parametrize(
    "xml_attribute",
    ('xml:base="https://example.invalid/"', 'xml:lang="en"', 'xml:space="preserve"'),
)
def test_docx_loader_rejects_xml_state_on_unwrapped_extensions(
    xml_attribute: str,
) -> None:
    """ProcessContent cannot discard inherited XML base, language, or spacing."""

    document_xml = (
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" '
        'xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006" '
        'xmlns:x="urn:extension" mc:Ignorable="x" mc:ProcessContent="x:wrapper">'
        f'<w:body><w:p><x:wrapper {xml_attribute}><w:r><w:t>Text</w:t></w:r>'
        '</x:wrapper></w:p></w:body></w:document>'
    ).encode("utf-8")
    data = _docx_bytes(document_xml)

    with pytest.raises(DocumentCorruptError):
        DocxDocumentLoader().load(
            _source(data, file_name="xml-state.docx", media_type=_DOCX_MEDIA_TYPE),
            data,
            DocumentLoadLimits(),
        )


def test_docx_loader_accepts_empty_markup_compatibility_lists() -> None:
    """The three list-valued compatibility attributes may be empty."""

    document_xml = b"""<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006" mc:Ignorable="" mc:ProcessContent="" mc:MustUnderstand=""><w:body><w:p><w:r><w:t>Visible</w:t></w:r></w:p></w:body></w:document>"""
    data = _docx_bytes(document_xml)

    loaded = DocxDocumentLoader().load(
        _source(data, file_name="empty-lists.docx", media_type=_DOCX_MEDIA_TYPE),
        data,
        DocumentLoadLimits(),
    )

    assert loaded.blocks[0].text == "Visible"


def test_docx_loader_ignores_unselected_branch_descendants() -> None:
    """Fallback selection ignores foreign children and mismatch inside Choice."""

    document_xml = b"""<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006" xmlns:x="urn:unsupported"><w:body><w:p><mc:AlternateContent mc:Ignorable="x"><x:metadata mc:MustUnderstand="x"/><mc:Choice Requires="x" mc:MustUnderstand="x"><w:r mc:MustUnderstand="x"><w:t>UNSELECTED</w:t></w:r></mc:Choice><mc:Fallback><w:r><w:t>Fallback</w:t></w:r></mc:Fallback></mc:AlternateContent></w:p></w:body></w:document>"""
    data = _docx_bytes(document_xml)

    loaded = DocxDocumentLoader().load(
        _source(data, file_name="unselected.docx", media_type=_DOCX_MEDIA_TYPE),
        data,
        DocumentLoadLimits(),
    )

    assert loaded.blocks[0].text == "Fallback"


def test_docx_loader_validates_unselected_branch_wrapper_syntax() -> None:
    """An unselected Choice still requires resolvable wrapper-level prefixes."""

    document_xml = b"""<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006" xmlns:x="urn:unsupported"><w:body><w:p><mc:AlternateContent><mc:Choice Requires="x" mc:MustUnderstand="missing"><w:r><w:t>Unselected</w:t></w:r></mc:Choice><mc:Fallback><w:r><w:t>Fallback</w:t></w:r></mc:Fallback></mc:AlternateContent></w:p></w:body></w:document>"""
    data = _docx_bytes(document_xml)

    with pytest.raises(DocumentCorruptError):
        DocxDocumentLoader().load(
            _source(data, file_name="bad-prefix.docx", media_type=_DOCX_MEDIA_TYPE),
            data,
            DocumentLoadLimits(),
        )


def test_docx_loader_rejects_alternate_content_without_choice() -> None:
    """Fallback cannot make an AlternateContent container structurally valid."""

    document_xml = b"""<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006"><w:body><w:p><mc:AlternateContent><mc:Fallback><w:r><w:t>Fallback</w:t></w:r></mc:Fallback></mc:AlternateContent></w:p></w:body></w:document>"""
    data = _docx_bytes(document_xml)

    with pytest.raises(DocumentCorruptError):
        DocxDocumentLoader().load(
            _source(data, file_name="no-choice.docx", media_type=_DOCX_MEDIA_TYPE),
            data,
            DocumentLoadLimits(),
        )


def test_docx_loader_reports_unsupported_must_understand_on_selected_choice() -> None:
    """Only a selected branch reports a required unsupported capability."""

    document_xml = b"""<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006" xmlns:x="urn:unsupported"><w:body><w:p><mc:AlternateContent><mc:Choice Requires="w" mc:MustUnderstand="x"><w:r><w:t>Selected</w:t></w:r></mc:Choice><mc:Fallback><w:r><w:t>Fallback</w:t></w:r></mc:Fallback></mc:AlternateContent></w:p></w:body></w:document>"""
    data = _docx_bytes(document_xml)

    with pytest.raises(DocumentUnsupportedFeatureError):
        DocxDocumentLoader().load(
            _source(data, file_name="must-understand.docx", media_type=_DOCX_MEDIA_TYPE),
            data,
            DocumentLoadLimits(),
        )


def test_docx_loader_validates_must_understand_before_process_content() -> None:
    """An unwrapped extension cannot hide a required unsupported namespace."""

    unsupported_xml = b"""<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006" xmlns:x="urn:extension" xmlns:y="urn:unsupported" mc:Ignorable="x" mc:ProcessContent="x:wrapper"><w:body><w:p><x:wrapper mc:MustUnderstand="y"><w:r><w:t>Hidden requirement</w:t></w:r></x:wrapper></w:p></w:body></w:document>"""
    unsupported_data = _docx_bytes(unsupported_xml)

    with pytest.raises(DocumentUnsupportedFeatureError):
        DocxDocumentLoader().load(
            _source(
                unsupported_data,
                file_name="processed-requirement.docx",
                media_type=_DOCX_MEDIA_TYPE,
            ),
            unsupported_data,
            DocumentLoadLimits(),
        )

    supported_xml = b"""<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006" xmlns:x="urn:extension" mc:Ignorable="x" mc:ProcessContent="x:wrapper"><w:body><w:p><x:wrapper mc:MustUnderstand="w"><w:r><w:t>Visible</w:t></w:r></x:wrapper></w:p></w:body></w:document>"""
    supported_data = _docx_bytes(supported_xml)

    loaded = DocxDocumentLoader().load(
        _source(
            supported_data,
            file_name="processed-word.docx",
            media_type=_DOCX_MEDIA_TYPE,
        ),
        supported_data,
        DocumentLoadLimits(),
    )
    assert loaded.blocks[0].text == "Visible"


@pytest.mark.parametrize(
    "document_xml",
    (
        b"""<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006" mc:Ignorable="mc"><w:body/></w:document>""",
        b"""<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006"><w:body><w:p><mc:AlternateContent><mc:Choice Requires="mc"><w:r><w:t>Invalid</w:t></w:r></mc:Choice></mc:AlternateContent></w:p></w:body></w:document>""",
    ),
)
def test_docx_loader_rejects_mc_namespace_as_a_rule_target(
    document_xml: bytes,
) -> None:
    """Compatibility rules cannot declare their own MC namespace as data."""

    data = _docx_bytes(document_xml)

    with pytest.raises(DocumentCorruptError):
        DocxDocumentLoader().load(
            _source(data, file_name="mc-target.docx", media_type=_DOCX_MEDIA_TYPE),
            data,
            DocumentLoadLimits(),
        )


def test_docx_loader_validates_later_choices_after_selecting_the_first() -> None:
    """A selected Choice does not hide malformed later branch wrappers."""

    document_xml = b"""<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006"><w:body><w:p><mc:AlternateContent><mc:Choice Requires="w"><w:r><w:t>Selected</w:t></w:r></mc:Choice><mc:Choice><w:r><w:t>Malformed</w:t></w:r></mc:Choice></mc:AlternateContent></w:p></w:body></w:document>"""
    data = _docx_bytes(document_xml)

    with pytest.raises(DocumentCorruptError):
        DocxDocumentLoader().load(
            _source(data, file_name="later-choice.docx", media_type=_DOCX_MEDIA_TYPE),
            data,
            DocumentLoadLimits(),
        )


def test_docx_loader_rejects_direct_process_content_child_of_alternate_content() -> None:
    """ProcessContent cannot manufacture an indirect MC branch wrapper."""

    document_xml = b"""<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006" xmlns:x="urn:extension"><w:body><w:p><mc:AlternateContent mc:Ignorable="x" mc:ProcessContent="x:wrapper"><x:wrapper><mc:Choice Requires="w"><w:r><w:t>Injected</w:t></w:r></mc:Choice></x:wrapper><mc:Choice Requires="w"><w:r><w:t>Selected</w:t></w:r></mc:Choice></mc:AlternateContent></w:p></w:body></w:document>"""
    data = _docx_bytes(document_xml)

    with pytest.raises(DocumentCorruptError):
        DocxDocumentLoader().load(
            _source(data, file_name="direct-wrapper.docx", media_type=_DOCX_MEDIA_TYPE),
            data,
            DocumentLoadLimits(),
        )


@pytest.mark.parametrize(
    "wrapper_attribute",
    ('w:unexpected="value"', 'xml:id="branch"'),
)
def test_docx_loader_rejects_non_ignorable_mc_wrapper_attributes(
    wrapper_attribute: str,
) -> None:
    """A disappearing MC wrapper cannot smuggle active namespace state."""

    document_xml = (
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" '
        'xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006">'
        f'<w:body><w:p><mc:AlternateContent><mc:Choice Requires="w" {wrapper_attribute}>'
        '<w:r><w:t>Selected</w:t></w:r></mc:Choice></mc:AlternateContent>'
        '</w:p></w:body></w:document>'
    ).encode("utf-8")
    data = _docx_bytes(document_xml)

    with pytest.raises(DocumentCorruptError):
        DocxDocumentLoader().load(
            _source(data, file_name="branch-attribute.docx", media_type=_DOCX_MEDIA_TYPE),
            data,
            DocumentLoadLimits(),
        )


def test_docx_loader_accepts_ignorable_mc_wrapper_attribute() -> None:
    """A branch wrapper may carry an attribute from an ignorable namespace."""

    document_xml = b"""<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006" xmlns:x="urn:extension"><w:body><w:p><mc:AlternateContent mc:Ignorable="x"><mc:Choice Requires="w" x:metadata="safe"><w:r><w:t>Selected</w:t></w:r></mc:Choice></mc:AlternateContent></w:p></w:body></w:document>"""
    data = _docx_bytes(document_xml)

    loaded = DocxDocumentLoader().load(
        _source(data, file_name="branch-attribute.docx", media_type=_DOCX_MEDIA_TYPE),
        data,
        DocumentLoadLimits(),
    )

    assert loaded.blocks[0].text == "Selected"


def test_docx_loader_reports_unknown_non_ignorable_element_namespace() -> None:
    """Foreign wrappers become unsupported features without exposing text."""

    document_xml = b"""<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" xmlns:x="urn:foreign"><w:body><w:p><x:wrapper><w:r><w:t>Injected</w:t></w:r></x:wrapper></w:p></w:body></w:document>"""
    data = _docx_bytes(document_xml)

    with pytest.raises(DocumentUnsupportedFeatureError):
        DocxDocumentLoader().load(
            _source(data, file_name="foreign-wrapper.docx", media_type=_DOCX_MEDIA_TYPE),
            data,
            DocumentLoadLimits(),
        )


def test_docx_loader_reports_unknown_non_ignorable_attribute_namespace() -> None:
    """Unsupported active attributes fail without changing Word semantics."""

    document_xml = b"""<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" xmlns:x="urn:foreign"><w:body><w:p x:metadata="unsupported"><w:r><w:t>Text</w:t></w:r></w:p></w:body></w:document>"""
    data = _docx_bytes(document_xml)

    with pytest.raises(DocumentUnsupportedFeatureError):
        DocxDocumentLoader().load(
            _source(data, file_name="foreign-attribute.docx", media_type=_DOCX_MEDIA_TYPE),
            data,
            DocumentLoadLimits(),
        )


def test_docx_loader_reuses_state_for_empty_compatibility_redeclarations() -> None:
    """Empty directives do not copy a potentially large inherited rule set."""

    parent = docx_module._MarkupCompatibilityState(
        ignorable_namespaces=frozenset({"urn:extension"}),
        process_content=frozenset(
            {("urn:extension", f"name-{index}") for index in range(4_096)}
        ),
    )
    element = ElementTree.fromstring(
        b"""<w:p xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006" mc:Ignorable="" mc:ProcessContent=""/>"""
    )

    state = docx_module._markup_state_for_element(element, parent, {})

    assert state is parent


def test_docx_loader_validates_must_understand_on_ignored_wrapper() -> None:
    """Ignored wrapper support is inert, but its prefix syntax must resolve."""

    document_xml = b"""<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006" xmlns:x="urn:extension" mc:Ignorable="x"><w:body><w:p><x:hidden mc:MustUnderstand="missing"><w:r><w:t>Ignored</w:t></w:r></x:hidden><w:r><w:t>Visible</w:t></w:r></w:p></w:body></w:document>"""
    data = _docx_bytes(document_xml)

    with pytest.raises(DocumentCorruptError):
        DocxDocumentLoader().load(
            _source(data, file_name="ignored-prefix.docx", media_type=_DOCX_MEDIA_TYPE),
            data,
            DocumentLoadLimits(),
        )


@pytest.mark.parametrize(
    "alternate_content",
    (
        '<mc:AlternateContent mc:Ignorable="xml" xml:id="alternate"><mc:Choice Requires="w"><w:r><w:t>Selected</w:t></w:r></mc:Choice></mc:AlternateContent>',
        '<mc:AlternateContent><mc:Choice Requires="w" mc:Ignorable="xml" xml:id="choice"><w:r><w:t>Selected</w:t></w:r></mc:Choice></mc:AlternateContent>',
        '<mc:AlternateContent><mc:Choice Requires="x"><w:r><w:t>Unused</w:t></w:r></mc:Choice><mc:Fallback mc:Ignorable="xml" xml:id="fallback"><w:r><w:t>Fallback</w:t></w:r></mc:Fallback></mc:AlternateContent>',
    ),
)
def test_docx_loader_rejects_xml_attributes_on_mc_elements(
    alternate_content: str,
) -> None:
    """Declaring the XML namespace ignorable cannot alter MC wrapper scope."""

    document_xml = (
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" '
        'xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006" '
        f'xmlns:x="urn:unsupported"><w:body><w:p>{alternate_content}'
        '</w:p></w:body></w:document>'
    ).encode("utf-8")
    data = _docx_bytes(document_xml)

    with pytest.raises(DocumentCorruptError):
        DocxDocumentLoader().load(
            _source(data, file_name="xml-mc-attribute.docx", media_type=_DOCX_MEDIA_TYPE),
            data,
            DocumentLoadLimits(),
        )


@pytest.mark.parametrize(
    "unknown_attribute",
    (
        'mc:Unknown="ordinary"',
        'mc:Unknown="alternate"',
        'mc:Unknown="choice"',
        'mc:Unknown="fallback"',
    ),
)
def test_docx_loader_rejects_unknown_mc_attributes(
    unknown_attribute: str,
) -> None:
    """Only defined MC attributes may affect active or alternative markup."""

    target = unknown_attribute.split('="', 1)[1].removesuffix('"')
    ordinary_attribute = unknown_attribute if target == "ordinary" else ""
    alternate_attribute = unknown_attribute if target == "alternate" else ""
    choice_attribute = unknown_attribute if target == "choice" else ""
    fallback_attribute = unknown_attribute if target == "fallback" else ""
    document_xml = (
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" '
        'xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006">'
        f'<w:body {ordinary_attribute}><w:p><mc:AlternateContent {alternate_attribute}>'
        f'<mc:Choice Requires="w" {choice_attribute}><w:r><w:t>Selected</w:t></w:r>'
        f'</mc:Choice><mc:Fallback {fallback_attribute}><w:r><w:t>Fallback</w:t>'
        '</w:r></mc:Fallback></mc:AlternateContent></w:p></w:body></w:document>'
    ).encode("utf-8")
    data = _docx_bytes(document_xml)

    with pytest.raises(DocumentCorruptError):
        DocxDocumentLoader().load(
            _source(data, file_name="unknown-mc.docx", media_type=_DOCX_MEDIA_TYPE),
            data,
            DocumentLoadLimits(),
        )


@pytest.mark.parametrize(
    "legacy_attribute",
    (
        'mc:PreserveElements="x:item"',
        'mc:PreserveAttributes="x:item"',
    ),
)
def test_docx_loader_rejects_legacy_preserve_directives(
    legacy_attribute: str,
) -> None:
    """Unsupported pre-2015 MC directives cannot be silently accepted."""

    document_xml = (
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" '
        'xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006" '
        f'xmlns:x="urn:extension" mc:Ignorable="x" {legacy_attribute}>'
        '<w:body><w:p><w:r><w:t>Text</w:t></w:r></w:p></w:body></w:document>'
    ).encode("utf-8")
    data = _docx_bytes(document_xml)

    with pytest.raises(DocumentCorruptError):
        DocxDocumentLoader().load(
            _source(data, file_name="legacy-mc.docx", media_type=_DOCX_MEDIA_TYPE),
            data,
            DocumentLoadLimits(),
        )


@pytest.mark.parametrize(
    "compatibility_attribute",
    (
        'mc:Ignorable="x\u00a0"',
        'mc:Ignorable="1invalid"',
        'mc:Ignorable="x" mc:ProcessContent="x:1invalid"',
    ),
)
def test_docx_loader_uses_xml_whitespace_and_ncname_rules(
    compatibility_attribute: str,
) -> None:
    """Unicode separators and malformed QName components are not MC tokens."""

    document_xml = (
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" '
        'xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006" '
        f'xmlns:x="urn:extension" {compatibility_attribute}>'
        '<w:body><w:p><w:r><w:t>Text</w:t></w:r></w:p></w:body></w:document>'
    ).encode("utf-8")
    data = _docx_bytes(document_xml)

    with pytest.raises(DocumentCorruptError):
        DocxDocumentLoader().load(
            _source(data, file_name="mc-token.docx", media_type=_DOCX_MEDIA_TYPE),
            data,
            DocumentLoadLimits(),
        )


def test_docx_loader_bounds_each_markup_compatibility_directive() -> None:
    """One oversized MC value stops before compatibility state is retained."""

    oversized = "x" * (docx_module._MAX_MC_DIRECTIVE_CODE_POINTS + 1)
    document_xml = (
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" '
        'xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006" '
        f'mc:Ignorable="{oversized}"><w:body/></w:document>'
    ).encode("utf-8")
    data = _docx_bytes(document_xml)

    with pytest.raises(DocumentContentLimitError):
        DocxDocumentLoader().load(
            _source(data, file_name="long-directive.docx", media_type=_DOCX_MEDIA_TYPE),
            data,
            DocumentLoadLimits(),
        )


def test_docx_loader_bounds_markup_compatibility_tokens_per_directive() -> None:
    """A compact compatibility list cannot create unbounded token objects."""

    tokens = " ".join(
        "x" for _ in range(docx_module._MAX_MC_TOKENS_PER_DIRECTIVE + 1)
    )
    document_xml = (
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" '
        'xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006" '
        f'xmlns:x="urn:extension" mc:Ignorable="{tokens}"><w:body/></w:document>'
    ).encode("utf-8")
    data = _docx_bytes(document_xml)

    with pytest.raises(DocumentContentLimitError):
        DocxDocumentLoader().load(
            _source(data, file_name="many-tokens.docx", media_type=_DOCX_MEDIA_TYPE),
            data,
            DocumentLoadLimits(),
        )


def test_docx_loader_bounds_cumulative_markup_compatibility_tokens() -> None:
    """Many individually valid directives share one package-wide token budget."""

    tokens = " ".join("x" for _ in range(4_096))
    elements = "".join(
        f'<x:hidden mc:MustUnderstand="{tokens}"/>' for _ in range(5)
    )
    document_xml = (
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" '
        'xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006" '
        f'xmlns:x="urn:extension" mc:Ignorable="x"><w:body>{elements}'
        '</w:body></w:document>'
    ).encode("utf-8")
    data = _docx_bytes(document_xml)

    with pytest.raises(DocumentContentLimitError):
        DocxDocumentLoader().load(
            _source(data, file_name="cumulative-tokens.docx", media_type=_DOCX_MEDIA_TYPE),
            data,
            DocumentLoadLimits(),
        )


def test_docx_loader_bounds_simultaneous_namespace_bindings() -> None:
    """One element cannot retain an unbounded in-scope prefix table."""

    declarations = " ".join(
        f'xmlns:x{index}="urn:x{index}"'
        for index in range(docx_module._MAX_NAMESPACE_BINDINGS)
    )
    document_xml = (
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" '
        f'{declarations}><w:body/></w:document>'
    ).encode("utf-8")
    data = _docx_bytes(document_xml)

    with pytest.raises(DocumentContentLimitError):
        DocxDocumentLoader().load(
            _source(data, file_name="namespace-bindings.docx", media_type=_DOCX_MEDIA_TYPE),
            data,
            DocumentLoadLimits(),
        )


def test_docx_loader_bounds_cumulative_namespace_declarations() -> None:
    """Namespace redeclaration churn shares one package-wide parsing budget."""

    elements = "".join(
        f'<w:r xmlns:x="urn:x{index}"/>'
        for index in range(docx_module._MAX_NAMESPACE_DECLARATIONS + 1)
    )
    document_xml = (
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        f'<w:body><w:p>{elements}</w:p></w:body></w:document>'
    ).encode("utf-8")
    data = _docx_bytes(document_xml)

    with pytest.raises(DocumentContentLimitError):
        DocxDocumentLoader().load(
            _source(data, file_name="namespace-churn.docx", media_type=_DOCX_MEDIA_TYPE),
            data,
            DocumentLoadLimits(),
        )


def test_docx_loader_uses_part_specific_markup_profiles() -> None:
    """A core-properties namespace is not an understood Word feature."""

    document_xml = b"""<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006" xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties"><w:body><w:p><mc:AlternateContent><mc:Choice Requires="cp"><w:r><w:t>WRONG PROFILE</w:t></w:r></mc:Choice><mc:Fallback><w:r><w:t>Fallback</w:t></w:r></mc:Fallback></mc:AlternateContent></w:p></w:body></w:document>"""
    data = _docx_bytes(document_xml)

    loaded = DocxDocumentLoader().load(
        _source(data, file_name="part-profile.docx", media_type=_DOCX_MEDIA_TYPE),
        data,
        DocumentLoadLimits(),
    )

    assert loaded.blocks[0].text == "Fallback"


def test_docx_loader_isolates_package_relationship_markup_profile() -> None:
    """Word support cannot select a Choice inside package relationships."""

    relationships_xml = b"""<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships" xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006" xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><mc:AlternateContent><mc:Choice Requires="w"><Relationship Id="bad" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="https://example.invalid/document.xml" TargetMode="External"/></mc:Choice><mc:Fallback><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/></mc:Fallback></mc:AlternateContent></Relationships>"""
    document_xml = b"""<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>Body</w:t></w:r></w:p></w:body></w:document>"""
    data = _docx_bytes(document_xml, relationships_xml=relationships_xml)

    loaded = DocxDocumentLoader().load(
        _source(data, file_name="relationship-profile.docx", media_type=_DOCX_MEDIA_TYPE),
        data,
        DocumentLoadLimits(),
    )

    assert loaded.blocks[0].text == "Body"


def test_docx_loader_isolates_the_content_types_markup_profile() -> None:
    """Word support cannot select a Choice inside the content-types part."""

    content_types_xml = b"""<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types" xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006" xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><mc:AlternateContent><mc:Choice Requires="w"><Override PartName="/word/document.xml" ContentType="application/vnd.ms-word.document.macroEnabled.main+xml"/></mc:Choice><mc:Fallback><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></mc:Fallback></mc:AlternateContent></Types>"""
    document_xml = b"""<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>Body</w:t></w:r></w:p></w:body></w:document>"""
    data = _docx_bytes(document_xml, content_types_xml=content_types_xml)

    loaded = DocxDocumentLoader().load(
        _source(data, file_name="content-profile.docx", media_type=_DOCX_MEDIA_TYPE),
        data,
        DocumentLoadLimits(),
    )

    assert loaded.blocks[0].text == "Body"


def test_docx_loader_accepts_word_relationship_attributes() -> None:
    """The Word profile recognizes relationship attributes, not elements."""

    document_xml = b"""<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><w:body><w:p><w:hyperlink r:id="rId2"><w:r><w:t>Linked</w:t></w:r></w:hyperlink></w:p></w:body></w:document>"""
    data = _docx_bytes(document_xml)

    loaded = DocxDocumentLoader().load(
        _source(data, file_name="relationship-attribute.docx", media_type=_DOCX_MEDIA_TYPE),
        data,
        DocumentLoadLimits(),
    )

    assert loaded.blocks[0].text == "Linked"


def test_docx_loader_skips_known_opaque_drawing_and_math_subtrees() -> None:
    """Images and equations do not block or inject surrounding raw text."""

    document_xml = b"""<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" xmlns:wp="http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing" xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" xmlns:pic="http://schemas.openxmlformats.org/drawingml/2006/picture" xmlns:m="http://schemas.openxmlformats.org/officeDocument/2006/math"><w:body><w:p><w:r><w:t>Before</w:t></w:r><w:r><w:drawing><wp:inline><a:graphic><pic:pic><w:t>INJECTED IMAGE TEXT</w:t></pic:pic></a:graphic></wp:inline></w:drawing></w:r><m:oMath><m:r><m:t>Equation</m:t></m:r></m:oMath><w:r><w:t>After</w:t></w:r></w:p></w:body></w:document>"""
    data = _docx_bytes(document_xml)

    loaded = DocxDocumentLoader().load(
        _source(data, file_name="opaque-content.docx", media_type=_DOCX_MEDIA_TYPE),
        data,
        DocumentLoadLimits(),
    )

    assert loaded.blocks[0].text == "BeforeAfter"


def test_docx_loader_processes_children_of_ignorable_opaque_wrapper() -> None:
    """ProcessContent takes precedence over the opaque vocabulary policy."""

    document_xml = b"""<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006" xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" mc:Ignorable="a" mc:ProcessContent="a:graphic"><w:body><w:p><a:graphic><w:r><w:t>Visible child</w:t></w:r></a:graphic></w:p></w:body></w:document>"""
    data = _docx_bytes(document_xml)

    loaded = DocxDocumentLoader().load(
        _source(data, file_name="opaque-wrapper.docx", media_type=_DOCX_MEDIA_TYPE),
        data,
        DocumentLoadLimits(),
    )

    assert loaded.blocks[0].text == "Visible child"


def test_docx_loader_ignores_must_understand_on_ignored_opaque_wrapper() -> None:
    """An ignored opaque subtree validates QName syntax but not capability."""

    document_xml = b"""<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006" xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" xmlns:y="urn:unsupported" mc:Ignorable="a"><w:body><w:p><a:graphic mc:MustUnderstand="y"><w:r><w:t>Ignored</w:t></w:r></a:graphic><w:r><w:t>Visible</w:t></w:r></w:p></w:body></w:document>"""
    data = _docx_bytes(document_xml)

    loaded = DocxDocumentLoader().load(
        _source(data, file_name="ignored-opaque.docx", media_type=_DOCX_MEDIA_TYPE),
        data,
        DocumentLoadLimits(),
    )

    assert loaded.blocks[0].text == "Visible"


def test_docx_loader_accepts_standard_core_property_namespaces() -> None:
    """Dublin Core terms and XML Schema attributes remain valid metadata."""

    document_xml = b"""<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>Body</w:t></w:r></w:p></w:body></w:document>"""
    core_xml = b"""<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:dcterms="http://purl.org/dc/terms/" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"><dc:title>Core title</dc:title><dcterms:created xsi:type="dcterms:W3CDTF">2026-09-24T00:00:00Z</dcterms:created></cp:coreProperties>"""
    data = _docx_bytes(document_xml, core_xml=core_xml)

    loaded = DocxDocumentLoader().load(
        _source(data, file_name="core-properties.docx", media_type=_DOCX_MEDIA_TYPE),
        data,
        DocumentLoadLimits(),
    )

    assert loaded.title is not None
    assert loaded.title.text == "Core title"


@pytest.mark.parametrize(
    "document_xml",
    (
        b'<?xml version="1.0" encoding="x-unknown"?><w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body/></w:document>',
        b'<?xml version="1.0" encoding="UTF-32"?><w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body/></w:document>',
    ),
)
def test_docx_loader_maps_invalid_xml_encodings_to_corrupt(
    document_xml: bytes,
) -> None:
    """Unsupported or byte-inconsistent XML declarations are stable corruption."""

    data = _docx_bytes(document_xml)

    with pytest.raises(DocumentCorruptError):
        DocxDocumentLoader().load(
            _source(data, file_name="encoding.docx", media_type=_DOCX_MEDIA_TYPE),
            data,
            DocumentLoadLimits(),
        )


def test_docx_loader_accepts_strict_wordprocessing_namespace() -> None:
    """Strict WordprocessingML and relationship URIs remain supported."""

    strict_document = b"""<w:document xmlns:w="http://purl.oclc.org/ooxml/wordprocessingml/main"><w:body><w:p><w:r><w:t>Strict text</w:t></w:r></w:p></w:body></w:document>"""
    strict_relationships = b"""<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://purl.oclc.org/ooxml/officeDocument/relationships/officeDocument" Target="word/document.xml"/></Relationships>"""
    data = _docx_bytes(
        strict_document,
        relationships_xml=strict_relationships,
    )

    loaded = DocxDocumentLoader().load(
        _source(data, file_name="strict.docx", media_type=_DOCX_MEDIA_TYPE),
        data,
        DocumentLoadLimits(),
    )

    assert loaded.blocks[0].text == "Strict text"


def test_docx_loader_does_not_promote_oversized_heading_title() -> None:
    """A long styled block remains content without violating title bounds."""

    heading = "T" * (MAX_DOCUMENT_TITLE_CODE_POINTS + 1)
    document_xml = (
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:pPr><w:pStyle w:val="Title"/></w:pPr><w:r><w:t>'
        + heading
        + "</w:t></w:r></w:p></w:body></w:document>"
    ).encode("utf-8")
    data = _docx_bytes(document_xml)

    loaded = DocxDocumentLoader().load(
        _source(data, file_name="long-title.docx", media_type=_DOCX_MEDIA_TYPE),
        data,
        DocumentLoadLimits(),
    )

    assert loaded.blocks[0].kind == "title"
    assert loaded.blocks[0].text == heading
    assert loaded.title is None


def test_docx_loader_ignores_macro_payloads_and_tracks_exact_bytes() -> None:
    """Unselected binary parts are never interpreted and byte drift is rejected."""

    document_xml = b"""<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>Visible text</w:t></w:r></w:p></w:body></w:document>"""
    data = _docx_bytes(
        document_xml,
        extra_members=(("word/vbaProject.bin", b"not executed"),),
    )
    source = _source(data, file_name="safe.docx", media_type=_DOCX_MEDIA_TYPE)

    loaded = DocxDocumentLoader().load(source, data, DocumentLoadLimits())

    assert loaded.blocks[0].text == "Visible text"
    with pytest.raises(DocumentReadError):
        DocxDocumentLoader().load(source, data + b"x", DocumentLoadLimits())


def test_docx_loader_revalidates_its_exact_route() -> None:
    """The binary adapter cannot be invoked through a mismatched suffix or MIME."""

    document_xml = b"""<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>Routed</w:t></w:r></w:p></w:body></w:document>"""
    data = _docx_bytes(document_xml)
    loaded = DocxDocumentLoader().load(
        _source(data, file_name="GUIDE.DOCX", media_type=_DOCX_MEDIA_TYPE),
        data,
        DocumentLoadLimits(),
    )

    assert loaded.blocks[0].text == "Routed"
    with pytest.raises(DocumentUnsupportedFormatError):
        DocxDocumentLoader().load(
            _source(data, file_name="guide.zip", media_type=_DOCX_MEDIA_TYPE),
            data,
            DocumentLoadLimits(),
        )
    with pytest.raises(DocumentUnsupportedFormatError):
        DocxDocumentLoader().load(
            _source(
                data,
                file_name="guide.docx",
                media_type="application/zip",
            ),
            data,
            DocumentLoadLimits(),
        )


def test_docx_loader_errors_never_include_the_display_name() -> None:
    """Native ZIP failures remain stable and path-free at the adapter boundary."""

    data = b"PK\x03\x04truncated-private-payload"

    with pytest.raises(DocumentCorruptError) as captured:
        DocxDocumentLoader().load(
            _source(
                data,
                file_name="private-course.docx",
                media_type=_DOCX_MEDIA_TYPE,
            ),
            data,
            DocumentLoadLimits(),
        )

    assert "private-course.docx" not in str(captured.value)
