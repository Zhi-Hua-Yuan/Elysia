"""Pure text normalization helpers for explicit memory commands."""

from __future__ import annotations

import re
import unicodedata


_WHITESPACE_PATTERN = re.compile(r"\s+")
_TRAILING_SENTENCE_PUNCTUATION = "。.!！?？"
_MATCH_IGNORED_CHARACTERS = str.maketrans(
    "",
    "",
    " \t\r\n,，。.!！?？:：;；'\"“”‘’()（）[]【】",
)


def normalize_memory_text(value: str) -> str:
    """Normalize presentation text without performing semantic rewriting."""
    if not isinstance(value, str):
        raise TypeError("memory text must be a string")

    normalized = unicodedata.normalize("NFKC", value)
    normalized = _WHITESPACE_PATTERN.sub(" ", normalized).strip()
    return normalized.rstrip(_TRAILING_SENTENCE_PUNCTUATION).rstrip()


def normalize_memory_match_text(value: str) -> str:
    """Normalize text for deterministic deduplication and exact matching."""
    normalized = normalize_memory_text(value).casefold()
    return normalized.translate(_MATCH_IGNORED_CHARACTERS)
