"""Deterministic parser for explicitly requested memory operations."""

from __future__ import annotations

import re
import unicodedata

from .command_types import (
    MemoryCommand,
    MemoryCommandKind,
    MemoryCommandParseReason,
    MemoryCommandParseResult,
    MemoryCommandParseStatus,
)
from .normalization import normalize_memory_text
from .types import MemoryCategory


DEFAULT_MAX_COMMAND_CHARS = 2048

_CONFIRM_CLEAR_PATTERN = re.compile(
    r"^(?:请)?确认(?:要)?清空(?:所有|全部)?(?:持久)?记忆$"
)
_CANCEL_CLEAR_PATTERN = re.compile(
    r"^(?:取消清空(?:所有|全部)?(?:持久)?(?:记忆)?|"
    r"不要清空(?:所有|全部)?(?:持久)?(?:记忆)?了?)$"
)
_CLEAR_PATTERN = re.compile(
    r"^(?:请)?(?:清空(?:所有|全部)?(?:持久)?记忆|"
    r"忘掉(?:所有|全部)(?:关于我)?的?(?:持久)?记忆)$"
)
_LIST_PATTERN = re.compile(
    r"^(?:你)?(?:都)?记得我什么(?:吗)?$|"
    r"^(?:查看|列出)(?:你的|我的)?(?:持久)?记忆$"
)
_DELETE_PATTERNS = (
    re.compile(r"^(?:请)?忘(?:掉|记)(?:关于)?[，,:：\s]*(?P<target>.+)$"),
    re.compile(r"^(?:请)?删除(?:关于)?[，,:：\s]*(?P<target>.+?)(?:这条|这项)?记忆$"),
)
_ADDRESS_PATTERN = re.compile(
    r"^(?:(?:以后)(?:请)?|请)叫我(?:为|作|做)?[，,:：\s]*(?P<value>.*)$"
)
_SAVE_PATTERN = re.compile(
    r"^(?:(?:请|帮我|你要)?记住|我希望你记住)[，,:：\s]*(?P<value>.*)$"
)
_NEGATED_PATTERN = re.compile(r"^(?:请)?(?:不要|别|不用)记住(?:.*)$")
_COMMAND_PREFIX_PATTERN = re.compile(
    r"^(?:请确认|确认|取消清空|不要清空|请清空|清空|忘掉|忘记|请忘掉|请忘记|"
    r"删除|请删除|以后叫我|以后请叫我|请叫我|记住|请记住|帮我记住|"
    r"你要记住|我希望你记住|不要记住|别记住|不用记住|你记得我什么|"
    r"你都记得我什么|查看.*记忆|列出.*记忆)"
)
_PREFERENCE_PATTERN = re.compile(
    r"^(?:"
    r"我(?:不喜欢|喜欢|更喜欢|偏好|讨厌|习惯|希望回答|希望回复|希望你回答|希望你回复)|"
    r"我的偏好是|"
    r"(?:回答|回复|说话)时|"
    r"和我说话时|"
    r"以后(?:回答|回复|说话)|"
    r"请(?:尽量|不要)"
    r")"
)
_EMBEDDED_COMMAND_PATTERN = re.compile(
    r"(?:确认清空|取消清空|清空(?:所有|全部)?.*记忆|删除.+记忆)"
)
_TARGET_SUFFIXES = (
    "这件事",
    "这个偏好",
    "这条记忆",
    "这项记忆",
    "的记忆",
)
_WRAPPING_QUOTES = (('"', '"'), ("'", "'"), ("“", "”"), ("‘", "’"))


class MemoryCommandParser:
    """Parse only anchored, explicit commands without model inference."""

    def __init__(self, max_command_chars: int = DEFAULT_MAX_COMMAND_CHARS) -> None:
        if type(max_command_chars) is not int or max_command_chars <= 0:
            raise ValueError("max_command_chars must be a positive integer")
        self._max_command_chars = max_command_chars

    def parse(self, text: str) -> MemoryCommandParseResult:
        """Return a typed result while leaving ordinary conversation untouched."""
        if not isinstance(text, str):
            raise TypeError("memory command input must be a string")

        normalized = normalize_memory_text(text)
        if not normalized:
            return self._not_command()

        looks_like_command = bool(_COMMAND_PREFIX_PATTERN.match(normalized))
        if not looks_like_command:
            return self._not_command()
        if len(normalized) > self._max_command_chars:
            return self._invalid(MemoryCommandParseReason.INPUT_TOO_LONG)
        if any(unicodedata.category(char).startswith("C") for char in text):
            return self._invalid(MemoryCommandParseReason.UNSUPPORTED_FORM)

        if _CONFIRM_CLEAR_PATTERN.fullmatch(normalized):
            return self._recognized(MemoryCommand(kind=MemoryCommandKind.CONFIRM_CLEAR))
        if _CANCEL_CLEAR_PATTERN.fullmatch(normalized):
            return self._recognized(MemoryCommand(kind=MemoryCommandKind.CANCEL_CLEAR))
        if _CLEAR_PATTERN.fullmatch(normalized):
            return self._recognized(MemoryCommand(kind=MemoryCommandKind.CLEAR))
        if _LIST_PATTERN.fullmatch(normalized):
            return self._recognized(MemoryCommand(kind=MemoryCommandKind.LIST))
        if re.fullmatch(r"(?:请)?(?:忘掉|忘记|删除)[，,:：\s]*", normalized):
            return self._invalid(MemoryCommandParseReason.INVALID_TARGET)

        for pattern in _DELETE_PATTERNS:
            match = pattern.fullmatch(normalized)
            if match:
                target = self._clean_target(match.group("target"))
                if not target:
                    return self._invalid(MemoryCommandParseReason.INVALID_TARGET)
                return self._recognized(
                    MemoryCommand(kind=MemoryCommandKind.DELETE, target=target)
                )

        address_match = _ADDRESS_PATTERN.fullmatch(normalized)
        if address_match:
            value = self._clean_value(address_match.group("value"))
            if not value:
                return self._invalid(MemoryCommandParseReason.EMPTY_VALUE)
            if self._has_embedded_command(value):
                return self._invalid(MemoryCommandParseReason.UNSUPPORTED_FORM)
            return self._recognized(
                MemoryCommand(
                    kind=MemoryCommandKind.SAVE,
                    category=MemoryCategory.PREFERRED_ADDRESS,
                    value=value,
                )
            )

        save_match = _SAVE_PATTERN.fullmatch(normalized)
        if save_match:
            value = self._clean_value(save_match.group("value"))
            if not value:
                return self._invalid(MemoryCommandParseReason.EMPTY_VALUE)
            if self._has_embedded_command(value):
                return self._invalid(MemoryCommandParseReason.UNSUPPORTED_FORM)
            return self._recognized(
                MemoryCommand(
                    kind=MemoryCommandKind.SAVE,
                    category=self._classify_value(value),
                    value=value,
                )
            )

        if _NEGATED_PATTERN.fullmatch(normalized):
            return self._invalid(MemoryCommandParseReason.NEGATED_COMMAND)
        return self._invalid(MemoryCommandParseReason.UNSUPPORTED_FORM)

    @staticmethod
    def _clean_value(value: str) -> str:
        normalized = normalize_memory_text(value)
        return MemoryCommandParser._strip_wrapping_quotes(normalized)

    @staticmethod
    def _clean_target(target: str) -> str:
        normalized = normalize_memory_text(target)
        normalized = MemoryCommandParser._strip_wrapping_quotes(normalized)
        for suffix in _TARGET_SUFFIXES:
            if normalized.endswith(suffix):
                normalized = normalized[: -len(suffix)].rstrip()
                break
        return MemoryCommandParser._strip_wrapping_quotes(normalized)

    @staticmethod
    def _strip_wrapping_quotes(value: str) -> str:
        stripped = value.strip()
        for opening, closing in _WRAPPING_QUOTES:
            if stripped.startswith(opening) and stripped.endswith(closing):
                return stripped[len(opening) : -len(closing)].strip()
        return stripped

    @staticmethod
    def _has_embedded_command(value: str) -> bool:
        return bool(_EMBEDDED_COMMAND_PATTERN.search(value))

    @staticmethod
    def _classify_value(value: str) -> MemoryCategory:
        if _PREFERENCE_PATTERN.match(value):
            return MemoryCategory.PREFERENCE
        return MemoryCategory.IMPORTANT_FACT

    @staticmethod
    def _recognized(command: MemoryCommand) -> MemoryCommandParseResult:
        return MemoryCommandParseResult(
            status=MemoryCommandParseStatus.RECOGNIZED,
            command=command,
        )

    @staticmethod
    def _invalid(reason: MemoryCommandParseReason) -> MemoryCommandParseResult:
        return MemoryCommandParseResult(
            status=MemoryCommandParseStatus.INVALID,
            reason=reason,
        )

    @staticmethod
    def _not_command() -> MemoryCommandParseResult:
        return MemoryCommandParseResult(status=MemoryCommandParseStatus.NOT_COMMAND)


def parse_memory_command(text: str) -> MemoryCommandParseResult:
    """Parse text with the stateless default command parser."""
    return MemoryCommandParser().parse(text)
