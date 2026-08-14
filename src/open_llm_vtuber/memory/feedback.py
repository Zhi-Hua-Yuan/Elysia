"""Deterministic, local feedback for explicit persistent-memory commands."""

from __future__ import annotations

from collections.abc import Sequence

from .command_types import MemoryCommandParseReason
from .types import MemoryAction, MemoryCategory, MemoryItem, MemoryReasonCode

DEFAULT_LIST_FEEDBACK_ITEMS = 5
DEFAULT_LIST_FEEDBACK_CHARS = 320

_PARSE_FEEDBACK = {
    MemoryCommandParseReason.EMPTY_VALUE: "请告诉我具体要记住什么。",
    MemoryCommandParseReason.INVALID_TARGET: "请说得更具体一些，告诉我要忘掉哪条记忆。",
    MemoryCommandParseReason.NEGATED_COMMAND: "好的，我不会保存这条内容。",
    MemoryCommandParseReason.INPUT_TOO_LONG: "这条记忆命令太长了，请缩短后再试。",
    MemoryCommandParseReason.UNSUPPORTED_FORM: "我没有执行这条记忆命令，请换一种更明确的说法。",
}

_REASON_FEEDBACK = {
    MemoryReasonCode.MEMORY_DISABLED: "持久记忆功能当前没有启用。",
    MemoryReasonCode.INVALID_SCOPE: "当前角色的记忆作用域无效，操作没有执行。",
    MemoryReasonCode.INVALID_VALUE: "这条内容不符合记忆规则，请换一种说法。",
    MemoryReasonCode.SENSITIVE_CONTENT: (
        "这类内容不适合保存，请不要保存密码或高风险隐私。"
    ),
    MemoryReasonCode.CAPACITY_REACHED: "记忆已经满了，请先删除一条。",
    MemoryReasonCode.NOT_FOUND: "我没有找到对应的记忆。",
    MemoryReasonCode.DUPLICATE_ITEM: "已经存在相同的记忆，请先查看已有内容。",
    MemoryReasonCode.AMBIGUOUS_MATCH: ("找到了多条可能的记忆，请说得更具体一些。"),
    MemoryReasonCode.REVISION_CONFLICT: ("记忆内容已经发生变化，请重新发起操作。"),
    MemoryReasonCode.STORAGE_FAILURE: "这次记忆操作没有成功。",
    MemoryReasonCode.CONFIRMATION_REQUIRED: (
        "清空会删除当前角色的全部持久记忆。请说“确认清空记忆”，或者说“取消清空”。"
    ),
    MemoryReasonCode.CONFIRMATION_EXPIRED: (
        "清空确认已经过期，请重新说“清空所有记忆”。"
    ),
    MemoryReasonCode.CONFIRMATION_NOT_PENDING: "当前没有等待确认的清空操作。",
}

_CATEGORY_LABELS = {
    MemoryCategory.PREFERRED_ADDRESS: "称呼",
    MemoryCategory.PREFERENCE: "偏好",
    MemoryCategory.IMPORTANT_FACT: "重要事项",
}


def feedback_for_parse_failure(reason: MemoryCommandParseReason) -> str:
    """Return a fixed response for a rejected explicit command form."""
    return _PARSE_FEEDBACK[reason]


def feedback_for_reason(reason: MemoryReasonCode) -> str:
    """Return a fixed response for a stable operation reason code."""
    return _REASON_FEEDBACK[reason]


def feedback_for_success(
    *,
    action: MemoryAction,
    changed: bool,
    category: MemoryCategory | None = None,
) -> str:
    """Return a fixed response derived only from the committed result."""
    if action == MemoryAction.CREATE:
        return "好的，我记住了。"
    if action == MemoryAction.UPDATE:
        if not changed:
            return "这条我已经记住了。"
        if category == MemoryCategory.PREFERRED_ADDRESS:
            return "好的，以后我会按这个称呼你。"
        return "好的，我已经更新了这条记忆。"
    if action == MemoryAction.DELETE:
        return "好的，我已经忘掉那条记忆了。"
    if action == MemoryAction.CLEAR:
        if changed:
            return "好的，我已经清空当前角色的持久记忆了。"
        return "当前角色没有需要清空的持久记忆。"
    raise ValueError(f"unsupported successful memory action: {action.value}")


def feedback_for_list(
    items: Sequence[MemoryItem],
    *,
    max_items: int = DEFAULT_LIST_FEEDBACK_ITEMS,
    max_chars: int = DEFAULT_LIST_FEEDBACK_CHARS,
) -> str:
    """Render a bounded spoken summary without exposing it to result events."""
    if max_items <= 0 or max_chars <= 0:
        raise ValueError("list feedback limits must be positive")
    if not items:
        return "我目前还没有保存关于你的持久记忆。"[:max_chars]

    prefix = "我记得："
    suffix = "。"
    if max_chars <= len(prefix) + len(suffix):
        return f"{prefix}{suffix}"[:max_chars]
    parts: list[str] = []
    for item in items[:max_items]:
        candidate = f"{_CATEGORY_LABELS[item.category]}：{item.value}"
        available = max_chars - len(prefix) - len(suffix) - len("；".join(parts))
        if parts:
            available -= 1
        if available <= 0:
            break
        if len(candidate) > available:
            if not parts:
                candidate = candidate[:available]
            else:
                break
        parts.append(candidate)

    if not parts:
        return "我保存了一些关于你的持久记忆，但这次无法完整朗读。"[:max_chars]
    return f"{prefix}{'；'.join(parts)}{suffix}"


def feedback_expression(*, success: bool, requires_confirmation: bool = False) -> str:
    """Return a model-independent expression key with safe runtime fallback."""
    if success:
        return "joy"
    if requires_confirmation:
        return "neutral"
    return "neutral"
