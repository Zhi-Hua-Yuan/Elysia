"""Typed contracts for deterministic explicit memory commands."""

from __future__ import annotations

from datetime import datetime, timedelta
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .types import MemoryAction, MemoryCategory, MemoryReasonCode, MemoryScope


class MemoryCommandKind(str, Enum):
    """Commands recognized before any persistence operation is attempted."""

    SAVE = "save"
    LIST = "list"
    DELETE = "delete"
    CLEAR = "clear"
    CONFIRM_CLEAR = "confirm_clear"
    CANCEL_CLEAR = "cancel_clear"


class MemoryCommandParseStatus(str, Enum):
    """Outcome of deterministic command parsing."""

    NOT_COMMAND = "not_command"
    RECOGNIZED = "recognized"
    INVALID = "invalid"


class MemoryCommandParseReason(str, Enum):
    """Internal parse failures that never include user-provided content."""

    EMPTY_VALUE = "empty_value"
    INVALID_TARGET = "invalid_target"
    NEGATED_COMMAND = "negated_command"
    INPUT_TOO_LONG = "input_too_long"
    UNSUPPORTED_FORM = "unsupported_form"


class MemoryCommand(BaseModel):
    """A parsed command that contains only the fields required by its kind."""

    model_config = ConfigDict(frozen=True)

    kind: MemoryCommandKind
    category: MemoryCategory | None = None
    value: str | None = None
    target: str | None = None

    @model_validator(mode="after")
    def validate_command_shape(self) -> "MemoryCommand":
        if self.kind == MemoryCommandKind.SAVE:
            if self.category is None or not self.value or self.target is not None:
                raise ValueError("save commands require category and value only")
        elif self.kind == MemoryCommandKind.DELETE:
            if not self.target or self.category is not None or self.value is not None:
                raise ValueError("delete commands require target only")
        elif any(
            field is not None for field in (self.category, self.value, self.target)
        ):
            raise ValueError("control commands must not include payload fields")
        return self


class MemoryCommandParseResult(BaseModel):
    """Validated envelope distinguishing conversation text from memory commands."""

    model_config = ConfigDict(frozen=True)

    status: MemoryCommandParseStatus
    command: MemoryCommand | None = None
    reason: MemoryCommandParseReason | None = None

    @model_validator(mode="after")
    def validate_result_shape(self) -> "MemoryCommandParseResult":
        if self.status == MemoryCommandParseStatus.RECOGNIZED:
            if self.command is None or self.reason is not None:
                raise ValueError("recognized parse results require a command only")
        elif self.status == MemoryCommandParseStatus.INVALID:
            if self.command is not None or self.reason is None:
                raise ValueError("invalid parse results require a reason only")
        elif self.command is not None or self.reason is not None:
            raise ValueError("non-command parse results must not include details")
        return self


class MemoryPolicyDecision(BaseModel):
    """Safe output of local memory-content validation."""

    model_config = ConfigDict(frozen=True)

    allowed: bool
    normalized_value: str | None = None
    reason_code: MemoryReasonCode | None = None

    @model_validator(mode="after")
    def validate_decision_shape(self) -> "MemoryPolicyDecision":
        if self.allowed:
            if not self.normalized_value or self.reason_code is not None:
                raise ValueError("allowed decisions require normalized text only")
        elif self.normalized_value is not None or self.reason_code is None:
            raise ValueError("rejected decisions require a reason only")
        return self


class PendingMemoryConfirmation(BaseModel):
    """Connection-local confirmation state without memory content."""

    model_config = ConfigDict(frozen=True)

    scope: MemoryScope
    action: MemoryAction = MemoryAction.CLEAR
    expected_revision: int = Field(ge=0)
    expires_at: datetime

    @field_validator("expires_at")
    @classmethod
    def validate_expiry_is_utc(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("confirmation expiry must include timezone information")
        if value.utcoffset() != timedelta(0):
            raise ValueError("confirmation expiry must use UTC")
        return value

    @model_validator(mode="after")
    def validate_clear_action(self) -> "PendingMemoryConfirmation":
        if self.action != MemoryAction.CLEAR:
            raise ValueError("only clear operations can require confirmation")
        return self
