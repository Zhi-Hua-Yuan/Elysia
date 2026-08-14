from datetime import datetime, timezone

from open_llm_vtuber.memory import (
    MemoryAction,
    MemoryCategory,
    MemoryCommandParseReason,
    MemoryItem,
    MemoryReasonCode,
    MemorySource,
    feedback_for_list,
    feedback_for_parse_failure,
    feedback_for_reason,
    feedback_for_success,
)


def make_item(item_id: str, category: MemoryCategory, value: str) -> MemoryItem:
    now = datetime(2026, 8, 14, tzinfo=timezone.utc)
    return MemoryItem(
        id=f"{int(item_id.removeprefix('item')):032x}",
        category=category,
        key=f"key-{item_id}",
        value=value,
        source=MemorySource.EXPLICIT_USER,
        created_at=now,
        updated_at=now,
    )


def test_success_feedback_distinguishes_committed_and_idempotent_results() -> None:
    assert (
        feedback_for_success(action=MemoryAction.CREATE, changed=True)
        == "好的，我记住了。"
    )
    assert (
        feedback_for_success(action=MemoryAction.UPDATE, changed=False)
        == "这条我已经记住了。"
    )
    assert "称呼" in feedback_for_success(
        action=MemoryAction.UPDATE,
        changed=True,
        category=MemoryCategory.PREFERRED_ADDRESS,
    )


def test_failure_feedback_never_claims_the_operation_succeeded() -> None:
    for reason in MemoryReasonCode:
        text = feedback_for_reason(reason)
        assert text
        assert "我记住了" not in text
        assert "已经记住" not in text

    for reason in MemoryCommandParseReason:
        text = feedback_for_parse_failure(reason)
        assert text
        assert "我记住了" not in text
        assert "已经记住" not in text


def test_list_feedback_is_order_preserving_bounded_and_limited_to_five() -> None:
    items = [
        make_item(
            f"item{i}",
            MemoryCategory.PREFERENCE,
            f"偏好{i}",
        )
        for i in range(7)
    ]

    text = feedback_for_list(items, max_items=5, max_chars=80)

    assert "偏好0" in text
    assert "偏好4" in text
    assert "偏好5" not in text
    assert len(text) <= 80


def test_list_feedback_handles_empty_memory() -> None:
    assert "还没有" in feedback_for_list([])
    assert len(feedback_for_list([], max_chars=4)) <= 4
