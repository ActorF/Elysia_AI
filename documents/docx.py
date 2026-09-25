"""Load bounded DOCX structure without extracting files or following links.

DOCX is an OPC ZIP package containing XML and optional binary assets.  This
adapter validates the complete central directory before reading selected XML
parts, then enforces actual-read, element, depth, table, block, and text
budgets.  It never writes archive members to disk, loads macros or embedded
objects, or dereferences external relationships.
"""

from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
from pathlib import PurePath
import posixpath
import re
import stat
from struct import Struct
from typing import Callable, Final, Iterator, cast
from urllib.parse import unquote, urlsplit
import unicodedata
from xml.etree import ElementTree
from zipfile import (
    BadZipFile,
    LargeZipFile,
    ZIP_DEFLATED,
    ZIP_STORED,
    ZipFile,
    ZipInfo,
)
import zlib

from .domain import (
    DOCUMENT_SCHEMA_VERSION,
    MAX_DOCUMENT_TITLE_CODE_POINTS,
    DocumentBlock,
    DocumentLoadLimits,
    DocumentSource,
    DocumentTable,
    DocumentTitle,
    LoadedDocument,
)
from .exceptions import (
    DocumentContentLimitError,
    DocumentCorruptError,
    DocumentEmptyError,
    DocumentEncryptedError,
    DocumentError,
    DocumentLoadFailedError,
    DocumentReadError,
    DocumentTooLargeError,
    DocumentUnsupportedFeatureError,
    DocumentUnsupportedFormatError,
    DocumentValidationError,
)
from .protocol import DocumentRoute


_ZIP_LOCAL_HEADER: Final = b"PK\x03\x04"
_ZIP_CENTRAL_HEADER: Final = b"PK\x01\x02"
_ZIP_END_OF_CENTRAL_DIRECTORY: Final = b"PK\x05\x06"
_ZIP64_END_OF_CENTRAL_DIRECTORY: Final = b"PK\x06\x06"
_ZIP64_END_OF_CENTRAL_DIRECTORY_LOCATOR: Final = b"PK\x06\x07"
_OLE_COMPOUND_HEADER: Final = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
_CONTENT_TYPES_PART: Final = "[Content_Types].xml"
_PACKAGE_RELATIONSHIPS_PART: Final = "_rels/.rels"
_MAIN_DOCUMENT_CONTENT_TYPE: Final = (
    "application/vnd.openxmlformats-officedocument."
    "wordprocessingml.document.main+xml"
)
_STYLES_CONTENT_TYPE: Final = (
    "application/vnd.openxmlformats-officedocument."
    "wordprocessingml.styles+xml"
)
_CORE_PROPERTIES_CONTENT_TYPE: Final = (
    "application/vnd.openxmlformats-package.core-properties+xml"
)
_MAX_MEMBER_BYTES: Final = 16 * 1024 * 1024
_MAX_XML_PART_BYTES: Final = 16 * 1024 * 1024
_MAX_COMPRESSION_RATIO: Final = 200
_MAX_CENTRAL_DIRECTORY_BYTES: Final = 4 * 1024 * 1024
_MAX_CENTRAL_DIRECTORY_BYTES_PER_ENTRY: Final = 4 * 1024
_MAX_ZIP64_END_RECORD_BYTES: Final = 64 * 1024
_MAX_MC_DIRECTIVE_CODE_POINTS: Final = 64 * 1024
_MAX_MC_TOKENS_PER_DIRECTIVE: Final = 4_096
_MAX_MC_TOKENS_TOTAL: Final = 16_384
_MAX_NAMESPACE_BINDINGS: Final = 256
_MAX_NAMESPACE_DECLARATIONS: Final = 8_192
_XML_WHITESPACE: Final = frozenset(" \t\r\n")
_ZIP_END_STRUCT: Final = Struct("<4s4H2LH")
_ZIP64_LOCATOR_STRUCT: Final = Struct("<4sLQL")
_ZIP64_END_STRUCT: Final = Struct("<4sQ2H2L4Q")
_ZIP_CENTRAL_STRUCT: Final = Struct("<4s6H3L5H2L")
_WORDPROCESSING_NAMESPACES: Final = frozenset(
    {
        "http://schemas.openxmlformats.org/wordprocessingml/2006/main",
        "http://purl.oclc.org/ooxml/wordprocessingml/main",
    }
)
_MARKUP_COMPATIBILITY_NAMESPACE: Final = (
    "http://schemas.openxmlformats.org/markup-compatibility/2006"
)
_MARKUP_COMPATIBILITY_ATTRIBUTES: Final = frozenset(
    {
        "Ignorable",
        "MustUnderstand",
        "ProcessContent",
    }
)
_XML_NAMESPACE: Final = "http://www.w3.org/XML/1998/namespace"
_PACKAGE_RELATIONSHIP_NAMESPACES: Final = frozenset(
    {
        "http://schemas.openxmlformats.org/package/2006/relationships",
        "http://purl.oclc.org/ooxml/package/relationships",
    }
)
_CONTENT_TYPE_NAMESPACES: Final = frozenset(
    {
        "http://schemas.openxmlformats.org/package/2006/content-types",
        "http://purl.oclc.org/ooxml/package/content-types",
    }
)
_CORE_PROPERTIES_NAMESPACES: Final = frozenset(
    {
        "http://schemas.openxmlformats.org/package/2006/metadata/core-properties",
    }
)
_DUBLIN_CORE_NAMESPACES: Final = frozenset(
    {"http://purl.org/dc/elements/1.1/"}
)
_DUBLIN_CORE_TERMS_NAMESPACES: Final = frozenset(
    {"http://purl.org/dc/terms/"}
)
_XML_SCHEMA_INSTANCE_NAMESPACES: Final = frozenset(
    {"http://www.w3.org/2001/XMLSchema-instance"}
)
_OFFICE_DOCUMENT_RELATIONSHIP_NAMESPACES: Final = frozenset(
    {
        "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
        "http://purl.oclc.org/ooxml/officeDocument/relationships",
    }
)
_WORD_OPAQUE_CONTENT_NAMESPACES: Final = frozenset(
    {
        "http://schemas.openxmlformats.org/drawingml/2006/main",
        "http://schemas.openxmlformats.org/drawingml/2006/picture",
        "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing",
        "http://schemas.openxmlformats.org/officeDocument/2006/math",
        "http://purl.oclc.org/ooxml/drawingml/main",
        "http://purl.oclc.org/ooxml/drawingml/picture",
        "http://purl.oclc.org/ooxml/drawingml/wordprocessingDrawing",
        "http://purl.oclc.org/ooxml/officeDocument/math",
        "urn:schemas-microsoft-com:office:office",
        "urn:schemas-microsoft-com:office:word",
        "urn:schemas-microsoft-com:vml",
    }
)
_OFFICE_DOCUMENT_RELATIONSHIP_TYPES: Final = frozenset(
    {
        "http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument",
        "http://purl.oclc.org/ooxml/officeDocument/relationships/officeDocument",
    }
)
_STYLES_RELATIONSHIP_TYPES: Final = frozenset(
    {
        "http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles",
        "http://purl.oclc.org/ooxml/officeDocument/relationships/styles",
    }
)
_CORE_PROPERTIES_RELATIONSHIP_TYPES: Final = frozenset(
    {
        "http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties",
        "http://purl.oclc.org/ooxml/package/relationships/metadata/core-properties",
    }
)
_STRUCTURAL_WRAPPERS: Final = frozenset(
    {"sdt", "sdtContent", "customXml", "ins", "moveTo", "smartTag"}
)
_PARAGRAPH_TEXT_CONTAINERS: Final = frozenset(
    {
        *_STRUCTURAL_WRAPPERS,
        "bdo",
        "dir",
        "fldSimple",
        "hyperlink",
        "p",
        "r",
        "rt",
        "ruby",
        "rubyBase",
    }
)
_CELL_PARAGRAPH_CONTAINERS: Final = frozenset(
    {*_STRUCTURAL_WRAPPERS, "tbl", "tr", "tc"}
)
_DELETED_WRAPPERS: Final = frozenset({"del", "moveFrom"})
_HEADING_STYLE_PATTERN: Final = re.compile(
    r"^heading[\s_-]*([1-9])$",
    re.IGNORECASE,
)
_ROUTES: Final = frozenset(
    {
        (
            ".docx",
            "application/vnd.openxmlformats-officedocument."
            "wordprocessingml.document",
        )
    }
)


def _has_meaningful_text(value: str) -> bool:
    """Return whether text contains content beyond whitespace and BOMs."""

    return any(
        not (character.isspace() or character == "\ufeff")
        for character in value
    )


def _has_unsafe_control(value: str) -> bool:
    """Detect controls prohibited by the shared loaded-document contract."""

    return any(
        unicodedata.category(character) == "Cs"
        or (
            unicodedata.category(character) == "Cc"
            and character not in {"\t", "\n", "\r"}
        )
        for character in value
    )


def _local_name(tag: object) -> str:
    """Return an XML local name without trusting a document prefix."""

    if not isinstance(tag, str):
        return ""
    return tag.rsplit("}", 1)[-1]


def _namespace(tag: object) -> str:
    """Return an expanded XML namespace or an empty string."""

    if not isinstance(tag, str) or not tag.startswith("{"):
        return ""
    return tag[1:].split("}", 1)[0]


def _is_namespaced_element(
    element: ElementTree.Element,
    local_name: str,
    namespaces: frozenset[str],
) -> bool:
    """Return whether an element has both an exact name and allowed namespace."""

    return (
        _local_name(element.tag) == local_name
        and _namespace(element.tag) in namespaces
    )


def _attribute(
    element: ElementTree.Element,
    local_name: str,
    namespaces: frozenset[str],
) -> str | None:
    """Read one attribute only when its expanded namespace is allowed."""

    for name, value in element.attrib.items():
        if (
            _local_name(name) == local_name
            and _namespace(name) in namespaces
        ):
            return value
    return None


def _direct_children(
    element: ElementTree.Element,
    local_name: str,
    namespaces: frozenset[str],
) -> Iterator[ElementTree.Element]:
    """Yield direct children with one exact expanded XML name."""

    for child in element:
        if _is_namespaced_element(child, local_name, namespaces):
            yield child


def _first_direct_child(
    element: ElementTree.Element,
    local_name: str,
    namespaces: frozenset[str],
) -> ElementTree.Element | None:
    """Return the first direct child with one exact expanded XML name."""

    return next(_direct_children(element, local_name, namespaces), None)


@dataclass(slots=True)
class _XmlBudget:
    """Track XML elements across every selected package part."""

    limits: DocumentLoadLimits
    elements: int = 0
    mc_tokens: int = 0
    namespace_declarations: int = 0

    def add_element(self) -> None:
        """Consume one element or stop before an XML structure bomb grows."""

        self.elements += 1
        if self.elements > self.limits.max_xml_elements:
            raise DocumentContentLimitError(
                "The DOCX package exceeds the XML element limit."
            )

    def add_mc_tokens(self, count: int) -> None:
        """Bound compatibility directives retained for post-parse selection."""

        self.mc_tokens += count
        if self.mc_tokens > _MAX_MC_TOKENS_TOTAL:
            raise DocumentContentLimitError(
                "The DOCX package exceeds the compatibility-token limit."
            )

    def add_namespace_declarations(self, count: int) -> None:
        """Bound namespace churn that would otherwise amplify XML traversal."""

        self.namespace_declarations += count
        if self.namespace_declarations > _MAX_NAMESPACE_DECLARATIONS:
            raise DocumentContentLimitError(
                "The DOCX package exceeds the namespace-declaration limit."
            )


@dataclass(frozen=True, slots=True)
class _MarkupCompatibilityState:
    """Carry persistent OOXML compatibility deltas without copying ancestors."""

    parent: _MarkupCompatibilityState | None = None
    ignorable_namespaces: frozenset[str] = frozenset()
    process_content: frozenset[tuple[str, str]] = frozenset()


def _is_ignorable_namespace(
    state: _MarkupCompatibilityState,
    namespace: str,
) -> bool:
    """Find an inherited ignorable namespace through persistent state deltas."""

    current: _MarkupCompatibilityState | None = state
    while current is not None:
        if namespace in current.ignorable_namespaces:
            return True
        current = current.parent
    return False


def _has_process_content_rule(
    state: _MarkupCompatibilityState,
    expanded_name: tuple[str, str],
) -> bool:
    """Find one exact inherited ProcessContent rule without flattening state."""

    current: _MarkupCompatibilityState | None = state
    while current is not None:
        if expanded_name in current.process_content:
            return True
        current = current.parent
    return False


@dataclass(frozen=True, slots=True)
class _MarkupProfile:
    """Describe XML namespaces understood in one selected OPC part."""

    element_namespaces: frozenset[str]
    attribute_namespaces: frozenset[str]
    opaque_element_namespaces: frozenset[str] = frozenset()


_CONTENT_TYPES_MARKUP_PROFILE: Final = _MarkupProfile(
    element_namespaces=_CONTENT_TYPE_NAMESPACES,
    attribute_namespaces=frozenset(
        {*_CONTENT_TYPE_NAMESPACES, _XML_NAMESPACE}
    ),
)
_PACKAGE_RELATIONSHIPS_MARKUP_PROFILE: Final = _MarkupProfile(
    element_namespaces=_PACKAGE_RELATIONSHIP_NAMESPACES,
    attribute_namespaces=frozenset(
        {*_PACKAGE_RELATIONSHIP_NAMESPACES, _XML_NAMESPACE}
    ),
)
_WORDPROCESSING_MARKUP_PROFILE: Final = _MarkupProfile(
    element_namespaces=_WORDPROCESSING_NAMESPACES,
    attribute_namespaces=frozenset(
        {
            *_WORDPROCESSING_NAMESPACES,
            *_OFFICE_DOCUMENT_RELATIONSHIP_NAMESPACES,
            _XML_NAMESPACE,
        }
    ),
    opaque_element_namespaces=_WORD_OPAQUE_CONTENT_NAMESPACES,
)
_CORE_PROPERTIES_MARKUP_PROFILE: Final = _MarkupProfile(
    element_namespaces=frozenset(
        {
            *_CORE_PROPERTIES_NAMESPACES,
            *_DUBLIN_CORE_NAMESPACES,
            *_DUBLIN_CORE_TERMS_NAMESPACES,
        }
    ),
    attribute_namespaces=frozenset(
        {
            *_CORE_PROPERTIES_NAMESPACES,
            *_DUBLIN_CORE_NAMESPACES,
            *_DUBLIN_CORE_TERMS_NAMESPACES,
            *_XML_SCHEMA_INSTANCE_NAMESPACES,
            _XML_NAMESPACE,
        }
    ),
)


def _markup_attribute(
    element: ElementTree.Element,
    local_name: str,
) -> str | None:
    """Read one exact Markup Compatibility attribute."""

    return element.attrib.get(
        f"{{{_MARKUP_COMPATIBILITY_NAMESPACE}}}{local_name}"
    )


def _xml_whitespace_tokens(
    raw_value: str,
    *,
    attribute_name: str,
) -> tuple[str, ...]:
    """Split one MC list on XML whitespace under fixed resource limits.

    Python's generic ``str.split`` accepts many Unicode separators that XML
    does not.  A small explicit scanner preserves the standard's four
    whitespace characters and stops oversized attacker-controlled lists before
    they can create an unbounded number of temporary strings.
    """

    if len(raw_value) > _MAX_MC_DIRECTIVE_CODE_POINTS:
        raise DocumentContentLimitError(
            f"The DOCX package exceeds the mc:{attribute_name} length limit."
        )
    tokens: list[str] = []
    start: int | None = None
    for index, character in enumerate(raw_value):
        if character in _XML_WHITESPACE:
            if start is not None:
                tokens.append(raw_value[start:index])
                start = None
        elif start is None:
            start = index
        if len(tokens) > _MAX_MC_TOKENS_PER_DIRECTIVE:
            raise DocumentContentLimitError(
                f"The DOCX package exceeds the mc:{attribute_name} token limit."
            )
    if start is not None:
        tokens.append(raw_value[start:])
    if len(tokens) > _MAX_MC_TOKENS_PER_DIRECTIVE:
        raise DocumentContentLimitError(
            f"The DOCX package exceeds the mc:{attribute_name} token limit."
        )
    return tuple(tokens)


def _is_ncname_start(character: str) -> bool:
    """Return whether one code point may begin an XML 1.0 NCName."""

    code_point = ord(character)
    return (
        character == "_"
        or "A" <= character <= "Z"
        or "a" <= character <= "z"
        or 0xC0 <= code_point <= 0xD6
        or 0xD8 <= code_point <= 0xF6
        or 0xF8 <= code_point <= 0x2FF
        or 0x370 <= code_point <= 0x37D
        or 0x37F <= code_point <= 0x1FFF
        or 0x200C <= code_point <= 0x200D
        or 0x2070 <= code_point <= 0x218F
        or 0x2C00 <= code_point <= 0x2FEF
        or 0x3001 <= code_point <= 0xD7FF
        or 0xF900 <= code_point <= 0xFDCF
        or 0xFDF0 <= code_point <= 0xFFFD
        or 0x10000 <= code_point <= 0xEFFFF
    )


def _is_ncname(value: str) -> bool:
    """Return whether a token is an XML 1.0 NCName without a colon."""

    if not value or not _is_ncname_start(value[0]):
        return False
    return all(
        _is_ncname_start(character)
        or character in {"-", "."}
        or "0" <= character <= "9"
        or ord(character) == 0xB7
        or 0x300 <= ord(character) <= 0x36F
        or 0x203F <= ord(character) <= 0x2040
        for character in value[1:]
    )


def _mc_prefix_references(
    element: ElementTree.Element,
    budget: _XmlBudget,
) -> frozenset[str]:
    """Collect only prefix bindings needed by one MC-bearing element.

    Prefix-list syntax is validated later, after AlternateContent selection,
    so malformed directives inside an unselected subtree stay semantically
    inert.  Raw lengths and token counts are still charged during parsing to
    keep every branch bounded before its selection is known.
    """

    for name in element.attrib:
        if (
            _namespace(name) == _MARKUP_COMPATIBILITY_NAMESPACE
            and _local_name(name) not in _MARKUP_COMPATIBILITY_ATTRIBUTES
        ):
            raise DocumentCorruptError(
                "The DOCX package contains an unknown compatibility attribute."
            )

    references: set[str] = set()
    for attribute_name in (
        "Ignorable",
        "MustUnderstand",
        "ProcessContent",
    ):
        raw_value = _markup_attribute(element, attribute_name)
        if raw_value is None:
            continue
        tokens = _xml_whitespace_tokens(
            raw_value,
            attribute_name=attribute_name,
        )
        budget.add_mc_tokens(len(tokens))
        if attribute_name == "ProcessContent":
            references.update(token.split(":", 1)[0] for token in tokens)
        else:
            references.update(tokens)

    if (
        _namespace(element.tag) == _MARKUP_COMPATIBILITY_NAMESPACE
        and _local_name(element.tag) == "Choice"
    ):
        raw_requires = element.attrib.get("Requires")
        if raw_requires is not None:
            tokens = _xml_whitespace_tokens(
                raw_requires,
                attribute_name="Choice Requires",
            )
            budget.add_mc_tokens(len(tokens))
            references.update(tokens)
    return frozenset(references)


def _resolve_prefixes(
    raw_value: str,
    namespaces: dict[str, str],
    *,
    attribute_name: str,
    require_non_empty: bool = False,
) -> tuple[str, ...]:
    """Resolve one MC prefix list and reject the MC namespace itself."""

    prefixes = _xml_whitespace_tokens(
        raw_value,
        attribute_name=attribute_name,
    )
    if (
        (require_non_empty and not prefixes)
        or any(not _is_ncname(prefix) for prefix in prefixes)
    ):
        raise DocumentCorruptError(
            f"The DOCX package contains an invalid mc:{attribute_name} value."
        )
    resolved: list[str] = []
    for prefix in prefixes:
        namespace = namespaces.get(prefix)
        if not namespace or namespace == _MARKUP_COMPATIBILITY_NAMESPACE:
            raise DocumentCorruptError(
                f"The DOCX package contains an unresolved mc:{attribute_name} prefix."
            )
        resolved.append(namespace)
    return tuple(resolved)


def _resolve_process_content(
    raw_value: str,
    namespaces: dict[str, str],
) -> tuple[tuple[str, str], ...]:
    """Resolve scoped ``mc:ProcessContent`` qualified element names."""

    tokens = _xml_whitespace_tokens(
        raw_value,
        attribute_name="ProcessContent",
    )
    resolved: list[tuple[str, str]] = []
    for token in tokens:
        if token.count(":") != 1:
            raise DocumentCorruptError(
                "The DOCX package contains an invalid mc:ProcessContent value."
            )
        prefix, local_name = token.split(":", 1)
        namespace = namespaces.get(prefix)
        if (
            not _is_ncname(prefix)
            or (local_name != "*" and not _is_ncname(local_name))
            or not namespace
            or namespace == _MARKUP_COMPATIBILITY_NAMESPACE
        ):
            raise DocumentCorruptError(
                "The DOCX package contains an unresolved mc:ProcessContent name."
            )
        resolved.append((namespace, local_name))
    return tuple(resolved)


def _markup_state_for_element(
    element: ElementTree.Element,
    parent: _MarkupCompatibilityState,
    namespaces: dict[str, str],
) -> _MarkupCompatibilityState:
    """Apply only new inherited rules as a persistent state delta."""

    raw_ignorable = _markup_attribute(element, "Ignorable")
    raw_process_content = _markup_attribute(element, "ProcessContent")
    if raw_ignorable is None and raw_process_content is None:
        # Most OOXML elements carry no compatibility state.  Reusing the
        # immutable parent avoids copying inherited sets at every tree depth.
        return parent

    resolved_ignorable = (
        ()
        if raw_ignorable is None
        else _resolve_prefixes(
            raw_ignorable,
            namespaces,
            attribute_name="Ignorable",
        )
    )
    ignorable_delta = frozenset(
        namespace
        for namespace in resolved_ignorable
        if not _is_ignorable_namespace(parent, namespace)
    )

    resolved_names: tuple[tuple[str, str], ...] = ()
    if raw_process_content is not None:
        resolved_names = _resolve_process_content(
            raw_process_content,
            namespaces,
        )
        if any(
            namespace not in ignorable_delta
            and not _is_ignorable_namespace(parent, namespace)
            for namespace, _ in resolved_names
        ):
            raise DocumentCorruptError(
                "The DOCX package processes a namespace that is not ignorable."
            )
    process_delta = frozenset(
        expanded_name
        for expanded_name in resolved_names
        if not _has_process_content_rule(parent, expanded_name)
    )
    if not ignorable_delta and not process_delta:
        return parent
    return _MarkupCompatibilityState(
        parent=parent,
        ignorable_namespaces=ignorable_delta,
        process_content=process_delta,
    )


def _must_understand_namespaces(
    element: ElementTree.Element,
    namespaces: dict[str, str],
) -> tuple[str, ...]:
    """Resolve one MustUnderstand list without applying runtime support."""

    raw_value = _markup_attribute(element, "MustUnderstand")
    if raw_value is None:
        return ()
    return _resolve_prefixes(
        raw_value,
        namespaces,
        attribute_name="MustUnderstand",
    )


def _validate_must_understand(
    element: ElementTree.Element,
    namespaces: dict[str, str],
    profile: _MarkupProfile,
) -> None:
    """Reject an active element that requires an unsupported namespace."""

    required_namespaces = _must_understand_namespaces(element, namespaces)
    if any(
        namespace not in profile.element_namespaces
        for namespace in required_namespaces
    ):
        raise DocumentUnsupportedFeatureError(
            "The DOCX package requires an unsupported XML namespace."
        )


def _choice_is_supported(
    element: ElementTree.Element,
    namespaces: dict[str, str],
    profile: _MarkupProfile,
) -> bool:
    """Return whether every namespace required by one MC choice is understood."""

    raw_requires = element.attrib.get("Requires")
    if raw_requires is None:
        raise DocumentCorruptError(
            "The DOCX package contains an mc:Choice without Requires."
        )
    return all(
        namespace in profile.element_namespaces
        for namespace in _resolve_prefixes(
            raw_requires,
            namespaces,
            attribute_name="Choice Requires",
            require_non_empty=True,
        )
    )


def _process_content_matches(
    element: ElementTree.Element,
    state: _MarkupCompatibilityState,
) -> bool:
    """Return whether an ignored wrapper's children must remain processable."""

    expanded_name = (_namespace(element.tag), _local_name(element.tag))
    return _has_process_content_rule(
        state,
        expanded_name,
    ) or _has_process_content_rule(state, (expanded_name[0], "*"))


def _validate_process_content_wrapper(element: ElementTree.Element) -> None:
    """Reject XML state that cannot survive removal of an ignored wrapper."""

    if any(
        _namespace(name) == _XML_NAMESPACE
        and _local_name(name) in {"base", "lang", "space"}
        for name in element.attrib
    ):
        raise DocumentCorruptError(
            "The DOCX package cannot unwrap an XML-scoped extension element."
        )


def _namespace_context_for(
    element: ElementTree.Element,
    contexts: dict[int, dict[str, str]],
) -> dict[str, str]:
    """Return the captured in-scope prefixes for one MC-bearing element."""

    return contexts.get(id(element), {})


def _validate_branch_attributes(
    element: ElementTree.Element,
    state: _MarkupCompatibilityState,
    *,
    choice: bool,
) -> None:
    """Validate the attributes that may appear on an MC branch wrapper."""

    for name in element.attrib:
        namespace = _namespace(name)
        local_name = _local_name(name)
        if not namespace:
            if choice and local_name == "Requires":
                continue
            raise DocumentCorruptError(
                "The DOCX package contains an invalid MC branch attribute."
            )
        if namespace == _XML_NAMESPACE:
            raise DocumentCorruptError(
                "The DOCX package contains an XML attribute on an MC element."
            )
        if namespace == _MARKUP_COMPATIBILITY_NAMESPACE:
            if local_name not in _MARKUP_COMPATIBILITY_ATTRIBUTES:
                raise DocumentCorruptError(
                    "The DOCX package contains an unknown MC branch attribute."
                )
            continue
        if not _is_ignorable_namespace(state, namespace):
            raise DocumentCorruptError(
                "The DOCX package contains an unsupported MC branch attribute."
            )


def _validate_alternate_content_attributes(
    element: ElementTree.Element,
    state: _MarkupCompatibilityState,
) -> None:
    """Validate attributes whose wrapper disappears after branch selection."""

    _validate_process_content_wrapper(element)
    for name in element.attrib:
        namespace = _namespace(name)
        local_name = _local_name(name)
        if not namespace:
            raise DocumentCorruptError(
                "The DOCX package contains an unqualified AlternateContent attribute."
            )
        if namespace == _XML_NAMESPACE:
            raise DocumentCorruptError(
                "The DOCX package contains an XML attribute on an MC element."
            )
        if namespace == _MARKUP_COMPATIBILITY_NAMESPACE:
            if local_name not in _MARKUP_COMPATIBILITY_ATTRIBUTES:
                raise DocumentCorruptError(
                    "The DOCX package contains an unknown AlternateContent attribute."
                )
            continue
        if not _is_ignorable_namespace(state, namespace):
            raise DocumentCorruptError(
                "The DOCX package contains an unsupported AlternateContent attribute."
            )


def _validate_active_attributes(
    element: ElementTree.Element,
    state: _MarkupCompatibilityState,
    profile: _MarkupProfile,
) -> None:
    """Reject active attributes from namespaces outside the loader profile."""

    for name in element.attrib:
        namespace = _namespace(name)
        local_name = _local_name(name)
        if (
            namespace == _MARKUP_COMPATIBILITY_NAMESPACE
            and local_name not in _MARKUP_COMPATIBILITY_ATTRIBUTES
        ):
            raise DocumentCorruptError(
                "The DOCX package contains an unknown compatibility attribute."
            )
        if (
            not namespace
            or namespace in profile.attribute_namespaces
            or namespace == _MARKUP_COMPATIBILITY_NAMESPACE
            or _is_ignorable_namespace(state, namespace)
        ):
            continue
        raise DocumentUnsupportedFeatureError(
            "The DOCX package contains an unsupported XML attribute namespace."
        )


def _preprocess_markup_element(
    element: ElementTree.Element,
    parent_state: _MarkupCompatibilityState,
    contexts: dict[int, dict[str, str]],
    profile: _MarkupProfile,
) -> list[ElementTree.Element]:
    """Return the active replacement for one element under OOXML MC rules."""

    namespaces = _namespace_context_for(element, contexts)
    state = _markup_state_for_element(element, parent_state, namespaces)
    namespace = _namespace(element.tag)
    local_name = _local_name(element.tag)

    if namespace == _MARKUP_COMPATIBILITY_NAMESPACE:
        _validate_must_understand(element, namespaces, profile)
        if local_name == "AlternateContent":
            _validate_alternate_content_attributes(element, state)
            return _preprocess_alternate_content(
                element,
                state,
                contexts,
                profile,
            )
        raise DocumentCorruptError(
            "The DOCX package contains a misplaced MC branch."
        )

    if (
        namespace not in profile.element_namespaces
        and _is_ignorable_namespace(state, namespace)
    ):
        if not _process_content_matches(element, state):
            # A fully ignored wrapper suppresses runtime support mismatch, but
            # its prefix list remains lexical XML and must still resolve.
            _must_understand_namespaces(element, namespaces)
            # An ignored wrapper's descendants are semantically nonexistent;
            # notably, MustUnderstand inside an unprocessed subtree cannot fail
            # the active document.
            return []
        # ProcessContent keeps the wrapper's children active, so the selected
        # processing path must honor its mandatory capability declaration.
        _validate_must_understand(element, namespaces, profile)
        _validate_process_content_wrapper(element)
        return _preprocess_markup_children(
            element,
            state,
            contexts,
            profile,
        )

    if namespace in profile.opaque_element_namespaces:
        # MCE rules take precedence above: an explicitly ProcessContent
        # wrapper exposes its children, while an ordinary Ignorable wrapper is
        # discarded.  Only an active opaque vocabulary reaches this branch.
        # Drawing, picture, legacy shape, and Office Math subtrees are then
        # dropped without claiming support for Choice or MustUnderstand.
        _validate_must_understand(element, namespaces, profile)
        return []

    if namespace not in profile.element_namespaces:
        raise DocumentUnsupportedFeatureError(
            "The DOCX package contains an unsupported XML element namespace."
        )
    _validate_must_understand(element, namespaces, profile)
    _validate_active_attributes(element, state, profile)
    element[:] = _preprocess_markup_children(
        element,
        state,
        contexts,
        profile,
    )
    return [element]


def _preprocess_markup_children(
    element: ElementTree.Element,
    state: _MarkupCompatibilityState,
    contexts: dict[int, dict[str, str]],
    profile: _MarkupProfile,
) -> list[ElementTree.Element]:
    """Flatten each active child replacement while preserving source order."""

    children: list[ElementTree.Element] = []
    for child in element:
        children.extend(
            _preprocess_markup_element(child, state, contexts, profile)
        )
    return children


def _alternate_branch_nodes(
    element: ElementTree.Element,
    state: _MarkupCompatibilityState,
    contexts: dict[int, dict[str, str]],
    profile: _MarkupProfile,
) -> list[tuple[ElementTree.Element, _MarkupCompatibilityState]]:
    """Collect Choice/Fallback wrappers after removing ignorable extensions."""

    branches: list[tuple[ElementTree.Element, _MarkupCompatibilityState]] = []
    for child in element:
        namespaces = _namespace_context_for(child, contexts)
        child_state = _markup_state_for_element(child, state, namespaces)
        namespace = _namespace(child.tag)
        local_name = _local_name(child.tag)
        if (
            namespace == _MARKUP_COMPATIBILITY_NAMESPACE
            and local_name in {"Choice", "Fallback"}
        ):
            # Every wrapper must be syntactically valid even though the 2015
            # processing model applies the support mismatch only to the branch
            # that is ultimately selected.
            _must_understand_namespaces(child, namespaces)
            _validate_branch_attributes(
                child,
                child_state,
                choice=local_name == "Choice",
            )
            branches.append((child, child_state))
            continue
        if (
            namespace not in profile.element_namespaces
            and _is_ignorable_namespace(child_state, namespace)
        ):
            _must_understand_namespaces(child, namespaces)
            if _process_content_matches(child, child_state):
                raise DocumentCorruptError(
                    "The DOCX package unwraps a direct AlternateContent child."
                )
            continue
        raise DocumentCorruptError(
            "The DOCX package contains malformed mc:AlternateContent."
        )
    return branches


def _preprocess_alternate_content(
    element: ElementTree.Element,
    state: _MarkupCompatibilityState,
    contexts: dict[int, dict[str, str]],
    profile: _MarkupProfile,
) -> list[ElementTree.Element]:
    """Select the first understood Choice or the optional Fallback branch."""

    selected: tuple[ElementTree.Element, _MarkupCompatibilityState] | None = None
    fallback: tuple[ElementTree.Element, _MarkupCompatibilityState] | None = None
    choices = 0
    fallback_seen = False
    for child, child_state in _alternate_branch_nodes(
        element,
        state,
        contexts,
        profile,
    ):
        local_name = _local_name(child.tag)
        if local_name == "Choice":
            if fallback_seen:
                raise DocumentCorruptError(
                    "The DOCX package contains malformed mc:AlternateContent."
                )
            choices += 1
            supported = _choice_is_supported(
                child,
                _namespace_context_for(child, contexts),
                profile,
            )
            if selected is None and supported:
                selected = (child, child_state)
        else:
            if fallback is not None:
                raise DocumentCorruptError(
                    "The DOCX package contains duplicate mc:Fallback branches."
                )
            fallback = (child, child_state)
            fallback_seen = True
    if choices == 0:
        raise DocumentCorruptError(
            "The DOCX package contains mc:AlternateContent without a Choice."
        )
    chosen = selected if selected is not None else fallback
    if chosen is None:
        return []
    branch, branch_state = chosen
    _validate_must_understand(
        branch,
        _namespace_context_for(branch, contexts),
        profile,
    )
    # Only the selected branch's descendants exist semantically.  Raw XML
    # budgets were already charged during parsing, but ignored alternatives do
    # not get a chance to trigger MustUnderstand or expose hidden text.
    return _preprocess_markup_children(
        branch,
        branch_state,
        contexts,
        profile,
    )


@dataclass(slots=True)
class _OutputBudget:
    """Track published blocks, text, and table cells while parsing."""

    limits: DocumentLoadLimits
    text_code_points: int = 0
    table_cells: int = 0

    def add_text(self, text: str) -> None:
        """Consume extracted Unicode against the aggregate output budget."""

        if _has_unsafe_control(text):
            raise DocumentCorruptError(
                "The DOCX document contains invalid extracted text."
            )
        self.text_code_points += len(text)
        if self.text_code_points > self.limits.max_text_code_points:
            raise DocumentContentLimitError(
                "The DOCX document exceeds the text limit."
            )

    def start_cell(self) -> None:
        """Consume one table cell before any of its text is materialized."""

        self.table_cells += 1
        if self.table_cells > self.limits.max_table_cells:
            raise DocumentContentLimitError(
                "The DOCX document exceeds the table-cell limit."
            )

    def add_cell_fragment(self, text: str, cell_code_points: int) -> int:
        """Consume one cell fragment incrementally and return its new length."""

        updated_cell_code_points = cell_code_points + len(text)
        if updated_cell_code_points > self.limits.max_cell_code_points:
            raise DocumentContentLimitError(
                "A DOCX table cell exceeds the text limit."
            )
        self.add_text(text)
        return updated_cell_code_points


def _safe_xml_root(
    payload: bytes,
    budget: _XmlBudget,
    profile: _MarkupProfile,
) -> ElementTree.Element:
    """Parse bounded XML while rejecting DTD/entity expansion and deep trees."""

    if not payload:
        raise DocumentCorruptError("A required DOCX XML part is empty.")
    if len(payload) > _MAX_XML_PART_BYTES:
        raise DocumentContentLimitError(
            "A DOCX XML part exceeds the safe byte limit."
        )
    # OOXML does not require a DTD.  Checking both the raw and NUL-stripped
    # spellings also catches UTF-16 declarations before an XML parser can
    # allocate expanded entity text.
    folded = payload.upper()
    compact_folded = folded.replace(b"\x00", b"")
    if any(
        marker in folded or marker in compact_folded
        for marker in (b"<!DOCTYPE", b"<!ENTITY")
    ):
        raise DocumentCorruptError(
            "DOCX XML declarations are not permitted."
        )

    root: ElementTree.Element | None = None
    depth = 0
    active_namespaces: dict[str, str] = {"xml": _XML_NAMESPACE}
    pending_namespaces: list[tuple[str, str]] = []
    namespace_undo_stack: list[list[tuple[str, str, bool]]] = []
    markup_contexts: dict[int, dict[str, str]] = {}
    try:
        event_stream = ElementTree.iterparse(
            BytesIO(payload),
            events=("start-ns", "start", "end"),
        )
        for event, event_value in event_stream:
            if event == "start-ns":
                prefix, namespace = cast(tuple[str, str], event_value)
                pending_namespaces.append((prefix or "", namespace))
                continue

            element = cast(ElementTree.Element, event_value)
            if event == "start":
                budget.add_namespace_declarations(len(pending_namespaces))
                namespace_undo: list[tuple[str, str, bool]] = []
                for prefix, namespace in pending_namespaces:
                    had_previous = prefix in active_namespaces
                    namespace_undo.append(
                        (
                            prefix,
                            active_namespaces.get(prefix, ""),
                            had_previous,
                        )
                    )
                    active_namespaces[prefix] = namespace
                pending_namespaces.clear()
                namespace_undo_stack.append(namespace_undo)
                if len(active_namespaces) > _MAX_NAMESPACE_BINDINGS:
                    raise DocumentContentLimitError(
                        "The DOCX package exceeds the in-scope namespace limit."
                    )
                referenced_prefixes = _mc_prefix_references(element, budget)
                if referenced_prefixes:
                    # Retaining only prefixes mentioned by this element avoids
                    # copying the full in-scope namespace map at every node.
                    markup_contexts[id(element)] = {
                        prefix: active_namespaces.get(prefix, "")
                        for prefix in referenced_prefixes
                    }
                depth += 1
                if depth > budget.limits.max_xml_depth:
                    raise DocumentContentLimitError(
                        "The DOCX package exceeds the XML depth limit."
                    )
                budget.add_element()
                if root is None:
                    root = cast(ElementTree.Element, element)
            else:
                for prefix, previous, had_previous in reversed(
                    namespace_undo_stack.pop()
                ):
                    if had_previous:
                        active_namespaces[prefix] = previous
                    else:
                        del active_namespaces[prefix]
                depth -= 1
    except (ElementTree.ParseError, LookupError, UnicodeError, ValueError) as error:
        raise DocumentCorruptError(
            "The DOCX package contains malformed or undecodable XML."
        ) from error
    if root is None or depth != 0:
        raise DocumentCorruptError(
            "The DOCX package contains incomplete XML."
        )
    replacements = _preprocess_markup_element(
        root,
        _MarkupCompatibilityState(),
        markup_contexts,
        profile,
    )
    if len(replacements) != 1:
        raise DocumentCorruptError(
            "The DOCX package has an invalid compatibility root."
        )
    return replacements[0]


def _validate_member_name(name: str) -> str:
    """Return one canonical OPC member name or reject archive ambiguity."""

    if (
        not name
        or "\x00" in name
        or "\\" in name
        or name.startswith("/")
        or "//" in name
    ):
        raise DocumentCorruptError(
            "The DOCX package contains an unsafe member name."
        )
    components = name.split("/")
    if any(component in {"", ".", ".."} for component in components):
        raise DocumentCorruptError(
            "The DOCX package contains an unsafe member name."
        )
    return name


def _preflight_central_directory(
    data: bytes,
    limits: DocumentLoadLimits,
) -> None:
    """Bound ZIP metadata before ``ZipFile`` materializes every ``ZipInfo``.

    ``ZipFile`` eagerly parses the complete central directory in its
    constructor, so checking ``infolist()`` afterward is too late to prevent a
    many-entry memory spike.  This fixed-size EOCD/Zip64 read validates the
    declared count and a conservative directory-byte budget first.  The later
    per-entry walk remains mandatory because declarations can be forged.
    """

    eocd_search_start = max(
        0,
        len(data) - (65_535 + _ZIP_END_STRUCT.size),
    )
    eocd_offset = data.rfind(
        _ZIP_END_OF_CENTRAL_DIRECTORY,
        eocd_search_start,
    )
    if eocd_offset < 0 or eocd_offset + _ZIP_END_STRUCT.size > len(data):
        raise DocumentCorruptError(
            "The DOCX package has no valid central-directory terminator."
        )
    (
        signature,
        disk_number,
        central_disk,
        disk_entries,
        total_entries,
        central_size,
        central_offset,
        comment_size,
    ) = _ZIP_END_STRUCT.unpack_from(data, eocd_offset)
    if (
        signature != _ZIP_END_OF_CENTRAL_DIRECTORY
        or eocd_offset + _ZIP_END_STRUCT.size + comment_size != len(data)
        or disk_number != 0
        or central_disk != 0
        or disk_entries != total_entries
    ):
        raise DocumentCorruptError(
            "The DOCX package has invalid central-directory metadata."
        )

    trailer_offset = eocd_offset
    zip64_required = (
        total_entries == 0xFFFF
        or central_size == 0xFFFFFFFF
        or central_offset == 0xFFFFFFFF
    )
    if zip64_required:
        locator_offset = eocd_offset - _ZIP64_LOCATOR_STRUCT.size
        if locator_offset < 0:
            raise DocumentCorruptError(
                "The DOCX package has invalid Zip64 metadata."
            )
        (
            locator_signature,
            zip64_disk,
            zip64_offset,
            total_disks,
        ) = _ZIP64_LOCATOR_STRUCT.unpack_from(data, locator_offset)
        if (
            locator_signature != _ZIP64_END_OF_CENTRAL_DIRECTORY_LOCATOR
            or zip64_disk != 0
            or total_disks != 1
            or zip64_offset + _ZIP64_END_STRUCT.size > locator_offset
        ):
            raise DocumentCorruptError(
                "The DOCX package has invalid Zip64 metadata."
            )
        (
            zip64_signature,
            zip64_record_size,
            _version_made,
            _version_needed,
            zip64_disk_number,
            zip64_central_disk,
            zip64_disk_entries,
            zip64_total_entries,
            zip64_central_size,
            zip64_central_offset,
        ) = _ZIP64_END_STRUCT.unpack_from(data, zip64_offset)
        if (
            zip64_signature != _ZIP64_END_OF_CENTRAL_DIRECTORY
            or zip64_record_size < 44
            or zip64_record_size > _MAX_ZIP64_END_RECORD_BYTES
            or zip64_offset + 12 + zip64_record_size != locator_offset
            or zip64_disk_number != 0
            or zip64_central_disk != 0
            or zip64_disk_entries != zip64_total_entries
        ):
            raise DocumentCorruptError(
                "The DOCX package has invalid Zip64 metadata."
            )
        total_entries = zip64_total_entries
        central_size = zip64_central_size
        central_offset = zip64_central_offset
        trailer_offset = zip64_offset

    if total_entries == 0:
        raise DocumentEmptyError("The DOCX package contains no parts.")
    if total_entries > limits.max_package_entries:
        raise DocumentContentLimitError(
            "The DOCX package exceeds the entry-count limit."
        )
    minimum_central_size = total_entries * 46
    if central_size < minimum_central_size:
        raise DocumentCorruptError(
            "The DOCX package has inconsistent central-directory metadata."
        )
    central_budget = min(
        limits.max_source_bytes,
        _MAX_CENTRAL_DIRECTORY_BYTES,
        total_entries * _MAX_CENTRAL_DIRECTORY_BYTES_PER_ENTRY,
    )
    if central_size > central_budget:
        raise DocumentContentLimitError(
            "The DOCX package central directory exceeds the safe byte limit."
        )
    if (
        central_offset < len(_ZIP_LOCAL_HEADER)
        or central_offset + central_size != trailer_offset
    ):
        raise DocumentCorruptError(
            "The DOCX package has inconsistent central-directory metadata."
        )

    # Do not trust the EOCD count: an attacker can forge it downward while
    # leaving tens of thousands of real records for ``ZipFile`` to allocate.
    # Walking fixed-size record headers here is allocation-free and stops at
    # limit + 1, before the standard library constructor sees the archive.
    cursor = central_offset
    central_end = central_offset + central_size
    actual_entries = 0
    while cursor < central_end:
        if cursor + _ZIP_CENTRAL_STRUCT.size > central_end:
            raise DocumentCorruptError(
                "The DOCX package has a truncated central directory."
            )
        central_record = _ZIP_CENTRAL_STRUCT.unpack_from(data, cursor)
        if central_record[0] != _ZIP_CENTRAL_HEADER:
            raise DocumentCorruptError(
                "The DOCX package has an invalid central-directory record."
            )
        name_size = central_record[10]
        extra_size = central_record[11]
        entry_comment_size = central_record[12]
        disk_start = central_record[13]
        if disk_start != 0:
            raise DocumentCorruptError(
                "Multi-disk DOCX packages are not supported."
            )
        record_size = (
            _ZIP_CENTRAL_STRUCT.size
            + name_size
            + extra_size
            + entry_comment_size
        )
        cursor += record_size
        if cursor > central_end:
            raise DocumentCorruptError(
                "The DOCX package has a truncated central-directory record."
            )
        actual_entries += 1
        if actual_entries > limits.max_package_entries:
            raise DocumentContentLimitError(
                "The DOCX package exceeds the entry-count limit."
            )
    if cursor != central_end or actual_entries != total_entries:
        raise DocumentCorruptError(
            "The DOCX package has inconsistent central-directory metadata."
        )


def _validate_archive(
    archive: ZipFile,
    limits: DocumentLoadLimits,
) -> dict[str, ZipInfo]:
    """Validate the complete central directory before opening any member."""

    entries = archive.infolist()
    if not entries:
        raise DocumentEmptyError("The DOCX package contains no parts.")
    if len(entries) > limits.max_package_entries:
        raise DocumentContentLimitError(
            "The DOCX package exceeds the entry-count limit."
        )

    members: dict[str, ZipInfo] = {}
    folded_names: set[str] = set()
    expanded_bytes = 0
    member_limit = min(limits.max_expanded_bytes, _MAX_MEMBER_BYTES)
    for entry in entries:
        # ``zipfile`` normalizes backslashes to forward slashes on Windows.
        # Validate the original spelling first so a platform-dependent alias
        # cannot become indistinguishable from a canonical OPC part name.
        original_name = entry.orig_filename.rstrip("/")
        name = _validate_member_name(original_name)
        if name != entry.filename.rstrip("/"):
            raise DocumentCorruptError(
                "The DOCX package contains an ambiguous member name."
            )
        folded_name = name.casefold()
        if folded_name in folded_names:
            raise DocumentCorruptError(
                "The DOCX package contains duplicate member names."
            )
        folded_names.add(folded_name)
        if entry.flag_bits & 0x1:
            raise DocumentEncryptedError(
                "Encrypted DOCX packages are not supported."
            )
        if entry.compress_type not in {ZIP_STORED, ZIP_DEFLATED}:
            raise DocumentCorruptError(
                "The DOCX package uses an unsupported compression method."
            )
        unix_mode = (entry.external_attr >> 16) & 0xFFFF
        if entry.create_system == 3 and stat.S_ISLNK(unix_mode):
            raise DocumentCorruptError(
                "The DOCX package contains an unsafe linked member."
            )
        if entry.file_size < 0 or entry.compress_size < 0:
            raise DocumentCorruptError(
                "The DOCX package contains invalid member sizes."
            )
        if entry.file_size > member_limit:
            raise DocumentContentLimitError(
                "A DOCX package member exceeds the expansion limit."
            )
        expanded_bytes += entry.file_size
        if expanded_bytes > limits.max_expanded_bytes:
            raise DocumentContentLimitError(
                "The DOCX package exceeds the expansion limit."
            )
        if (
            entry.file_size > 0
            and entry.file_size
            > max(1, entry.compress_size) * _MAX_COMPRESSION_RATIO
        ):
            raise DocumentContentLimitError(
                "A DOCX package member exceeds the compression-ratio limit."
            )
        if not entry.is_dir():
            members[name] = entry
    return members


def _read_member(
    archive: ZipFile,
    members: dict[str, ZipInfo],
    name: str,
    limits: DocumentLoadLimits,
    *,
    required: bool,
) -> bytes | None:
    """Read one selected member with an actual decompression byte ceiling."""

    entry = members.get(name)
    if entry is None:
        if required:
            raise DocumentCorruptError(
                "The DOCX package is missing a required part."
            )
        return None
    read_limit = min(
        entry.file_size,
        limits.max_expanded_bytes,
        _MAX_XML_PART_BYTES,
    )
    with archive.open(entry, "r") as stream:
        payload = stream.read(read_limit + 1)
        if len(payload) > read_limit or stream.read(1):
            raise DocumentContentLimitError(
                "A DOCX package member exceeds the actual-read limit."
            )
    if len(payload) != entry.file_size:
        raise DocumentCorruptError(
            "A DOCX package member has inconsistent size metadata."
        )
    return payload


def _relationship_part(
    root: ElementTree.Element,
    relationship_types: frozenset[str],
    *,
    source_part: str | None,
    required: bool,
) -> str | None:
    """Resolve one unique internal OPC relationship to a canonical part name."""

    if not _is_namespaced_element(
        root,
        "Relationships",
        _PACKAGE_RELATIONSHIP_NAMESPACES,
    ):
        raise DocumentCorruptError(
            "The DOCX package relationships root is invalid."
        )
    target: str | None = None
    for relationship in _direct_children(
        root,
        "Relationship",
        _PACKAGE_RELATIONSHIP_NAMESPACES,
    ):
        relation_type = relationship.attrib.get("Type", "")
        if relation_type not in relationship_types:
            continue
        if relationship.attrib.get("TargetMode", "Internal") != "Internal":
            raise DocumentCorruptError(
                "A selected DOCX part cannot use an external relationship."
            )
        if target is not None:
            raise DocumentCorruptError(
                "The DOCX package has duplicate selected-part relationships."
            )
        target = relationship.attrib.get("Target")
    if target is None:
        if required:
            raise DocumentCorruptError(
                "The DOCX package is missing a required part relationship."
            )
        return None

    decoded = unquote(target)
    parsed = urlsplit(decoded)
    if parsed.scheme or parsed.netloc or parsed.query or parsed.fragment:
        raise DocumentCorruptError(
            "The DOCX selected-part relationship is invalid."
        )
    if "\\" in parsed.path or "\x00" in parsed.path:
        raise DocumentCorruptError(
            "The DOCX selected-part relationship is invalid."
        )
    if parsed.path.startswith("/"):
        candidate = parsed.path.lstrip("/")
    else:
        source_directory = (
            "" if source_part is None else posixpath.dirname(source_part)
        )
        candidate = posixpath.join(source_directory, parsed.path)
    normalized = posixpath.normpath(candidate)
    if normalized in {"", ".", ".."} or normalized.startswith("../"):
        raise DocumentCorruptError(
            "The DOCX selected-part relationship escapes its package."
        )
    return _validate_member_name(normalized)


def _relationship_part_name(source_part: str) -> str:
    """Return the OPC relationship-part name associated with one source part."""

    source_directory = posixpath.dirname(source_part)
    relationship_name = f"{posixpath.basename(source_part)}.rels"
    if source_directory:
        return f"{source_directory}/_rels/{relationship_name}"
    return f"_rels/{relationship_name}"


def _require_part_content_type(
    root: ElementTree.Element,
    part_name: str,
    expected_content_type: str,
) -> None:
    """Require one exact Override for a selected OPC part."""

    if not _is_namespaced_element(
        root,
        "Types",
        _CONTENT_TYPE_NAMESPACES,
    ):
        raise DocumentCorruptError(
            "The DOCX package content-types root is invalid."
        )
    expected_part_name = f"/{part_name}"
    matches = [
        element.attrib.get("ContentType")
        for element in _direct_children(
            root,
            "Override",
            _CONTENT_TYPE_NAMESPACES,
        )
        if unquote(element.attrib.get("PartName", "")) == expected_part_name
    ]
    if matches != [expected_content_type]:
        raise DocumentCorruptError(
            "The DOCX package selected-part content type is invalid."
        )


def _paragraph_text(
    paragraph: ElementTree.Element,
    consume_fragment: Callable[[str], None] | None = None,
) -> str:
    """Preserve exact Word text once while excluding tracked deletions.

    Foreign elements may wrap legitimate Word runs, but a foreign ``t`` or
    ``del`` never gains Word semantics.  The optional fragment callback lets
    ordinary paragraphs and table cells enforce limits before concatenating
    attacker-controlled text.
    """

    pieces: list[str] = []

    def append(piece: str) -> None:
        """Record one visible fragment after incremental budget accounting."""

        if consume_fragment is not None:
            consume_fragment(piece)
        pieces.append(piece)

    def visit(element: ElementTree.Element) -> None:
        """Walk recognized paragraph containers, excluding tracked deletions."""

        name = _local_name(element.tag)
        is_word_element = _namespace(element.tag) in _WORDPROCESSING_NAMESPACES
        if not is_word_element or name in _DELETED_WRAPPERS:
            return
        if name == "t":
            append(element.text or "")
            return
        if name == "tab":
            append("\t")
            return
        if name in {"br", "cr"}:
            append("\n")
            return
        if name not in _PARAGRAPH_TEXT_CONTAINERS:
            # The loader is not a complete WordprocessingML schema validator.
            # Descending only through text-bearing containers prevents an
            # invented local name in the Word namespace from exposing nested
            # ``w:t`` that Word itself would not treat as paragraph content.
            return
        for child in element:
            visit(child)

    visit(paragraph)
    return "".join(pieces)


def _style_descriptors(
    root: ElementTree.Element | None,
) -> dict[str, tuple[str | None, int | None]]:
    """Map paragraph style IDs to optional names and outline levels."""

    if root is None or not _is_namespaced_element(
        root,
        "styles",
        _WORDPROCESSING_NAMESPACES,
    ):
        return {}
    descriptors: dict[str, tuple[str | None, int | None]] = {}
    for style in _direct_children(
        root,
        "style",
        _WORDPROCESSING_NAMESPACES,
    ):
        if (
            _attribute(style, "type", _WORDPROCESSING_NAMESPACES)
            or "paragraph"
        ) != "paragraph":
            continue
        style_id = _attribute(
            style,
            "styleId",
            _WORDPROCESSING_NAMESPACES,
        )
        if not style_id:
            continue
        name_element = _first_direct_child(
            style,
            "name",
            _WORDPROCESSING_NAMESPACES,
        )
        paragraph_properties = _first_direct_child(
            style,
            "pPr",
            _WORDPROCESSING_NAMESPACES,
        )
        outline_element = (
            None
            if paragraph_properties is None
            else _first_direct_child(
                paragraph_properties,
                "outlineLvl",
                _WORDPROCESSING_NAMESPACES,
            )
        )
        style_name = (
            None
            if name_element is None
            else _attribute(
                name_element,
                "val",
                _WORDPROCESSING_NAMESPACES,
            )
        )
        outline_level: int | None = None
        if outline_element is not None:
            raw_outline = _attribute(
                outline_element,
                "val",
                _WORDPROCESSING_NAMESPACES,
            )
            if raw_outline is not None and raw_outline.isdigit():
                parsed_outline = int(raw_outline) + 1
                if 1 <= parsed_outline <= 9:
                    outline_level = parsed_outline
        descriptors[style_id] = (style_name, outline_level)
    return descriptors


def _paragraph_kind(
    paragraph: ElementTree.Element,
    styles: dict[str, tuple[str | None, int | None]],
) -> tuple[str, int | None]:
    """Classify explicit Word title/heading styles without text heuristics."""

    properties = _first_direct_child(
        paragraph,
        "pPr",
        _WORDPROCESSING_NAMESPACES,
    )
    style_id: str | None = None
    direct_outline: int | None = None
    if properties is not None:
        style_element = _first_direct_child(
            properties,
            "pStyle",
            _WORDPROCESSING_NAMESPACES,
        )
        if style_element is not None:
            style_id = _attribute(
                style_element,
                "val",
                _WORDPROCESSING_NAMESPACES,
            )
        outline_element = _first_direct_child(
            properties,
            "outlineLvl",
            _WORDPROCESSING_NAMESPACES,
        )
        if outline_element is not None:
            raw_outline = _attribute(
                outline_element,
                "val",
                _WORDPROCESSING_NAMESPACES,
            )
            if raw_outline is not None and raw_outline.isdigit():
                candidate = int(raw_outline) + 1
                if 1 <= candidate <= 9:
                    direct_outline = candidate

    style_name: str | None = None
    style_outline: int | None = None
    if style_id is not None:
        style_name, style_outline = styles.get(style_id, (None, None))
    for candidate_name in (style_id, style_name):
        if candidate_name is None:
            continue
        if candidate_name.casefold() == "title":
            return "title", None
        match = _HEADING_STYLE_PATTERN.fullmatch(candidate_name)
        if match is not None:
            return "heading", int(match.group(1))
    heading_level = direct_outline or style_outline
    if heading_level is not None:
        return "heading", heading_level
    return "paragraph", None


def _iter_body_units(
    container: ElementTree.Element,
) -> Iterator[ElementTree.Element]:
    """Yield body paragraphs and tables in source order through safe wrappers."""

    for child in container:
        name = _local_name(child.tag)
        is_word_element = _namespace(child.tag) in _WORDPROCESSING_NAMESPACES
        if is_word_element and name in {"p", "tbl"}:
            yield child
        elif is_word_element and name in _STRUCTURAL_WRAPPERS:
            yield from _iter_body_units(child)
        # Deleted revisions, section properties, alternate content, embedded
        # objects, and extension elements are intentionally not interpreted.


def _table_from_element(
    table: ElementTree.Element,
    budget: _OutputBudget,
) -> DocumentTable | None:
    """Preserve DOCX rows and cell paragraph text without padding ragged rows."""

    def iter_cell_paragraphs(
        cell: ElementTree.Element,
    ) -> Iterator[ElementTree.Element]:
        """Yield each outer Word paragraph once through non-deleted wrappers."""

        for child in cell:
            name = _local_name(child.tag)
            is_word_element = (
                _namespace(child.tag) in _WORDPROCESSING_NAMESPACES
            )
            if is_word_element and name in _DELETED_WRAPPERS:
                continue
            if is_word_element and name == "p":
                # ``_paragraph_text`` owns the entire subtree.  Not descending
                # again prevents malformed nested paragraphs from multiplying
                # the same descendant text at every ancestor level.
                yield child
            elif (
                is_word_element
                and name in _CELL_PARAGRAPH_CONTAINERS
            ):
                yield from iter_cell_paragraphs(child)

    rows: list[tuple[str, ...]] = []
    has_content = False
    for row_element in _direct_children(
        table,
        "tr",
        _WORDPROCESSING_NAMESPACES,
    ):
        cells: list[str] = []
        for cell_element in _direct_children(
            row_element,
            "tc",
            _WORDPROCESSING_NAMESPACES,
        ):
            if len(cells) >= budget.limits.max_table_columns:
                raise DocumentContentLimitError(
                    "A DOCX table exceeds the column limit."
                )
            budget.start_cell()
            cell_code_points = 0
            paragraph_values: list[str] = []

            def consume_fragment(fragment: str) -> None:
                """Charge one visible cell fragment before concatenation."""

                nonlocal cell_code_points
                cell_code_points = budget.add_cell_fragment(
                    fragment,
                    cell_code_points,
                )

            for paragraph in iter_cell_paragraphs(cell_element):
                if paragraph_values:
                    consume_fragment("\n")
                paragraph_values.append(
                    _paragraph_text(paragraph, consume_fragment)
                )
            cell_text = "\n".join(paragraph_values)
            has_content = has_content or _has_meaningful_text(cell_text)
            cells.append(cell_text)
        if cells:
            rows.append(tuple(cells))
    if not rows or not has_content:
        return None
    return DocumentTable(rows=tuple(rows))


def _embedded_core_title(root: ElementTree.Element | None) -> str | None:
    """Return a safe core-properties title when one is explicitly present."""

    if root is None or not _is_namespaced_element(
        root,
        "coreProperties",
        _CORE_PROPERTIES_NAMESPACES,
    ):
        return None
    title_element = _first_direct_child(
        root,
        "title",
        _DUBLIN_CORE_NAMESPACES,
    )
    if title_element is not None:
        title = title_element.text or ""
        if (
            _has_meaningful_text(title)
            and len(title) <= MAX_DOCUMENT_TITLE_CODE_POINTS
            and not _has_unsafe_control(title)
        ):
            return title
    return None


class DocxDocumentLoader:
    """Extract ordered Word paragraphs, headings, titles, and tables safely."""

    @property
    def loader_id(self) -> str:
        """Return the stable adapter identity recorded with loaded results."""

        return "docx-ooxml"

    @property
    def loader_version(self) -> str:
        """Return the version governing raw OOXML extraction behavior."""

        return "1.0.0"

    @property
    def routes(self) -> frozenset[DocumentRoute]:
        """Return the exact DOCX suffix and media-type route."""

        return _ROUTES

    def load(
        self,
        source: DocumentSource,
        data: bytes,
        limits: DocumentLoadLimits,
    ) -> LoadedDocument:
        """Load a verified DOCX snapshot without extracting or dereferencing it."""

        self._validate_request(source, data, limits)
        _preflight_central_directory(data, limits)
        try:
            with ZipFile(BytesIO(data), mode="r", allowZip64=True) as archive:
                members = _validate_archive(archive, limits)
                return self._load_archive(
                    archive,
                    members,
                    source,
                    limits,
                )
        except DocumentError:
            raise
        except (BadZipFile, LargeZipFile, EOFError, zlib.error) as error:
            raise DocumentCorruptError(
                "The DOCX package is damaged or internally inconsistent."
            ) from error
        except (RecursionError, MemoryError) as error:
            raise DocumentContentLimitError(
                "The DOCX package exceeds a safe parsing limit."
            ) from error
        except (RuntimeError, NotImplementedError, UnicodeError) as error:
            raise DocumentCorruptError(
                "The DOCX package cannot be decoded safely."
            ) from error
        except OSError as error:
            raise DocumentReadError(
                "Verified DOCX bytes could not be read safely."
            ) from error
        except Exception as error:
            raise DocumentLoadFailedError(
                "The DOCX document could not be loaded safely."
            ) from error

    @staticmethod
    def _validate_request(
        source: DocumentSource,
        data: bytes,
        limits: DocumentLoadLimits,
    ) -> None:
        """Validate exact byte identity and distinguish encrypted containers."""

        if not isinstance(source, DocumentSource):
            raise DocumentValidationError("source must be DocumentSource.")
        if not isinstance(data, bytes):
            raise DocumentValidationError("data must be immutable bytes.")
        if not isinstance(limits, DocumentLoadLimits):
            raise DocumentValidationError("limits must be DocumentLoadLimits.")
        route = (PurePath(source.file_name).suffix.casefold(), source.media_type)
        if route not in _ROUTES:
            raise DocumentUnsupportedFormatError(
                "The document does not match the DOCX loader route."
            )
        if not data:
            raise DocumentEmptyError("The DOCX document contains no bytes.")
        if len(data) != source.size_bytes:
            raise DocumentReadError(
                "Verified document bytes do not match their metadata."
            )
        if len(data) > limits.max_source_bytes:
            raise DocumentTooLargeError(
                "The DOCX document exceeds the configured byte limit."
            )
        if data.startswith(_OLE_COMPOUND_HEADER):
            # Password-protected modern Office documents use an OLE compound
            # wrapper rather than a normal OPC ZIP.  Legacy .doc uses the same
            # signature and is likewise outside this exact .docx route.
            raise DocumentEncryptedError(
                "Encrypted or legacy Word containers are not supported."
            )
        if not data.startswith(_ZIP_LOCAL_HEADER):
            raise DocumentCorruptError(
                "The DOCX document has an invalid content signature."
            )

    def _load_archive(
        self,
        archive: ZipFile,
        members: dict[str, ZipInfo],
        source: DocumentSource,
        limits: DocumentLoadLimits,
    ) -> LoadedDocument:
        """Load selected OPC XML parts after the archive preflight succeeds."""

        xml_budget = _XmlBudget(limits)
        content_types_payload = _read_member(
            archive,
            members,
            _CONTENT_TYPES_PART,
            limits,
            required=True,
        )
        relationships_payload = _read_member(
            archive,
            members,
            _PACKAGE_RELATIONSHIPS_PART,
            limits,
            required=True,
        )
        assert content_types_payload is not None
        assert relationships_payload is not None
        content_types_root = _safe_xml_root(
            content_types_payload,
            xml_budget,
            _CONTENT_TYPES_MARKUP_PROFILE,
        )
        relationships_root = _safe_xml_root(
            relationships_payload,
            xml_budget,
            _PACKAGE_RELATIONSHIPS_MARKUP_PROFILE,
        )
        main_part = _relationship_part(
            relationships_root,
            _OFFICE_DOCUMENT_RELATIONSHIP_TYPES,
            source_part=None,
            required=True,
        )
        assert main_part is not None
        core_part = _relationship_part(
            relationships_root,
            _CORE_PROPERTIES_RELATIONSHIP_TYPES,
            source_part=None,
            required=False,
        )
        _require_part_content_type(
            content_types_root,
            main_part,
            _MAIN_DOCUMENT_CONTENT_TYPE,
        )
        if core_part is not None:
            _require_part_content_type(
                content_types_root,
                core_part,
                _CORE_PROPERTIES_CONTENT_TYPE,
            )
        main_payload = _read_member(
            archive,
            members,
            main_part,
            limits,
            required=True,
        )
        assert main_payload is not None
        document_root = _safe_xml_root(
            main_payload,
            xml_budget,
            _WORDPROCESSING_MARKUP_PROFILE,
        )
        if (
            _local_name(document_root.tag) != "document"
            or _namespace(document_root.tag) not in _WORDPROCESSING_NAMESPACES
        ):
            raise DocumentCorruptError(
                "The DOCX main document root is invalid."
            )

        main_relationships_payload = _read_member(
            archive,
            members,
            _relationship_part_name(main_part),
            limits,
            required=False,
        )
        styles_part: str | None = None
        if main_relationships_payload is not None:
            main_relationships_root = _safe_xml_root(
                main_relationships_payload,
                xml_budget,
                _PACKAGE_RELATIONSHIPS_MARKUP_PROFILE,
            )
            styles_part = _relationship_part(
                main_relationships_root,
                _STYLES_RELATIONSHIP_TYPES,
                source_part=main_part,
                required=False,
            )
        if styles_part is not None:
            _require_part_content_type(
                content_types_root,
                styles_part,
                _STYLES_CONTENT_TYPE,
            )
        styles_payload = (
            None
            if styles_part is None
            else _read_member(
                archive,
                members,
                styles_part,
                limits,
                required=True,
            )
        )
        styles_root = (
            None
            if styles_payload is None
            else _safe_xml_root(
                styles_payload,
                xml_budget,
                _WORDPROCESSING_MARKUP_PROFILE,
            )
        )
        if styles_root is not None and not _is_namespaced_element(
            styles_root,
            "styles",
            _WORDPROCESSING_NAMESPACES,
        ):
            raise DocumentCorruptError(
                "The DOCX styles root is invalid."
            )
        core_payload = (
            None
            if core_part is None
            else _read_member(
                archive,
                members,
                core_part,
                limits,
                required=True,
            )
        )
        core_root = (
            None
            if core_payload is None
            else _safe_xml_root(
                core_payload,
                xml_budget,
                _CORE_PROPERTIES_MARKUP_PROFILE,
            )
        )
        if core_root is not None and not _is_namespaced_element(
            core_root,
            "coreProperties",
            _CORE_PROPERTIES_NAMESPACES,
        ):
            raise DocumentCorruptError(
                "The DOCX core-properties root is invalid."
            )
        return self._extract_document(
            document_root,
            styles_root,
            core_root,
            source,
            limits,
        )

    def _extract_document(
        self,
        document_root: ElementTree.Element,
        styles_root: ElementTree.Element | None,
        core_root: ElementTree.Element | None,
        source: DocumentSource,
        limits: DocumentLoadLimits,
    ) -> LoadedDocument:
        """Preserve ordered main-body units under exact output budgets."""

        body = next(
            (
                element
                for element in document_root
                if _local_name(element.tag) == "body"
                and _namespace(element.tag) in _WORDPROCESSING_NAMESPACES
            ),
            None,
        )
        if body is None:
            raise DocumentCorruptError(
                "The DOCX main document has no valid body."
            )
        styles = _style_descriptors(styles_root)
        output_budget = _OutputBudget(limits)
        blocks: list[DocumentBlock] = []
        first_heading_title: str | None = None
        first_heading_seen = False
        for unit in _iter_body_units(body):
            if _local_name(unit.tag) == "p":
                text = _paragraph_text(unit, output_budget.add_text)
                if not _has_meaningful_text(text):
                    continue
                kind, heading_level = _paragraph_kind(unit, styles)
                if len(blocks) >= limits.max_blocks:
                    raise DocumentContentLimitError(
                        "The DOCX document exceeds the block limit."
                    )
                blocks.append(
                    DocumentBlock(
                        ordinal=len(blocks),
                        kind=kind,  # type: ignore[arg-type]
                        text=text,
                        heading_level=heading_level,
                    )
                )
                if kind in {"title", "heading"} and not first_heading_seen:
                    first_heading_seen = True
                    # A heading block can legitimately be larger than the
                    # domain's concise title field.  Preserve the block but do
                    # not promote it, avoiding a late validation exception.
                    if len(text) <= MAX_DOCUMENT_TITLE_CODE_POINTS:
                        first_heading_title = text
            else:
                table = _table_from_element(unit, output_budget)
                if table is not None:
                    if len(blocks) >= limits.max_blocks:
                        raise DocumentContentLimitError(
                            "The DOCX document exceeds the block limit."
                        )
                    blocks.append(
                        DocumentBlock(
                            ordinal=len(blocks),
                            kind="table",
                            table=table,
                        )
                    )

        if not blocks:
            raise DocumentEmptyError(
                "The DOCX document contains no extractable content."
            )
        embedded_title = _embedded_core_title(core_root)
        title: DocumentTitle | None = None
        if embedded_title is not None:
            output_budget.add_text(embedded_title)
            title = DocumentTitle(text=embedded_title, source="embedded")
        elif first_heading_title is not None:
            output_budget.add_text(first_heading_title)
            title = DocumentTitle(text=first_heading_title, source="heading")
        return LoadedDocument(
            schema_version=DOCUMENT_SCHEMA_VERSION,
            source=source,
            document_format="docx",
            loader_id=self.loader_id,
            loader_version=self.loader_version,
            blocks=tuple(blocks),
            limits=limits,
            title=title,
        )
