from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from open_llm_vtuber.memory import (
    MemoryAction,
    MemoryCategory,
    MemoryCommand,
    MemoryCommandKind,
    MemoryCommandParseReason,
    MemoryCommandParseResult,
    MemoryCommandParseStatus,
    MemoryPolicyDecision,
    MemoryReasonCode,
    MemoryScope,
    PendingMemoryConfirmation,
)


NOW = datetime(2026, 8, 12, 8, 0, tzinfo=timezone.utc)
SCOPE = MemoryScope(
    profile_id="local_default",
    character_conf_uid="elysia_mvp_001",
)


def test_save_command_requires_category_and_value_only() -> None:
    command = MemoryCommand(
        kind=MemoryCommandKind.SAVE,
        category=MemoryCategory.PREFERENCE,
        value="我不喜欢太甜的饮料",
    )

    assert command.target is None
    with pytest.raises(ValidationError):
        MemoryCommand(kind=MemoryCommandKind.SAVE)
    with pytest.raises(ValidationError):
        MemoryCommand(
            kind=MemoryCommandKind.SAVE,
            category=MemoryCategory.PREFERENCE,
            value="喜欢花",
            target="花",
        )


def test_delete_command_requires_target_only() -> None:
    command = MemoryCommand(kind=MemoryCommandKind.DELETE, target="太甜的饮料")

    assert command.category is None
    with pytest.raises(ValidationError):
        MemoryCommand(kind=MemoryCommandKind.DELETE)
    with pytest.raises(ValidationError):
        MemoryCommand(
            kind=MemoryCommandKind.DELETE,
            target="花",
            value="花",
        )


@pytest.mark.parametrize(
    "kind",
    [
        MemoryCommandKind.LIST,
        MemoryCommandKind.CLEAR,
        MemoryCommandKind.CONFIRM_CLEAR,
        MemoryCommandKind.CANCEL_CLEAR,
    ],
)
def test_control_commands_reject_payload_fields(kind: MemoryCommandKind) -> None:
    assert MemoryCommand(kind=kind).value is None
    with pytest.raises(ValidationError):
        MemoryCommand(kind=kind, value="unexpected")


def test_parse_result_shapes_are_mutually_exclusive() -> None:
    command = MemoryCommand(kind=MemoryCommandKind.LIST)
    recognized = MemoryCommandParseResult(
        status=MemoryCommandParseStatus.RECOGNIZED,
        command=command,
    )
    invalid = MemoryCommandParseResult(
        status=MemoryCommandParseStatus.INVALID,
        reason=MemoryCommandParseReason.EMPTY_VALUE,
    )
    ordinary = MemoryCommandParseResult(
        status=MemoryCommandParseStatus.NOT_COMMAND,
    )

    assert recognized.command == command
    assert invalid.reason == MemoryCommandParseReason.EMPTY_VALUE
    assert ordinary.model_dump() == {
        "status": MemoryCommandParseStatus.NOT_COMMAND,
        "command": None,
        "reason": None,
    }
    with pytest.raises(ValidationError):
        MemoryCommandParseResult(status=MemoryCommandParseStatus.RECOGNIZED)
    with pytest.raises(ValidationError):
        MemoryCommandParseResult(
            status=MemoryCommandParseStatus.NOT_COMMAND,
            reason=MemoryCommandParseReason.UNSUPPORTED_FORM,
        )


def test_command_contract_does_not_retain_raw_user_input() -> None:
    command = MemoryCommand(
        kind=MemoryCommandKind.SAVE,
        category=MemoryCategory.IMPORTANT_FACT,
        value="周末通常要加班",
    )

    assert "raw" not in command.model_dump()
    assert "input" not in command.model_dump()


def test_memory_policy_decision_requires_one_safe_outcome() -> None:
    allowed = MemoryPolicyDecision(allowed=True, normalized_value="喜欢花")
    rejected = MemoryPolicyDecision(
        allowed=False,
        reason_code=MemoryReasonCode.SENSITIVE_CONTENT,
    )

    assert allowed.reason_code is None
    assert rejected.normalized_value is None
    with pytest.raises(ValidationError):
        MemoryPolicyDecision(allowed=True)
    with pytest.raises(ValidationError):
        MemoryPolicyDecision(
            allowed=False,
            normalized_value="PRIVATE",
            reason_code=MemoryReasonCode.SENSITIVE_CONTENT,
        )


def test_pending_clear_confirmation_is_utc_and_content_free() -> None:
    pending = PendingMemoryConfirmation(
        scope=SCOPE,
        expected_revision=3,
        expires_at=NOW + timedelta(seconds=60),
    )

    assert pending.action == MemoryAction.CLEAR
    assert set(pending.model_dump()) == {
        "scope",
        "action",
        "expected_revision",
        "expires_at",
    }
    with pytest.raises(ValidationError):
        PendingMemoryConfirmation(
            scope=SCOPE,
            expected_revision=3,
            expires_at=NOW.replace(tzinfo=None),
        )
    with pytest.raises(ValidationError):
        PendingMemoryConfirmation(
            scope=SCOPE,
            action=MemoryAction.DELETE,
            expected_revision=3,
            expires_at=NOW,
        )


def test_confirmation_reason_codes_are_stable_values() -> None:
    assert MemoryReasonCode.CONFIRMATION_REQUIRED.value == "confirmation_required"
    assert MemoryReasonCode.CONFIRMATION_EXPIRED.value == "confirmation_expired"
    assert MemoryReasonCode.CONFIRMATION_NOT_PENDING.value == "confirmation_not_pending"
