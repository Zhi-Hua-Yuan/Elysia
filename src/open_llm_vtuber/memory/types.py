"""Stable data contracts for lightweight persistent memory.

This module deliberately contains no persistence or conversation integration.
"""

import re
import unicodedata
from datetime import datetime, timedelta
from enum import Enum
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


PROFILE_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
MEMORY_ID_PATTERN = r"^[0-9a-f]{32}$"
MEMORY_KEY_PATTERN = r"^[a-z][a-z0-9_.:-]{0,127}$"
WINDOWS_RESERVED_COMPONENTS = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{index}" for index in range(1, 10)),
    *(f"LPT{index}" for index in range(1, 10)),
}


def validate_profile_id(value: str) -> str:
    """Validate the stable local profile identifier."""
    if not PROFILE_ID_PATTERN.fullmatch(value):
        raise ValueError(
            "profile_id must contain 1-64 ASCII letters, digits, '_' or '-'"
        )
    return value


def _validate_character_conf_uid(value: str) -> str:
    """Validate a Unicode-safe identifier that can later become a filename."""
    if not value or len(value) > 255 or value != value.strip():
        raise ValueError("character_conf_uid must be 1-255 trimmed characters")
    if value in {".", ".."} or value.endswith((".", " ")):
        raise ValueError("character_conf_uid is not a safe path component")
    if any(char in '<>:"/\\|?*' for char in value):
        raise ValueError("character_conf_uid contains filesystem special characters")
    if any(unicodedata.category(char).startswith("C") for char in value):
        raise ValueError("character_conf_uid contains control characters")
    if value.split(".", 1)[0].upper() in WINDOWS_RESERVED_COMPONENTS:
        raise ValueError("character_conf_uid uses a reserved filename")
    return value


def _validate_utc_datetime(value: datetime) -> datetime:
    """Require explicit UTC timestamps in the persisted contract."""
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp must include timezone information")
    if value.utcoffset() != timedelta(0):
        raise ValueError("timestamp must use UTC")
    return value


class MemoryCategory(str, Enum):
    """Memory categories allowed by the M5 contract."""

    PREFERRED_ADDRESS = "preferred_address"
    PREFERENCE = "preference"
    IMPORTANT_FACT = "important_fact"


class MemorySource(str, Enum):
    """Sources that can explicitly authorize a memory write."""

    EXPLICIT_USER = "explicit_user"
    MANUAL_UI = "manual_ui"


class MemoryAction(str, Enum):
    """Stable operation names shared by later service and UI layers."""

    CREATE = "create"
    UPDATE = "update"
    DELETE = "delete"
    LIST = "list"
    CLEAR = "clear"


class MemoryOperationStatus(str, Enum):
    """High-level outcome of a memory operation."""

    SUCCESS = "success"
    REJECTED = "rejected"
    AMBIGUOUS = "ambiguous"
    FAILED = "failed"


class MemoryReasonCode(str, Enum):
    """Stable, non-sensitive reason codes for memory operation failures."""

    MEMORY_DISABLED = "memory_disabled"
    INVALID_SCOPE = "invalid_scope"
    INVALID_VALUE = "invalid_value"
    SENSITIVE_CONTENT = "sensitive_content"
    CAPACITY_REACHED = "capacity_reached"
    NOT_FOUND = "not_found"
    AMBIGUOUS_MATCH = "ambiguous_match"
    REVISION_CONFLICT = "revision_conflict"
    STORAGE_FAILURE = "storage_failure"


class MemoryScope(BaseModel):
    """Immutable isolation key for one local profile and one character."""

    model_config = ConfigDict(frozen=True)

    profile_id: str
    character_conf_uid: str

    @field_validator("profile_id")
    @classmethod
    def validate_scope_profile_id(cls, value: str) -> str:
        return validate_profile_id(value)

    @field_validator("character_conf_uid")
    @classmethod
    def validate_scope_character_uid(cls, value: str) -> str:
        return _validate_character_conf_uid(value)


class MemoryItem(BaseModel):
    """One explicitly authorized, structured user fact."""

    id: str = Field(pattern=MEMORY_ID_PATTERN)
    category: MemoryCategory
    key: str = Field(min_length=1, max_length=128, pattern=MEMORY_KEY_PATTERN)
    value: str = Field(min_length=1, max_length=500)
    source: MemorySource
    created_at: datetime
    updated_at: datetime

    @field_validator("value")
    @classmethod
    def validate_value(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("memory value must not be empty")
        if any(unicodedata.category(char).startswith("C") for char in normalized):
            raise ValueError("memory value must not contain control characters")
        return normalized

    @field_validator("created_at", "updated_at")
    @classmethod
    def validate_timestamps(cls, value: datetime) -> datetime:
        return _validate_utc_datetime(value)

    @model_validator(mode="after")
    def validate_item_consistency(self) -> "MemoryItem":
        if self.updated_at < self.created_at:
            raise ValueError("updated_at must not be earlier than created_at")
        if (
            self.category == MemoryCategory.PREFERRED_ADDRESS
            and self.key != MemoryCategory.PREFERRED_ADDRESS.value
        ):
            raise ValueError("preferred_address items must use the matching key")
        if (
            self.category != MemoryCategory.PREFERRED_ADDRESS
            and self.key == MemoryCategory.PREFERRED_ADDRESS.value
        ):
            raise ValueError("non-address items must not use the preferred_address key")
        return self


class MemoryDocument(BaseModel):
    """Versioned JSON document for one memory scope."""

    schema_version: Literal[1] = 1
    profile_id: str
    character_conf_uid: str
    revision: int = Field(0, ge=0)
    updated_at: datetime
    items: list[MemoryItem] = Field(default_factory=list, max_length=100)

    @field_validator("profile_id")
    @classmethod
    def validate_document_profile_id(cls, value: str) -> str:
        return validate_profile_id(value)

    @field_validator("character_conf_uid")
    @classmethod
    def validate_document_character_uid(cls, value: str) -> str:
        return _validate_character_conf_uid(value)

    @field_validator("updated_at")
    @classmethod
    def validate_document_timestamp(cls, value: datetime) -> datetime:
        return _validate_utc_datetime(value)

    @model_validator(mode="after")
    def validate_document_consistency(self) -> "MemoryDocument":
        item_ids = [item.id for item in self.items]
        if len(item_ids) != len(set(item_ids)):
            raise ValueError("memory item ids must be unique")

        address_count = sum(
            item.category == MemoryCategory.PREFERRED_ADDRESS for item in self.items
        )
        if address_count > 1:
            raise ValueError("a memory scope can contain only one preferred address")
        if any(item.updated_at > self.updated_at for item in self.items):
            raise ValueError("document updated_at must cover all item updates")
        return self


class MemoryOperationResult(BaseModel):
    """Non-sensitive result envelope for future memory operations."""

    status: MemoryOperationStatus
    action: MemoryAction
    reason_code: Optional[MemoryReasonCode] = None
    memory_id: Optional[str] = Field(None, pattern=MEMORY_ID_PATTERN)
    revision: Optional[int] = Field(None, ge=0)

    @model_validator(mode="after")
    def validate_result_consistency(self) -> "MemoryOperationResult":
        if self.status == MemoryOperationStatus.SUCCESS:
            if self.reason_code is not None:
                raise ValueError("successful operations must not include a reason code")
            if (
                self.action
                in {
                    MemoryAction.CREATE,
                    MemoryAction.UPDATE,
                    MemoryAction.DELETE,
                }
                and self.memory_id is None
            ):
                raise ValueError("successful item operations must include memory_id")
        elif self.reason_code is None:
            raise ValueError("unsuccessful operations must include a reason code")
        return self
