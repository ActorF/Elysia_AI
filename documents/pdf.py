"""Load bounded PDF text from verified in-memory attachment bytes.

The adapter deliberately preserves only structure that PDF can state
reliably: an embedded document title, one-based page numbers, and the raw
text returned for each page.  PDF has no dependable semantic paragraph or
table layer, so this module does not guess either structure and leaves all
cleaning and chunking to the next pipeline stage.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from io import BytesIO
from pathlib import PurePath
import unicodedata
from typing import Any, Final, cast

from pypdf import Configuration, PdfReader, __version__ as pypdf_version
from pypdf import apply_configuration
from pypdf.errors import (
    DependencyError,
    EmptyFileError,
    FileNotDecryptedError,
    LimitReachedError,
    ParseError,
    PdfReadError,
    PdfStreamError,
    PyPdfError,
    WrongPasswordError,
)
from pypdf.generic import (
    ArrayObject,
    ContentStream,
    DictionaryObject,
    NameObject,
    NullObject,
    StreamObject,
)

from .domain import (
    DOCUMENT_SCHEMA_VERSION,
    MAX_DOCUMENT_TITLE_CODE_POINTS,
    DocumentBlock,
    DocumentLoadLimits,
    DocumentSource,
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


_PDF_HEADER: Final = b"%PDF-"
_MAX_PDF_STREAM_BYTES: Final = 16 * 1024 * 1024
_MAX_PDF_RECOVERY_BYTES: Final = 1 * 1024 * 1024
_MAX_PDF_TREE_DEPTH: Final = 64
_MAX_PDF_XFORM_INVOCATIONS: Final = 5_000
_MAX_PDF_CONTENT_STREAM_BYTES: Final = 1 * 1024 * 1024
_MAX_PDF_CONTENT_TOKENS: Final = 60_000
_MAX_PDF_INLINE_IMAGE_HEADER_BYTES: Final = 4 * 1024
_MAX_PDF_OPERATIONS_PER_STREAM: Final = 50_000
_MAX_PDF_OPERATIONS: Final = 100_000
_MAX_PDF_FONT_RESOURCE_VISITS: Final = 4_096
_MAX_PDF_FONT_SEMANTIC_ENTRIES: Final = 100_000
_PDF_FONT_BASELINE_SEMANTIC_ENTRIES: Final = 512
_MAX_PDF_CMAP_TOKENS: Final = 250_000
_MAX_PDF_CMAP_CODE_HEX_DIGITS: Final = 16
_MAX_PDF_CMAP_STRING_HEX_DIGITS: Final = 1_024
_MAX_PDF_CMAP_OUTPUT_CODE_POINTS: Final = 256
_MAX_PDF_ENCODING_CODE_POINTS: Final = 4
_MAX_PDF_GLYPH_NAME_CODE_POINTS: Final = 256
_MAX_PDF_FONT_NAME_CODE_POINTS: Final = 256
_MAX_PDF_TEXT_OPERATION_CODE_POINTS: Final = 16_384
_PDF_CMAP_SECTION_MARKERS: Final = (
    b"beginbfchar",
    b"endbfchar",
    b"beginbfrange",
    b"endbfrange",
)
_ROUTES: Final = frozenset({(".pdf", "application/pdf")})


class _PdfExtractionAbort(BaseException):
    """Escape pypdf's broad Form-XObject ``Exception`` handler safely.

    pypdf intentionally suppresses ordinary exceptions raised while extracting
    a Form XObject so one broken drawing does not discard the rest of a page.
    A loader failure cannot be suppressed because that would publish partial
    text from a rejected document.  This private ``BaseException`` carries an
    already classified, path-free domain error and crosses only that parser
    catch; the loader unwraps it immediately at its trust boundary.
    """

    def __init__(
        self,
        error: DocumentError,
        cause: Exception | None = None,
    ) -> None:
        """Retain stable classification without exposing native error text."""

        super().__init__()
        self.error = error
        self.cause = cause


@dataclass(slots=True)
class _PdfTextGuard:
    """Bound text accumulated by one page or Form extraction context.

    pypdf reports a Form's text inside the recursive Form extraction and again
    when it appends the completed Form to its caller.  Each recursive extraction
    therefore receives a child guard, while the caller sees only the completed
    Form callback.  This keeps accounting exact at every nesting level and
    prevents repeated small callbacks from first building an oversized string.
    The root flag remains shared so a future parser cannot hide an aborted child
    extraction by converting its sentinel into a normal return.
    """

    remaining_code_points: int = 0
    exceeded: bool = False
    pending_code_points: int = 0
    root: _PdfTextGuard | None = field(default=None, repr=False)

    def reset(self, remaining_code_points: int) -> None:
        """Set the exact remaining output allowance for the next page."""

        self.remaining_code_points = remaining_code_points
        self.exceeded = False
        self.pending_code_points = 0

    def child(self) -> _PdfTextGuard:
        """Create a Form-local guard using its caller's remaining allowance."""

        root = self if self.root is None else self.root
        return _PdfTextGuard(
            remaining_code_points=self.remaining_code_points,
            root=root,
        )

    def reject(self) -> None:
        """Mark this extraction tree over budget and escape pypdf immediately."""

        self.exceeded = True
        if self.root is not None:
            self.root.exceeded = True
        raise _PdfExtractionAbort(
            DocumentContentLimitError(
                "The PDF document exceeds the text limit."
            )
        )

    def admit_operation(self, maximum_code_points: int) -> None:
        """Reject an operation before it can materialize oversized text.

        ``visitor_text`` runs only after pypdf has expanded a text operand.  A
        conservative pending bound is therefore retained between callbacks so
        several operations cannot first build one oversized internal string.
        """

        if (
            maximum_code_points < 0
            or maximum_code_points > _MAX_PDF_TEXT_OPERATION_CODE_POINTS
            or self.pending_code_points + maximum_code_points
            > self.remaining_code_points
        ):
            self.reject()

    def complete_operation(self, maximum_code_points: int) -> None:
        """Retain the admitted bound until pypdf flushes accumulated text."""

        self.pending_code_points += maximum_code_points

    def visit(
        self,
        text: Any,
        _current_matrix: Any,
        _text_matrix: Any,
        _font_dictionary: Any,
        _font_size: Any,
    ) -> None:
        """Charge one emitted fragment before later callbacks can amplify it."""

        if not isinstance(text, str):
            return
        self.remaining_code_points -= len(text)
        # A callback flushes the extractor's accumulated text.  If it occurs
        # partway through a right-to-left operand, that whole operand was still
        # admitted up front and its conservative bound is restored by the
        # operand-after callback.
        self.pending_code_points = 0
        if self.remaining_code_points < 0:
            self.reject()


@dataclass(slots=True)
class _PdfOperandGuard:
    """Mirror font-selection state and bound text before pypdf expands it."""

    text_guard: _PdfTextGuard
    font_factors: dict[str, int]
    current_factor: int = _MAX_PDF_ENCODING_CODE_POINTS
    factor_stack: list[int] = field(default_factory=list)
    admitted_code_points: int = 0

    @staticmethod
    def _value_bound(value: Any, factor: int) -> int:
        """Return a conservative output bound for one PDF string operand."""

        if isinstance(value, str):
            return len(value)
        if isinstance(value, bytes):
            return len(value) * factor
        raise _PdfExtractionAbort(
            DocumentCorruptError(
                "The PDF document contains an invalid text operand."
            )
        )

    def _text_bound(self, operator: bytes, operands: Any) -> int:
        """Bound every string and synthetic space handled by a text operator."""

        if operator not in {b"Tj", b"'", b'"', b"TJ"}:
            return 0
        if not isinstance(operands, list):
            raise _PdfExtractionAbort(
                DocumentCorruptError(
                    "The PDF document contains invalid text operands."
                )
            )
        if operator in {b"Tj", b"'"}:
            if len(operands) != 1:
                raise _PdfExtractionAbort(
                    DocumentCorruptError(
                        "The PDF document contains invalid text operands."
                    )
                )
            return self._value_bound(operands[0], self.current_factor)
        if operator == b'"':
            if (
                len(operands) != 3
                or not isinstance(operands[0], (int, float))
                or not isinstance(operands[1], (int, float))
            ):
                raise _PdfExtractionAbort(
                    DocumentCorruptError(
                        "The PDF document contains invalid text operands."
                    )
                )
            return self._value_bound(operands[2], self.current_factor)
        if len(operands) != 1:
            raise _PdfExtractionAbort(
                DocumentCorruptError(
                    "The PDF document contains invalid text operands."
                )
            )
        values = operands[0]
        if not isinstance(values, ArrayObject):
            raise _PdfExtractionAbort(
                DocumentCorruptError(
                    "The PDF document contains invalid text operands."
                )
            )
        bound = 0
        for value in values:
            if isinstance(value, (str, bytes)):
                bound += self._value_bound(value, self.current_factor)
            elif isinstance(value, (int, float)):
                # A sufficiently large kerning adjustment can synthesize one
                # space, so every numeric entry is charged conservatively.
                bound += 1
            else:
                raise _PdfExtractionAbort(
                    DocumentCorruptError(
                        "The PDF document contains an invalid text operand."
                    )
                )
        return bound

    def before(
        self,
        operator: Any,
        operands: Any,
        _current_matrix: Any,
        _text_matrix: Any,
    ) -> None:
        """Update graphics state and admit one operation before processing."""

        self.admitted_code_points = 0
        if operator == b"q":
            self.factor_stack.append(self.current_factor)
            return
        if operator == b"Q":
            # pypdf leaves the current font unchanged when ``Q`` underflows
            # its graphics-state stack, so the guard mirrors that recovery.
            if self.factor_stack:
                self.current_factor = self.factor_stack.pop()
            return
        if operator == b"Tf":
            if isinstance(operands, list) and operands:
                self.current_factor = self.font_factors.get(
                    str(operands[0]),
                    _MAX_PDF_ENCODING_CODE_POINTS,
                )
            else:
                self.current_factor = _MAX_PDF_ENCODING_CODE_POINTS
            return

        if isinstance(operator, bytes):
            bound = self._text_bound(operator, operands)
            if bound:
                self.text_guard.admit_operation(bound)
                self.admitted_code_points = bound

    def after(
        self,
        _operator: Any,
        _operands: Any,
        _current_matrix: Any,
        _text_matrix: Any,
    ) -> None:
        """Keep an admitted bound until a later text callback flushes it."""

        if self.admitted_code_points:
            self.text_guard.complete_operation(self.admitted_code_points)
            self.admitted_code_points = 0


@dataclass(slots=True)
class _PdfWorkBudget:
    """Bound decoded content and parsed operations along actual ``Do`` paths.

    pypdf materializes all ``ContentStream.operations`` objects at once.  The
    per-stream byte ceiling bounds that unavoidable in-process allocation, and
    the per-stream plus document operation ceilings bound the retained object
    graph.  This is a defensive in-process cap, not the stronger isolation an
    OS-constrained worker with a wall-clock deadline could provide.
    """

    limits: DocumentLoadLimits
    expanded_bytes: int = 0
    operations: int = 0
    form_invocations: int = 0
    font_resource_visits: int = 0
    font_semantic_entries: int = 0
    font_factors: dict[
        int,
        tuple[DictionaryObject, dict[str, int]],
    ] = field(default_factory=dict)
    plans: dict[
        int,
        tuple[StreamObject, int, list[tuple[Any, bytes]]],
    ] = field(
        default_factory=dict
    )

    def consume_stream(self, decoded_bytes: int, operation_count: int) -> None:
        """Charge one executed page/Form stream against aggregate budgets."""

        if decoded_bytes > _MAX_PDF_CONTENT_STREAM_BYTES:
            raise DocumentContentLimitError(
                "A PDF content stream exceeds the safe parsing limit."
            )
        if operation_count > _MAX_PDF_OPERATIONS_PER_STREAM:
            raise DocumentContentLimitError(
                "A PDF content stream exceeds the operation limit."
            )
        self.expanded_bytes += decoded_bytes
        if self.expanded_bytes > self.limits.max_expanded_bytes:
            raise DocumentContentLimitError(
                "The PDF document exceeds the expansion limit."
            )
        self.operations += operation_count
        if self.operations > min(
            self.limits.max_xml_elements,
            _MAX_PDF_OPERATIONS,
        ):
            raise DocumentContentLimitError(
                "The PDF document exceeds the traversal limit."
            )

    def consume_form_invocation(self) -> None:
        """Charge every actual Form invocation, including repeated references."""

        self.form_invocations += 1
        if self.form_invocations > min(
            self.limits.max_xml_elements,
            _MAX_PDF_XFORM_INVOCATIONS,
        ):
            raise DocumentContentLimitError(
                "The PDF document exceeds the Form invocation limit."
            )

    def consume_font_resources(self, resource_count: int) -> None:
        """Charge every font entry that pypdf will inspect during extraction."""

        self.font_resource_visits += resource_count
        if self.font_resource_visits > min(
            self.limits.max_xml_elements,
            _MAX_PDF_FONT_RESOURCE_VISITS,
        ):
            raise DocumentContentLimitError(
                "The PDF document exceeds the font resource limit."
            )

    def consume_font_stream(self, decoded_bytes: int) -> None:
        """Charge one font mapping parse against aggregate expanded work.

        The same decoded stream can be parsed into a separate character map for
        every font alias, page, or Form visit.  Charging each inspection, not
        just each object identity, therefore bounds both decompression and the
        mapping objects that pypdf materializes from cached bytes.
        """

        self.expanded_bytes += decoded_bytes
        if self.expanded_bytes > self.limits.max_expanded_bytes:
            raise DocumentContentLimitError(
                "The PDF document exceeds the expansion limit."
            )

    def consume_font_semantics(self, entry_count: int) -> None:
        """Charge mappings and widths that become individual Python entries."""

        if entry_count < 0:
            raise DocumentCorruptError(
                "The PDF document contains an invalid font range."
            )
        self.font_semantic_entries += entry_count
        if self.font_semantic_entries > min(
            self.limits.max_xml_elements,
            _MAX_PDF_FONT_SEMANTIC_ENTRIES,
        ):
            raise DocumentContentLimitError(
                "The PDF document exceeds the font mapping limit."
            )

    def remember_font_factors(
        self,
        resources: DictionaryObject,
        factors: dict[str, int],
    ) -> None:
        """Retain bounded per-font expansion factors for extraction guards."""

        self.font_factors[id(resources)] = (resources, factors)

    def factors_for(self, resources: DictionaryObject) -> dict[str, int]:
        """Return factors only when the retained resource identity still matches."""

        retained = self.font_factors.get(id(resources))
        if retained is None or retained[0] is not resources:
            return {}
        return retained[1]


def _has_meaningful_text(value: str) -> bool:
    """Return whether extracted text contains more than whitespace or BOMs."""

    return any(
        not (character.isspace() or character == "\ufeff")
        for character in value
    )


def _has_unsafe_control(value: str) -> bool:
    """Detect controls that the shared document domain intentionally rejects."""

    return any(
        unicodedata.category(character) == "Cs"
        or (
            unicodedata.category(character) == "Cc"
            and character not in {"\t", "\n", "\r"}
        )
        for character in value
    )


def _configuration_for(limits: DocumentLoadLimits) -> Configuration:
    """Build context-local pypdf decompression and traversal limits.

    pypdf's configuration is held in a ``ContextVar``.  Applying a complete
    immutable value for the duration of one load avoids weakening another
    concurrent request while bounding every compression filter used during
    page-content extraction.
    """

    stream_bytes = min(limits.max_expanded_bytes, _MAX_PDF_STREAM_BYTES)
    tree_depth = min(limits.max_xml_depth, _MAX_PDF_TREE_DEPTH)
    return Configuration(
        maximum_declared_stream_length=stream_bytes,
        array_based_stream_maximum_output_length=stream_bytes,
        jbig2_maximum_output_length=stream_bytes,
        lzw_maximum_output_length=stream_bytes,
        run_length_maximum_output_length=stream_bytes,
        zlib_maximum_output_length=stream_bytes,
        zlib_maximum_recovery_input_length=min(
            stream_bytes,
            _MAX_PDF_RECOVERY_BYTES,
        ),
        flate_maximum_columns=250_000,
        flate_maximum_row_length=min(stream_bytes, 4_000_000),
        image_maximum_buffer_size=stream_bytes,
        xmp_maximum_input_length=min(stream_bytes, _MAX_PDF_RECOVERY_BYTES),
        xmp_maximum_element_count=min(limits.max_xml_elements, 100_000),
        outline_maximum_entries=min(limits.max_blocks, 100_000),
        outline_maximum_depth=tree_depth,
        page_tree_maximum_entries=limits.max_pages,
        page_tree_maximum_depth=tree_depth,
        xform_maximum_invocations_per_extraction=min(
            limits.max_xml_elements,
            _MAX_PDF_XFORM_INVOCATIONS,
        ),
        # Text loading never needs to invoke the optional native JBIG2 tool.
        jbig2dec_binary=None,
        page_merge_box="cropbox",
        disable_legacy_handling=True,
    )


def _resolved_pdf_object(value: Any) -> Any:
    """Resolve one pypdf object reference without assuming its concrete type."""

    get_object = getattr(value, "get_object", None)
    return get_object() if callable(get_object) else value


def _inline_image_end(data: bytes, header_start: int) -> int:
    """Return the byte after a bounded inline image's closing ``EI`` token.

    Inline image samples are arbitrary binary data and must not be interpreted
    as PDF string or operator tokens by the lightweight density scan.  The
    header and closing-marker rules mirror the whitespace-delimited shape that
    pypdf accepts; ambiguous or missing boundaries fail closed.
    """

    whitespace = b"\x00\t\n\x0c\r "
    header_end = min(
        len(data),
        header_start + _MAX_PDF_INLINE_IMAGE_HEADER_BYTES,
    )
    data_marker = -1
    cursor = header_start
    while cursor < header_end:
        candidate = data.find(b"ID", cursor, header_end)
        if candidate < 0:
            break
        before_is_boundary = (
            candidate == header_start or data[candidate - 1] in whitespace
        )
        after = candidate + 2
        if (
            before_is_boundary
            and after < len(data)
            and data[after] in whitespace
        ):
            data_marker = after + 1
            if data[after] == ord("\r") and data_marker < len(data):
                if data[data_marker] == ord("\n"):
                    data_marker += 1
            break
        cursor = candidate + 2
    if data_marker < 0:
        raise DocumentCorruptError(
            "The PDF document contains an invalid inline image header."
        )

    cursor = data_marker
    while True:
        candidate = data.find(b"EI", cursor)
        if candidate < 0:
            raise DocumentCorruptError(
                "The PDF document contains an unterminated inline image."
            )
        before_is_boundary = (
            candidate == data_marker or data[candidate - 1] in whitespace
        )
        after = candidate + 2
        after_is_boundary = after == len(data) or data[after] in whitespace
        if before_is_boundary and after_is_boundary:
            next_token_start = after
            while (
                next_token_start < len(data)
                and data[next_token_start] in whitespace
            ):
                next_token_start += 1
            next_token_end = next_token_start
            while (
                next_token_end < len(data)
                and data[next_token_end] not in whitespace
                and data[next_token_end] not in b"()<>[]{}/%"
            ):
                next_token_end += 1
            following = data[next_token_start:next_token_end]
            numeric_following = following.lstrip(b"+-").replace(b".", b"")
            if (
                next_token_start == len(data)
                or data[next_token_start : next_token_start + 1] == b"/"
                or following in {b"'", b'"'}
                or (numeric_following and numeric_following.isdigit())
                or (
                    1 <= len(following) <= 3
                    and following.isalpha()
                )
            ):
                return after
        cursor = candidate + 2


def _validate_content_token_density(data: bytes) -> None:
    """Reject token-dense content before pypdf materializes operation objects.

    This allocation-free lexical pass deliberately counts operands and syntax
    delimiters as well as possible operators, making it a conservative upper
    bound rather than a second PDF parser.  Literal and hexadecimal strings are
    skipped as single tokens.  At most 60,000 tokens reach pypdf, so a compact
    stream such as repeated ``q Q`` cannot first create hundreds of thousands
    of Python tuples and only then be rejected by the operation limit.
    """

    whitespace = b"\x00\t\n\x0c\r "
    delimiters = b"()<>[]{}/%"
    index = 0
    token_count = 0
    data_length = len(data)
    while index < data_length:
        current = data[index]
        if current in whitespace:
            index += 1
            continue
        if current == ord("%"):
            index += 1
            while index < data_length and data[index] not in b"\r\n":
                index += 1
            continue
        if current == ord("("):
            index += 1
            depth = 1
            while index < data_length and depth:
                current = data[index]
                if current == ord("\\"):
                    index += 2
                    continue
                if current == ord("("):
                    depth += 1
                elif current == ord(")"):
                    depth -= 1
                index += 1
            if depth:
                raise DocumentCorruptError(
                    "The PDF document contains an unterminated content string."
                )
        elif current == ord("<") and (
            index + 1 >= data_length or data[index + 1] != ord("<")
        ):
            terminator = data.find(b">", index + 1)
            if terminator < 0:
                raise DocumentCorruptError(
                    "The PDF document contains an unterminated hexadecimal string."
                )
            index = terminator + 1
        elif current == ord("/"):
            index += 1
            while (
                index < data_length
                and data[index] not in whitespace
                and data[index] not in delimiters
            ):
                index += 1
        elif current in delimiters:
            if (
                current in {ord("<"), ord(">")}
                and index + 1 < data_length
                and data[index + 1] == current
            ):
                index += 2
            else:
                index += 1
        else:
            token_start = index
            index += 1
            while (
                index < data_length
                and data[index] not in whitespace
                and data[index] not in delimiters
            ):
                index += 1
            if data[token_start:index] == b"BI":
                index = _inline_image_end(data, index)
        token_count += 1
        if token_count > _MAX_PDF_CONTENT_TOKENS:
            raise DocumentContentLimitError(
                "A PDF content stream exceeds the token limit."
            )


def _pdf_resources(
    container: DictionaryObject,
    limits: DocumentLoadLimits,
) -> DictionaryObject:
    """Return resources through a bounded, cycle-safe inheritance walk.

    Page resources may be inherited through ``/Parent``.  pypdf's generic
    helper detects cycles but deliberately has no depth ceiling, and Form
    preflight can request resources thousands of times.  Walking at most the
    configured tree depth prevents a long parent chain from amplifying CPU
    work before the loader's aggregate Form budget can reject the document.
    """

    current = container
    visited: set[int] = set()
    maximum_depth = min(limits.max_xml_depth, _MAX_PDF_TREE_DEPTH)
    for depth in range(maximum_depth + 1):
        current_key = id(current)
        if current_key in visited:
            raise DocumentContentLimitError(
                "The PDF document contains a cyclic resource hierarchy."
            )
        visited.add(current_key)

        if "/Resources" in current:
            resources = _resolved_pdf_object(current.raw_get("/Resources"))
            if not isinstance(resources, DictionaryObject):
                raise DocumentCorruptError(
                    "The PDF document contains invalid content resources."
                )
            return resources
        if "/Parent" not in current:
            return DictionaryObject()
        if depth == maximum_depth:
            raise DocumentContentLimitError(
                "The PDF document exceeds the resource inheritance limit."
            )
        parent = _resolved_pdf_object(current.raw_get("/Parent"))
        if not isinstance(parent, DictionaryObject):
            raise DocumentCorruptError(
                "The PDF document contains an invalid resource hierarchy."
            )
        current = parent

    raise AssertionError("The bounded PDF resource walk did not terminate.")


def _iter_cmap_tokens(data: bytes) -> Iterator[tuple[str, bytes]]:
    """Yield bounded CMap tokens without constructing a complete token list.

    pypdf's CMap parser expands range declarations into dictionaries.  This
    lightweight lexer preserves only the tokens needed to measure that future
    expansion, while still validating comments, strings, and hexadecimal
    delimiters well enough to fail closed on ambiguous mapping sections.
    """

    whitespace = b"\x00\t\n\x0c\r "
    delimiters = b"<>[]()%"
    hexadecimal = b"0123456789abcdefABCDEF"
    index = 0
    token_count = 0
    while index < len(data):
        current = data[index]
        if current in whitespace:
            index += 1
            continue
        if current == ord("%"):
            index += 1
            while index < len(data) and data[index] not in b"\r\n":
                index += 1
            continue

        kind = "word"
        token: bytes
        if current == ord("<") and (
            index + 1 >= len(data) or data[index + 1] != ord("<")
        ):
            terminator = data.find(b">", index + 1)
            if terminator < 0:
                raise DocumentCorruptError(
                    "The PDF document contains an unterminated font map token."
                )
            raw_token = data[index + 1 : terminator]
            if any(
                character in b"\x00\t\n\x0c\r"
                for character in raw_token
            ):
                # pypdf strips only ASCII spaces at this normalization stage;
                # other PDF whitespace later becomes token separators and can
                # turn one apparent hex string into several mapping entries.
                raise DocumentCorruptError(
                    "The PDF document contains ambiguous font map whitespace."
                )
            token = raw_token.replace(b" ", b"")
            if any(character not in hexadecimal for character in token):
                raise DocumentCorruptError(
                    "The PDF document contains an invalid font map token."
                )
            kind = "hex"
            index = terminator + 1
        elif current in {ord("["), ord("]")}:
            token = bytes((current,))
            kind = "bracket"
            index += 1
        elif current == ord("("):
            start = index
            index += 1
            depth = 1
            while index < len(data) and depth:
                current = data[index]
                if current == ord("\\"):
                    index += 2
                    continue
                if current == ord("("):
                    depth += 1
                elif current == ord(")"):
                    depth -= 1
                index += 1
            if depth:
                raise DocumentCorruptError(
                    "The PDF document contains an unterminated font map string."
                )
            token = data[start:index]
        elif current in {ord("<"), ord(">")}:
            if index + 1 < len(data) and data[index + 1] == current:
                token = data[index : index + 2]
                index += 2
            else:
                raise DocumentCorruptError(
                    "The PDF document contains an invalid font map delimiter."
                )
        else:
            start = index
            index += 1
            while (
                index < len(data)
                and data[index] not in whitespace
                and data[index] not in delimiters
            ):
                index += 1
            token = data[start:index]

        token_count += 1
        if token_count > _MAX_PDF_CMAP_TOKENS:
            raise DocumentContentLimitError(
                "The PDF document exceeds the font map token limit."
            )
        if len(token) > _MAX_PDF_CMAP_STRING_HEX_DIGITS:
            raise DocumentContentLimitError(
                "The PDF document contains an oversized font map token."
            )
        yield kind, token


def _cmap_source_value(token: bytes) -> int:
    """Decode one bounded CMap source code or reject malformed hex."""

    if (
        not token
        or len(token) % 2
        or len(token) > _MAX_PDF_CMAP_CODE_HEX_DIGITS
    ):
        raise DocumentCorruptError(
            "The PDF document contains an invalid font map source code."
        )
    return int(token, 16)


def _cmap_destination_bound(token: bytes) -> int:
    """Return pypdf's maximum Unicode length for one destination token."""

    if not token:
        return 0
    if len(token) % 2 or len(token) > _MAX_PDF_CMAP_STRING_HEX_DIGITS:
        raise DocumentCorruptError(
            "The PDF document contains an invalid font map destination."
        )
    if len(token) < 4:
        return len(token) // 2
    if len(token) % 4:
        raise DocumentCorruptError(
            "The PDF document contains an invalid Unicode font mapping."
        )
    return min(
        len(token) // 4,
        _MAX_PDF_CMAP_OUTPUT_CODE_POINTS,
    )


def _analyze_to_unicode(data: bytes) -> tuple[int, int]:
    """Measure CMap dictionary entries and worst per-character expansion.

    Range sizes are computed arithmetically instead of expanded.  The parser
    accepts the two mapping forms that pypdf consumes (``bfchar`` and
    ``bfrange``) and rejects incomplete sections rather than letting a damaged
    map produce parser-dependent partial text.
    """

    mode: str | None = None
    source: bytes | None = None
    range_start: int | None = None
    range_end: int | None = None
    array_entries = 0
    entries = 0
    output_factor = 1
    raw_marker_count = sum(
        data.count(marker) for marker in _PDF_CMAP_SECTION_MARKERS
    )
    parsed_marker_count = 0

    for kind, token in _iter_cmap_tokens(data):
        if kind == "word" and token in {b"beginbfchar", b"beginbfrange"}:
            parsed_marker_count += 1
            if mode is not None:
                raise DocumentCorruptError(
                    "The PDF document contains nested font map sections."
                )
            mode = "char" if token == b"beginbfchar" else "range-source"
            continue
        if kind == "word" and token in {b"endbfchar", b"endbfrange"}:
            parsed_marker_count += 1
            expected = "char" if token == b"endbfchar" else "range-source"
            if (
                mode != expected
                or source is not None
                or range_start is not None
                or range_end is not None
            ):
                raise DocumentCorruptError(
                    "The PDF document contains an incomplete font map section."
                )
            mode = None
            continue
        if mode is None:
            continue

        if mode == "char":
            if kind != "hex":
                raise DocumentCorruptError(
                    "The PDF document contains an invalid character mapping."
                )
            if source is None:
                _cmap_source_value(token)
                source = token
            else:
                output_factor = max(
                    output_factor,
                    _cmap_destination_bound(token),
                )
                entries += 1
                source = None
            continue

        if mode == "range-source":
            if kind != "hex":
                raise DocumentCorruptError(
                    "The PDF document contains an invalid range mapping."
                )
            range_start = _cmap_source_value(token)
            mode = "range-end"
            continue
        if mode == "range-end":
            if kind != "hex" or range_start is None:
                raise DocumentCorruptError(
                    "The PDF document contains an invalid range mapping."
                )
            range_end = _cmap_source_value(token)
            if range_end < range_start:
                raise DocumentCorruptError(
                    "The PDF document contains a reversed font map range."
                )
            mode = "range-destination"
            continue
        if mode == "range-destination":
            if kind == "hex":
                if range_start is None or range_end is None:
                    raise DocumentCorruptError(
                        "The PDF document contains an invalid range mapping."
                    )
                output_factor = max(
                    output_factor,
                    _cmap_destination_bound(token),
                )
                entries += range_end - range_start + 1
                range_start = None
                range_end = None
                mode = "range-source"
                continue
            if kind == "bracket" and token == b"[":
                array_entries = 0
                mode = "range-array"
                continue
            raise DocumentCorruptError(
                "The PDF document contains an invalid range destination."
            )
        if mode == "range-array":
            if kind == "bracket" and token == b"]":
                entries += array_entries
                range_start = None
                range_end = None
                mode = "range-source"
                continue
            if kind != "hex":
                raise DocumentCorruptError(
                    "The PDF document contains an invalid range array."
                )
            output_factor = max(
                output_factor,
                _cmap_destination_bound(token),
            )
            array_entries += 1

    if parsed_marker_count != raw_marker_count:
        # pypdf normalizes marker byte substrings before it tokenizes CMaps.
        # A marker hidden in a comment, literal, or longer name could otherwise
        # become active for pypdf while remaining invisible to this preflight.
        raise DocumentCorruptError(
            "The PDF document contains an ambiguous font map marker."
        )
    if mode is not None or source is not None:
        raise DocumentCorruptError(
            "The PDF document contains an unterminated font map section."
        )
    return entries, output_factor


def _decode_font_stream(
    stream: StreamObject,
    budget: _PdfWorkBudget,
) -> bytes:
    """Decode and charge one font stream before pypdf builds a character map.

    The nested filter ceilings track the exact aggregate allowance remaining.
    Without that tightening, several individually legal compressed streams can
    each allocate up to the per-stream ceiling before an after-the-fact total
    check notices that their combined expansion exceeded the caller's budget.
    """

    remaining = budget.limits.max_expanded_bytes - budget.expanded_bytes
    if remaining < 0:
        raise DocumentContentLimitError(
            "The PDF document exceeds the expansion limit."
        )
    decode_ceiling = min(remaining + 1, _MAX_PDF_STREAM_BYTES)
    with apply_configuration(
        array_based_stream_maximum_output_length=decode_ceiling,
        jbig2_maximum_output_length=decode_ceiling,
        lzw_maximum_output_length=decode_ceiling,
        run_length_maximum_output_length=decode_ceiling,
        zlib_maximum_output_length=decode_ceiling,
        zlib_maximum_recovery_input_length=min(
            decode_ceiling,
            _MAX_PDF_RECOVERY_BYTES,
        ),
        flate_maximum_columns=min(decode_ceiling, 250_000),
        flate_maximum_row_length=min(decode_ceiling, 4_000_000),
        image_maximum_buffer_size=decode_ceiling,
    ):
        decoded = stream.get_data()
    if not isinstance(decoded, bytes):
        raise DocumentCorruptError(
            "The PDF document contains an invalid font stream."
        )
    budget.consume_font_stream(len(decoded))
    return decoded


def _preflight_cid_widths(
    widths: ArrayObject,
    budget: _PdfWorkBudget,
) -> None:
    """Measure compact CID width ranges before pypdf expands their entries."""

    index = 0
    while index < len(widths):
        start_value = _resolved_pdf_object(widths[index])
        if not isinstance(start_value, (int, float)):
            raise DocumentCorruptError(
                "The PDF document contains an invalid CID width range."
            )
        start = int(start_value)
        if start < 0 or index + 1 >= len(widths):
            raise DocumentCorruptError(
                "The PDF document contains an invalid CID width range."
            )
        next_value = _resolved_pdf_object(widths[index + 1])
        if isinstance(next_value, ArrayObject):
            if start + len(next_value) > 0x110000:
                raise DocumentCorruptError(
                    "The PDF document contains an invalid CID width range."
                )
            budget.consume_font_semantics(len(next_value))
            index += 2
            continue
        if (
            not isinstance(next_value, (int, float))
            or index + 2 >= len(widths)
            or not isinstance(
                _resolved_pdf_object(widths[index + 2]),
                (int, float),
            )
        ):
            raise DocumentCorruptError(
                "The PDF document contains an invalid CID width range."
            )
        end = int(next_value)
        if end < start or end > 0x10FFFF:
            raise DocumentCorruptError(
                "The PDF document contains an invalid CID width range."
            )
        budget.consume_font_semantics(end - start + 1)
        index += 3


def _preflight_font_structure(
    font: DictionaryObject,
    budget: _PdfWorkBudget,
) -> None:
    """Bound encoding, width, descendant, and Type3 resource expansion."""

    def validate_font_name(value: Any) -> None:
        """Reject names whose repeated parser copies could evade work budgets."""

        font_name = _resolved_pdf_object(value)
        if not isinstance(font_name, NameObject):
            raise DocumentCorruptError(
                "The PDF document contains an invalid font name."
            )
        if len(str(font_name)) > _MAX_PDF_FONT_NAME_CODE_POINTS:
            raise DocumentContentLimitError(
                "The PDF document contains an oversized font name."
            )

    for name_key in ("/BaseFont", "/Subtype"):
        if name_key not in font:
            continue
        validate_font_name(font.raw_get(name_key))

    # pypdf creates a fresh approximately 256-entry encoding dictionary and,
    # for common core fonts, another approximately 256-entry width dictionary
    # for every alias and Form visit.  Charge that unavoidable object baseline
    # before any input-driven maps or ranges are added below.
    budget.consume_font_semantics(_PDF_FONT_BASELINE_SEMANTIC_ENTRIES)
    encoding = _resolved_pdf_object(font.get("/Encoding"))
    if isinstance(encoding, NameObject):
        # Unknown codec names are retained by pypdf and looked up again for
        # every shown byte string, so the name itself needs a hard bound.
        validate_font_name(encoding)
    elif isinstance(encoding, DictionaryObject):
        if "/BaseEncoding" in encoding:
            validate_font_name(encoding.raw_get("/BaseEncoding"))
    elif encoding is not None and not isinstance(encoding, NullObject):
        raise DocumentCorruptError(
            "The PDF document contains an invalid font encoding."
        )
    if isinstance(encoding, DictionaryObject) and "/Differences" in encoding:
        differences = _resolved_pdf_object(encoding.get("/Differences"))
        if not isinstance(differences, ArrayObject):
            raise DocumentCorruptError(
                "The PDF document contains invalid font encoding differences."
            )
        budget.consume_font_semantics(len(differences))

    widths = _resolved_pdf_object(font.get("/Widths"))
    if widths is not None and not isinstance(widths, NullObject):
        if not isinstance(widths, ArrayObject):
            raise DocumentCorruptError(
                "The PDF document contains invalid font widths."
            )
        first_character = _resolved_pdf_object(font.get("/FirstChar", 0))
        if not isinstance(first_character, (int, float)):
            raise DocumentCorruptError(
                "The PDF document contains invalid font widths."
            )
        first = int(first_character)
        if first < 0 or first + len(widths) > 0x110000:
            raise DocumentCorruptError(
                "The PDF document contains invalid font widths."
            )
        budget.consume_font_semantics(len(widths))

    char_procs = _resolved_pdf_object(font.get("/CharProcs"))
    if char_procs is not None and not isinstance(char_procs, NullObject):
        if not isinstance(char_procs, DictionaryObject):
            raise DocumentCorruptError(
                "The PDF document contains invalid Type3 character procedures."
            )
        budget.consume_font_semantics(len(char_procs))

    descendants = _resolved_pdf_object(font.get("/DescendantFonts"))
    if descendants is None or isinstance(descendants, NullObject):
        return
    if not isinstance(descendants, ArrayObject):
        raise DocumentCorruptError(
            "The PDF document contains invalid descendant fonts."
        )
    budget.consume_font_semantics(len(descendants))
    for descendant_value in descendants:
        descendant = _resolved_pdf_object(descendant_value)
        if not isinstance(descendant, DictionaryObject):
            raise DocumentCorruptError(
                "The PDF document contains an invalid descendant font."
            )
        cid_widths = _resolved_pdf_object(descendant.get("/W"))
        if cid_widths is None or isinstance(cid_widths, NullObject):
            continue
        if not isinstance(cid_widths, ArrayObject):
            raise DocumentCorruptError(
                "The PDF document contains invalid CID widths."
            )
        _preflight_cid_widths(cid_widths, budget)


def _font_encoding_factor(font: DictionaryObject) -> int:
    """Return the bounded expansion of one encoded input character."""

    encoding = _resolved_pdf_object(font.get("/Encoding"))
    if not isinstance(encoding, DictionaryObject):
        return 1
    differences = _resolved_pdf_object(encoding.get("/Differences"))
    if differences is None or isinstance(differences, NullObject):
        return 1
    if not isinstance(differences, ArrayObject):
        raise DocumentCorruptError(
            "The PDF document contains invalid font encoding differences."
        )

    maximum = 1
    for difference_value in differences:
        difference = _resolved_pdf_object(difference_value)
        if isinstance(difference, int):
            continue
        if not isinstance(difference, NameObject):
            raise DocumentCorruptError(
                "The PDF document contains an invalid glyph name."
            )
        name_length = len(str(difference))
        if name_length > _MAX_PDF_GLYPH_NAME_CODE_POINTS:
            raise DocumentContentLimitError(
                "The PDF document contains an oversized glyph name."
            )
        # pypdf substitutes known Adobe names with at most four code points,
        # but deliberately preserves unknown names verbatim.  The larger of
        # that known-name ceiling and the actual name therefore bounds both.
        maximum = max(
            maximum,
            _MAX_PDF_ENCODING_CODE_POINTS,
            name_length,
        )
    return maximum


def _preflight_font_resources(
    resources: DictionaryObject,
    budget: _PdfWorkBudget,
) -> None:
    """Bound every font resource and stream pypdf will inspect for text.

    Plain text extraction constructs every font in a resource dictionary,
    including fonts that no content operator selects.  It decodes ToUnicode
    maps and, for Type1 fonts without one, may decode an embedded Type1 or CFF
    program to recover a character map.  Mirroring those paths here prevents
    unused compressed fonts from bypassing the aggregate expansion budget.
    """

    raw_fonts = _resolved_pdf_object(resources.get("/Font"))
    if raw_fonts is None or isinstance(raw_fonts, NullObject):
        budget.remember_font_factors(resources, {})
        return
    if not isinstance(raw_fonts, DictionaryObject):
        raise DocumentCorruptError(
            "The PDF document contains invalid font resources."
        )
    budget.consume_font_resources(len(raw_fonts))
    factors: dict[str, int] = {}
    for resource_name in raw_fonts:
        font = _resolved_pdf_object(raw_fonts.raw_get(resource_name))
        if not isinstance(font, DictionaryObject):
            raise DocumentCorruptError(
                "The PDF document contains an invalid font resource."
            )
        _preflight_font_structure(font, budget)
        encoding_factor = _font_encoding_factor(font)
        output_factor = encoding_factor
        if "/ToUnicode" in font:
            to_unicode = _resolved_pdf_object(font.raw_get("/ToUnicode"))
            if isinstance(to_unicode, StreamObject):
                decoded = _decode_font_stream(to_unicode, budget)
                mapping_entries, mapping_factor = _analyze_to_unicode(decoded)
                budget.consume_font_semantics(mapping_entries)
                output_factor = encoding_factor * mapping_factor
            else:
                # pypdf treats a named or null ToUnicode value as a tiny
                # identity range rather than decoding a stream.
                budget.consume_font_semantics(2)
                output_factor = encoding_factor
            factors[str(resource_name)] = output_factor
            continue
        if _resolved_pdf_object(font.get("/Subtype")) != NameObject("/Type1"):
            factors[str(resource_name)] = output_factor
            continue
        descriptor = _resolved_pdf_object(font.get("/FontDescriptor"))
        if not isinstance(descriptor, DictionaryObject):
            factors[str(resource_name)] = output_factor
            continue
        font_file = _resolved_pdf_object(descriptor.get("/FontFile"))
        if isinstance(font_file, StreamObject):
            decoded = _decode_font_stream(font_file, budget)
            # Type1 recovery examines clear-text ``dup`` declarations and can
            # retain at most one mapping per declaration.  Counting candidate
            # lines is conservative and avoids duplicating that parser here.
            budget.consume_font_semantics(
                decoded.count(b"\ndup")
                + decoded.count(b"\rdup")
                + int(decoded.startswith(b"dup"))
            )
            output_factor *= _MAX_PDF_ENCODING_CODE_POINTS
            factors[str(resource_name)] = output_factor
            continue
        font_file3 = _resolved_pdf_object(descriptor.get("/FontFile3"))
        if (
            isinstance(font_file3, StreamObject)
            and _resolved_pdf_object(font_file3.get("/Subtype"))
            == NameObject("/Type1C")
        ):
            # Preflight this optional path even when fontTools is absent from
            # the current environment, keeping the safety result deterministic
            # if that optional parser becomes available later.
            _decode_font_stream(font_file3, budget)
            budget.consume_font_semantics(256)
            output_factor *= _MAX_PDF_ENCODING_CODE_POINTS
        factors[str(resource_name)] = output_factor
    budget.remember_font_factors(resources, factors)


def _content_plan(
    stream: StreamObject,
    reader: PdfReader,
    budget: _PdfWorkBudget,
    *,
    cache: bool,
) -> tuple[int, list[tuple[Any, bytes]]]:
    """Decode and parse one unique content stream under allocation ceilings.

    Plans are cached by resolved object identity so repeated Form invocations
    do not make the preflight itself reparsed-work quadratic.  Aggregate bytes
    and operations are still charged for every invocation by the caller,
    matching the work pypdf will perform during extraction.
    """

    plan_key = id(stream)
    cached = budget.plans.get(plan_key) if cache else None
    if cached is not None and cached[0] is stream:
        return cached[1], cached[2]
    decoded = stream.get_data()
    if not isinstance(decoded, bytes):
        raise DocumentCorruptError(
            "The PDF document contains an invalid content stream."
        )
    if len(decoded) > _MAX_PDF_CONTENT_STREAM_BYTES:
        raise DocumentContentLimitError(
            "A PDF content stream exceeds the safe parsing limit."
        )
    _validate_content_token_density(decoded)
    content = (
        stream
        if isinstance(stream, ContentStream)
        else ContentStream(stream, reader, "bytes")
    )
    operations = content.operations
    if len(operations) > _MAX_PDF_OPERATIONS_PER_STREAM:
        raise DocumentContentLimitError(
            "A PDF content stream exceeds the operation limit."
        )
    plan = (len(decoded), operations)
    if cache:
        # Retaining the resolved object makes the identity key immune to Python
        # ``id`` reuse.  Page ContentStreams are intentionally not cached, so
        # their parsed operation graphs can be released after each page.
        budget.plans[plan_key] = (stream, plan[0], plan[1])
    return plan


def _preflight_content_stream(
    stream: StreamObject,
    resources: DictionaryObject,
    reader: PdfReader,
    budget: _PdfWorkBudget,
    active_forms: set[int],
    *,
    form_depth: int,
    is_form: bool,
) -> None:
    """Walk page/Form operations exactly as invoked before text extraction.

    A Form is charged each time a ``Do`` operation invokes it; merely summing
    unique stream objects would let a tiny Form create unbounded repeated work.
    Cycles and excessive nesting fail closed because pypdf otherwise logs and
    skips them, which could turn an over-budget document into partial success.
    """

    if is_form:
        budget.consume_form_invocation()
        if form_depth > min(budget.limits.max_xml_depth, _MAX_PDF_TREE_DEPTH):
            raise DocumentContentLimitError(
                "The PDF document exceeds the Form nesting limit."
            )

    _preflight_font_resources(resources, budget)

    decoded_bytes, operations = _content_plan(
        stream,
        reader,
        budget,
        cache=is_form,
    )
    budget.consume_stream(decoded_bytes, len(operations))
    for operands, operator in operations:
        if operator != b"Do":
            continue
        if not isinstance(operands, list) or len(operands) != 1:
            raise DocumentCorruptError(
                "The PDF document contains an invalid Form invocation."
            )
        resource_name = operands[0]
        if not isinstance(resource_name, NameObject):
            raise DocumentCorruptError(
                "The PDF document contains an invalid Form invocation."
            )
        xobjects = _resolved_pdf_object(resources.get("/XObject"))
        if not isinstance(xobjects, DictionaryObject) or resource_name not in xobjects:
            raise DocumentCorruptError(
                "The PDF document references an invalid external object."
            )
        external_object = _resolved_pdf_object(xobjects.raw_get(resource_name))
        if not isinstance(external_object, StreamObject):
            raise DocumentCorruptError(
                "The PDF document references an invalid external object."
            )
        subtype = _resolved_pdf_object(external_object.get("/Subtype"))
        if subtype == NameObject("/Image"):
            continue
        if subtype != NameObject("/Form"):
            raise DocumentCorruptError(
                "The PDF document references an unsupported external object."
            )
        form_key = id(external_object)
        if form_key in active_forms:
            raise DocumentContentLimitError(
                "The PDF document contains a cyclic Form reference."
            )
        active_forms.add(form_key)
        try:
            _preflight_content_stream(
                external_object,
                _pdf_resources(external_object, budget.limits),
                reader,
                budget,
                active_forms,
                form_depth=form_depth + 1,
                is_form=True,
            )
        finally:
            active_forms.remove(form_key)


def _preflight_page_content(
    page: DictionaryObject,
    reader: PdfReader,
    budget: _PdfWorkBudget,
) -> None:
    """Preflight one page and every Form reached from its content stream."""

    resources = _pdf_resources(page, budget.limits)
    raw_contents = _resolved_pdf_object(page.get("/Contents"))
    if raw_contents is None or isinstance(raw_contents, NullObject):
        # pypdf constructs fonts before it notices that content is absent.
        _preflight_font_resources(resources, budget)
        return
    content = ContentStream(raw_contents, reader, "bytes")
    _preflight_content_stream(
        content,
        resources,
        reader,
        budget,
        set(),
        form_depth=0,
        is_form=False,
    )


def _classify_pdf_extraction_error(error: Exception) -> DocumentError:
    """Map a nested native failure before pypdf can log and suppress it."""

    if isinstance(error, DocumentError):
        return error
    if isinstance(error, (LimitReachedError, RecursionError, MemoryError)):
        return DocumentContentLimitError(
            "The PDF document exceeds a safe parsing limit."
        )
    if isinstance(error, DependencyError):
        return DocumentUnsupportedFeatureError(
            "The PDF document requires an unavailable optional parser."
        )
    if isinstance(
        error,
        (
            PdfStreamError,
            PdfReadError,
            ParseError,
            PyPdfError,
            UnicodeError,
            ValueError,
            TypeError,
            KeyError,
            IndexError,
            OverflowError,
        ),
    ):
        return DocumentCorruptError(
            "The PDF document is damaged or internally inconsistent."
        )
    return DocumentLoadFailedError(
        "The PDF document could not be loaded safely."
    )


def _bounded_page_text(
    page: Any,
    guard: _PdfTextGuard,
    budget: _PdfWorkBudget,
) -> str | None:
    """Extract one page while isolating callback accounting for every Form.

    pypdf forwards the same visitor into recursive Form extraction, then calls
    it once more with the completed Form text.  A page-instance override gives
    each recursive call a child guard without changing global parser state or
    counting the Form twice in its parent.  The override is restored even when
    the private budget sentinel aborts from deep inside a Form.
    """

    original_extract_xform_text = page.extract_xform_text
    original_extract_text_xform = page._extract_text__xform
    missing_override = object()
    previous_text_override = vars(page).get(
        "extract_xform_text",
        missing_override,
    )
    previous_dispatch_override = vars(page).get(
        "_extract_text__xform",
        missing_override,
    )

    def _extract_xform_text(
        xform: Any,
        orientations: tuple[int, ...] = (0, 90, 270, 360),
        space_width: float = 200.0,
        visitor_operand_before: Any = None,
        visitor_operand_after: Any = None,
        visitor_text: Any = None,
        *,
        known_ids: set[int] | None = None,
        traversal_state: Any = None,
    ) -> str:
        """Run one Form with a budget context distinct from its caller."""

        parent_guard = getattr(visitor_text, "__self__", None)
        child_guard = (
            parent_guard.child()
            if isinstance(parent_guard, _PdfTextGuard)
            else None
        )
        child_allowance = (
            None
            if child_guard is None
            else child_guard.remaining_code_points
        )
        child_resources = _pdf_resources(xform, budget.limits)
        child_operand_guard = (
            _PdfOperandGuard(
                child_guard,
                budget.factors_for(child_resources),
            )
            if child_guard is not None
            else None
        )
        text = original_extract_xform_text(
            xform,
            orientations,
            space_width,
            (
                child_operand_guard.before
                if child_operand_guard is not None
                else visitor_operand_before
            ),
            (
                child_operand_guard.after
                if child_operand_guard is not None
                else visitor_operand_after
            ),
            child_guard.visit if child_guard is not None else visitor_text,
            known_ids=known_ids,
            traversal_state=traversal_state,
        )
        # Current pypdf versions visit every emitted fragment.  Retain this
        # postcondition so a future callback change still fails closed, although
        # that fallback can only run after the changed parser returns the Form.
        if (
            child_guard is not None
            and child_allowance is not None
            and len(text) > child_allowance
        ):
            child_guard.reject()
        return text

    def _extract_text_xform_dispatch(*args: Any, **kwargs: Any) -> str | None:
        """Promote failures across pypdf's suppressing Form dispatcher."""

        try:
            return original_extract_text_xform(*args, **kwargs)
        except Exception as error:
            raise _PdfExtractionAbort(
                _classify_pdf_extraction_error(error),
                error,
            ) from None

    page_object = cast(Any, page)
    page_object.extract_xform_text = _extract_xform_text
    page_object._extract_text__xform = _extract_text_xform_dispatch
    try:
        resources = _pdf_resources(page, budget.limits)
        operand_guard = _PdfOperandGuard(
            guard,
            budget.factors_for(resources),
        )
        return page.extract_text(
            visitor_operand_before=operand_guard.before,
            visitor_operand_after=operand_guard.after,
            visitor_text=guard.visit,
        )
    finally:
        if previous_text_override is missing_override:
            del page_object.extract_xform_text
        else:
            page_object.extract_xform_text = previous_text_override
        if previous_dispatch_override is missing_override:
            del page_object._extract_text__xform
        else:
            page_object._extract_text__xform = previous_dispatch_override


def _embedded_title(reader: PdfReader) -> DocumentTitle | None:
    """Return a safe embedded title without making malformed metadata fatal."""

    metadata = reader.metadata
    if metadata is None:
        return None
    raw_title = metadata.title
    if not isinstance(raw_title, str):
        return None
    if (
        not _has_meaningful_text(raw_title)
        or len(raw_title) > MAX_DOCUMENT_TITLE_CODE_POINTS
        or _has_unsafe_control(raw_title)
    ):
        return None
    return DocumentTitle(text=raw_title, source="embedded")


class PdfDocumentLoader:
    """Extract bounded page text from standards-conforming PDF documents."""

    @property
    def loader_id(self) -> str:
        """Return the stable adapter identity recorded with loaded results."""

        return "pdf-pypdf"

    @property
    def loader_version(self) -> str:
        """Return the raw-output version including the pinned parser version."""

        return f"1.0.0+pypdf-{pypdf_version}"

    @property
    def routes(self) -> frozenset[DocumentRoute]:
        """Return the exact PDF suffix and media-type route."""

        return _ROUTES

    def load(
        self,
        source: DocumentSource,
        data: bytes,
        limits: DocumentLoadLimits,
    ) -> LoadedDocument:
        """Load verified PDF bytes under explicit input and output budgets.

        Encrypted files are rejected even when an empty password happens to
        open them.  Supporting passwords would introduce a separate secret
        handling contract that this local raw-loader milestone does not own.
        """

        self._validate_request(source, data, limits)
        configuration = _configuration_for(limits)
        try:
            with apply_configuration(configuration):
                reader = self._open_reader(data, limits)
                if reader.is_encrypted:
                    raise DocumentEncryptedError(
                        "Encrypted PDF documents are not supported."
                    )
                return self._extract(reader, source, limits)
        except _PdfExtractionAbort as error:
            if error.cause is None:
                raise error.error from None
            raise error.error from error.cause
        except DocumentError:
            raise
        except EmptyFileError as error:
            raise DocumentEmptyError(
                "The PDF document contains no bytes."
            ) from error
        except (WrongPasswordError, FileNotDecryptedError) as error:
            raise DocumentEncryptedError(
                "Encrypted PDF documents are not supported."
            ) from error
        except DependencyError as error:
            raise DocumentUnsupportedFeatureError(
                "The PDF document requires an unavailable optional parser."
            ) from error
        except (LimitReachedError, RecursionError, MemoryError) as error:
            raise DocumentContentLimitError(
                "The PDF document exceeds a safe parsing limit."
            ) from error
        except (
            PdfStreamError,
            PdfReadError,
            ParseError,
            PyPdfError,
            UnicodeError,
            ValueError,
            TypeError,
            KeyError,
            IndexError,
            OverflowError,
        ) as error:
            raise DocumentCorruptError(
                "The PDF document is damaged or internally inconsistent."
            ) from error
        except Exception as error:
            # pypdf documents that malformed PDFs can raise non-PyPdfError
            # exceptions.  Translate at the adapter boundary so a native error
            # or path can never become part of a renderer-facing message.
            raise DocumentLoadFailedError(
                "The PDF document could not be loaded safely."
            ) from error

    @staticmethod
    def _validate_request(
        source: DocumentSource,
        data: bytes,
        limits: DocumentLoadLimits,
    ) -> None:
        """Validate metadata and exact immutable byte-snapshot identity."""

        if not isinstance(source, DocumentSource):
            raise DocumentValidationError("source must be DocumentSource.")
        if not isinstance(data, bytes):
            raise DocumentValidationError("data must be immutable bytes.")
        if not isinstance(limits, DocumentLoadLimits):
            raise DocumentValidationError("limits must be DocumentLoadLimits.")
        route = (PurePath(source.file_name).suffix.casefold(), source.media_type)
        if route not in _ROUTES:
            raise DocumentUnsupportedFormatError(
                "The document does not match the PDF loader route."
            )
        if not data:
            raise DocumentEmptyError("The PDF document contains no bytes.")
        if len(data) != source.size_bytes:
            raise DocumentReadError(
                "Verified document bytes do not match their metadata."
            )
        if len(data) > limits.max_source_bytes:
            raise DocumentTooLargeError(
                "The PDF document exceeds the configured byte limit."
            )
        if not data.startswith(_PDF_HEADER):
            raise DocumentCorruptError(
                "The PDF document has an invalid content signature."
            )

    @staticmethod
    def _open_reader(data: bytes, limits: DocumentLoadLimits) -> PdfReader:
        """Open one strict reader and classify missing crypto as encryption."""

        try:
            return PdfReader(
                BytesIO(data),
                strict=True,
                root_object_recovery_limit=min(
                    limits.max_xml_elements,
                    1_000,
                ),
            )
        except DependencyError as error:
            # During reader construction pypdf only needs an optional runtime
            # dependency while verifying an encrypted document.  Passwords are
            # unsupported regardless of which cipher the file requests.
            raise DocumentEncryptedError(
                "Encrypted PDF documents are not supported."
            ) from error

    def _extract(
        self,
        reader: PdfReader,
        source: DocumentSource,
        limits: DocumentLoadLimits,
    ) -> LoadedDocument:
        """Extract one block per non-empty page without semantic guessing."""

        page_count = len(reader.pages)
        if page_count == 0:
            raise DocumentEmptyError("The PDF document contains no pages.")
        if page_count > limits.max_pages:
            raise DocumentContentLimitError(
                "The PDF document exceeds the configured page limit."
            )

        title = _embedded_title(reader)
        text_code_points = 0 if title is None else len(title.text)
        if text_code_points > limits.max_text_code_points:
            raise DocumentContentLimitError(
                "The PDF document exceeds the text limit."
            )
        work_budget = _PdfWorkBudget(limits)
        text_guard = _PdfTextGuard()
        blocks: list[DocumentBlock] = []
        for page_number, page in enumerate(reader.pages, start=1):
            _preflight_page_content(page, reader, work_budget)
            text_guard.reset(limits.max_text_code_points - text_code_points)
            text = _bounded_page_text(page, text_guard, work_budget)
            # The mutable check is intentionally redundant with the private
            # BaseException sentinel.  It keeps the boundary fail-closed if a
            # future pypdf release changes its callback exception handling.
            if text_guard.exceeded:
                raise DocumentContentLimitError(
                    "The PDF document exceeds the text limit."
                )
            if text is None:
                continue
            if not isinstance(text, str) or _has_unsafe_control(text):
                raise DocumentCorruptError(
                    "The PDF document contains invalid extracted text."
                )
            if not _has_meaningful_text(text):
                continue
            text_code_points += len(text)
            if text_code_points > limits.max_text_code_points:
                raise DocumentContentLimitError(
                    "The PDF document exceeds the text limit."
                )
            if len(blocks) >= limits.max_blocks:
                raise DocumentContentLimitError(
                    "The PDF document exceeds the block limit."
                )
            blocks.append(
                DocumentBlock(
                    ordinal=len(blocks),
                    kind="paragraph",
                    text=text,
                    page_number=page_number,
                )
            )

        if not blocks:
            raise DocumentEmptyError(
                "The PDF document contains no extractable text."
            )
        return LoadedDocument(
            schema_version=DOCUMENT_SCHEMA_VERSION,
            source=source,
            document_format="pdf",
            loader_id=self.loader_id,
            loader_version=self.loader_version,
            blocks=tuple(blocks),
            limits=limits,
            title=title,
            page_count=page_count,
        )
