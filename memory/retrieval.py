"""Retrieve relevant profile, summary, and long-term memory items."""

import re
from dataclasses import dataclass
from typing import Literal, TypedDict

from .conversation_summary import ConversationSummary
from .long_term_memory import LongTermMemoryRecord
from .profile import Profile


MemoryRetrievalSource = Literal[
    "profile",
    "conversation_summary",
    "long_term_memory",
]


class RetrievedMemory(TypedDict):
    """One relevant memory with provenance and ranking metadata."""

    source: MemoryRetrievalSource
    key: str
    value: str
    source_type: str
    source_text: str
    timestamp: str | None
    confidence: float
    relevance: float


@dataclass(frozen=True)
class _RetrievalCandidate:
    """Normalize heterogeneous memory sources before relevance scoring.

    ``key_text`` may include hidden search hints, while the public ``key``
    remains the original field name presented to prompt-building code.
    """

    source: MemoryRetrievalSource
    key: str
    value: str
    source_type: str
    source_text: str
    timestamp: str | None
    confidence: float
    key_text: str


# English words and overlapping CJK bigrams support local bilingual matching
# without downloading a tokenizer or embedding model.
_ASCII_WORD_PATTERN = re.compile(
    r"[A-Za-z0-9]+"
)
_CJK_SEQUENCE_PATTERN = re.compile(
    r"[\u3400-\u4dbf\u4e00-\u9fff]+"
)

_ENGLISH_STOP_WORDS = frozenset(
    {
        "a",
        "about",
        "am",
        "an",
        "and",
        "are",
        "be",
        "do",
        "does",
        "elysia",
        "for",
        "how",
        "i",
        "in",
        "is",
        "it",
        "me",
        "memory",
        "my",
        "of",
        "on",
        "or",
        "please",
        "remember",
        "tell",
        "that",
        "the",
        "this",
        "to",
        "was",
        "what",
        "when",
        "where",
        "which",
        "who",
        "why",
        "with",
        "you",
    }
)

_CJK_STOP_TERMS = frozenset(
    {
        "什么",
        "一下",
        "可以",
        "告诉",
        "爱莉",
        "莉希",
        "希雅",
        "记住",
        "记得",
        "记忆",
    }
)

# Search hints connect natural questions to terse schema field names.
_PROFILE_SEARCH_HINTS: dict[str, str] = {
    "user_name": (
        "user name username 姓名 名字 用户"
    ),
    "assistant_name": (
        "assistant name elysia 助手 名字 爱莉希雅"
    ),
    "languages": (
        "language languages preferred reply "
        "Chinese English 语言 中文 英文 偏好 回复"
    ),
    "project": (
        "project goal work 项目 目标 开发"
    ),
    "launch_count": (
        "launch count launches session sessions "
        "启动 次数 会话"
    ),
}

_SUMMARY_SEARCH_HINTS: dict[str, str] = {
    "facts": (
        "fact facts information 事实 信息"
    ),
    "decisions": (
        "decision decisions decided 决定 选择"
    ),
    "action_items": (
        "action actions task tasks todo "
        "待办 任务 行动"
    ),
    "unresolved_questions": (
        "question questions unresolved open "
        "问题 未解决 待确认"
    ),
}


class MemoryRetriever:
    """Rank locally stored memory items using text overlap."""

    def __init__(
        self,
        result_limit: int = 5,
    ) -> None:
        """Create a retriever that returns at most ``result_limit`` items.

        Raises:
            ValueError: If the limit is not a positive, non-boolean integer.
        """

        if (
            not isinstance(result_limit, int)
            or isinstance(result_limit, bool)
            or result_limit <= 0
        ):
            raise ValueError(
                "Memory retrieval limit must be "
                "a positive integer."
            )

        self._result_limit = result_limit

    @property
    def result_limit(self) -> int:
        """Return the maximum number of ranked items emitted per query."""

        return self._result_limit

    def retrieve(
        self,
        query: str,
        profile: Profile,
        conversation_summary: (
            ConversationSummary | None
        ),
        long_term_memories: (
            list[LongTermMemoryRecord]
        ),
    ) -> list[RetrievedMemory]:
        """Return relevant items from strongest to weakest.

        Profile, conversation-summary, and long-term records are normalized,
        scored using bilingual term overlap, and truncated to ``result_limit``.

        Raises:
            ValueError: If ``query`` contains no non-whitespace text.
        """

        cleaned_query = query.strip()

        if not cleaned_query:
            raise ValueError(
                "Memory retrieval query cannot be empty."
            )

        query_terms = _extract_terms(
            cleaned_query
        )

        if not query_terms:
            return []

        results: list[RetrievedMemory] = []

        # Score all sources through the same normalized candidate contract.
        for candidate in _build_candidates(
            profile,
            conversation_summary,
            long_term_memories,
        ):
            relevance = _calculate_relevance(
                query_terms,
                candidate,
            )

            if relevance <= 0.0:
                continue

            results.append(
                {
                    "source": candidate.source,
                    "key": candidate.key,
                    "value": candidate.value,
                    "source_type": (
                        candidate.source_type
                    ),
                    "source_text": (
                        candidate.source_text
                    ),
                    "timestamp": (
                        candidate.timestamp
                    ),
                    "confidence": (
                        candidate.confidence
                    ),
                    "relevance": relevance,
                }
            )

        # Confidence breaks relevance ties in favor of stronger provenance.
        results.sort(
            key=lambda item: (
                item["relevance"],
                item["confidence"],
            ),
            reverse=True,
        )

        return results[
            : self._result_limit
        ]


def _build_candidates(
    profile: Profile,
    conversation_summary: (
        ConversationSummary | None
    ),
    long_term_memories: (
        list[LongTermMemoryRecord]
    ),
) -> list[_RetrievalCandidate]:
    """Combine candidates from every currently available memory source."""

    candidates = _build_profile_candidates(
        profile
    )

    if conversation_summary is not None:
        candidates.extend(
            _build_summary_candidates(
                conversation_summary
            )
        )

    candidates.extend(
        _build_long_term_candidates(
            long_term_memories
        )
    )

    return candidates


def _build_profile_candidates(
    profile: Profile,
) -> list[_RetrievalCandidate]:
    """Convert non-empty profile fields into highest-confidence candidates."""

    profile_values: dict[str, str] = {
        "user_name": profile["user_name"],
        "assistant_name": (
            profile["assistant_name"]
        ),
        "languages": ", ".join(
            profile["languages"]
        ),
        "project": profile["project"],
        "launch_count": str(
            profile["launch_count"]
        ),
    }

    return [
        _RetrievalCandidate(
            source="profile",
            key=field_name,
            value=value,
            source_type="profile_field",
            source_text=(
                f"profile.{field_name}"
            ),
            timestamp=None,
            confidence=1.0,
            key_text=(
                f"{field_name} "
                f"{_PROFILE_SEARCH_HINTS[field_name]}"
            ),
        )
        for field_name, value
        in profile_values.items()
        if value.strip()
    ]


def _build_summary_candidates(
    summary: ConversationSummary,
) -> list[_RetrievalCandidate]:
    """Flatten structured summary categories into individually ranked items."""

    candidates: list[
        _RetrievalCandidate
    ] = []

    summary_entries: tuple[
        tuple[str, list[str]],
        ...,
    ] = (
        (
            "facts",
            summary["content"]["facts"],
        ),
        (
            "decisions",
            summary["content"]["decisions"],
        ),
        (
            "action_items",
            summary["content"]["action_items"],
        ),
        (
            "unresolved_questions",
            summary["content"][
                "unresolved_questions"
            ],
        ),
    )

    for category, entries in summary_entries:
        for number, entry in enumerate(
            entries,
            start=1,
        ):
            candidates.append(
                _RetrievalCandidate(
                    source=(
                        "conversation_summary"
                    ),
                    key=(
                        f"{category}[{number}]"
                    ),
                    value=entry,
                    source_type=category,
                    source_text=(
                        "conversation messages "
                        f"{summary['source_start_timestamp']} "
                        "through "
                        f"{summary['source_end_timestamp']}"
                    ),
                    timestamp=(
                        summary["updated_at"]
                    ),
                    confidence=0.75,
                    key_text=(
                        f"{category} "
                        f"{_SUMMARY_SEARCH_HINTS[category]}"
                    ),
                )
            )

    return candidates


def _build_long_term_candidates(
    records: list[LongTermMemoryRecord],
) -> list[_RetrievalCandidate]:
    """Convert long-term records while preserving their provenance metadata."""

    return [
        _RetrievalCandidate(
            source="long_term_memory",
            key=record["key"],
            value=record["value"],
            source_type=record["source_type"],
            source_text=record["source_text"],
            timestamp=record["created_at"],
            # Explicit user statements outrank model-inferred memories.
            confidence=(
                1.0
                if record["source_type"]
                == "user_explicit"
                else 0.6
            ),
            key_text=record["key"],
        )
        for record in records
    ]


def _calculate_relevance(
    query_terms: set[str],
    candidate: _RetrievalCandidate,
) -> float:
    """Score query coverage with extra weight for key and value matches.

    Key matches contribute three units, value matches two, and source-text
    matches one. The final value is bounded to ``[0.0, 1.0]`` and rounded for
    stable prompt metadata and deterministic tests.
    """

    key_matches = (
        query_terms
        & _extract_terms(
            candidate.key_text
        )
    )
    value_matches = (
        query_terms
        & _extract_terms(
            candidate.value
        )
    )
    source_matches = (
        query_terms
        & _extract_terms(
            candidate.source_text
        )
    )

    matched_terms = (
        key_matches
        | value_matches
        | source_matches
    )

    if not matched_terms:
        return 0.0

    coverage = (
        len(matched_terms)
        / len(query_terms)
    )

    weighted_matches = 0

    for term in matched_terms:
        if term in key_matches:
            weighted_matches += 3
        elif term in value_matches:
            weighted_matches += 2
        else:
            weighted_matches += 1

    weighted_coverage = (
        weighted_matches
        / (3 * len(query_terms))
    )

    return round(
        min(
            1.0,
            (0.65 * coverage)
            + (
                0.35
                * weighted_coverage
            ),
        ),
        3,
    )


def _extract_terms(
    text: str,
) -> set[str]:
    """Extract normalized English words and overlapping Chinese bigrams."""

    terms = {
        word.casefold()
        for word
        in _ASCII_WORD_PATTERN.findall(text)
        if (
            len(word) > 1
            and word.casefold()
            not in _ENGLISH_STOP_WORDS
        )
    }

    for sequence in (
        _CJK_SEQUENCE_PATTERN.findall(text)
    ):
        if len(sequence) == 1:
            terms.add(sequence)
            continue

        # Overlapping bigrams allow partial matching without Chinese word
        # segmentation, for example ``汽车颜色`` -> ``汽车/车颜/颜色``.
        for index in range(
            len(sequence) - 1
        ):
            term = sequence[
                index : index + 2
            ]

            if term not in _CJK_STOP_TERMS:
                terms.add(term)

    return terms
