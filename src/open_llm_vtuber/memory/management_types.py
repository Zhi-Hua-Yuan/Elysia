"""Frozen v1 WebSocket contracts for local persistent-memory management.

This module deliberately contains no persistence, WebSocket routing, configuration
mutation, or conversation integration.
"""

from __future__ import annotations

import unicodedata
from datetime import datetime, timedelta
from enum import Enum
from typing import Annotated, Literal, Union

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    TypeAdapter,
    UUID4,
    field_serializer,
    field_validator,
    model_validator,
)

from .types import MEMORY_ID_PATTERN, MemoryCategory, MemorySource


MEMORY_MANAGEMENT_PROTOCOL_VERSION = 1
MAX_MANAGEMENT_VALUE_CHARS = 500


class MemoryManagementOperation(str, Enum):
    """Stable operation names used by management results."""

    STATE = "state"
    LIST = "list"
    CREATE = "create"
    UPDATE = "update"
    DELETE = "delete"
    CLEAR = "clear"
    SET_ENABLED = "set_enabled"


class MemoryManagementStatus(str, Enum):
    """Frozen v1 outcome values for management operations."""

    SUCCESS = "success"
    REJECTED = "rejected"
    FAILED = "failed"


class MemoryManagementReasonCode(str, Enum):
    """Stable, content-free reason codes exposed by the v1 protocol."""

    INVALID_REQUEST = "invalid_request"
    UNSUPPORTED_PROTOCOL_VERSION = "unsupported_protocol_version"
    LOCAL_ACCESS_REQUIRED = "local_access_required"
    PROXY_NOT_SUPPORTED = "proxy_not_supported"
    GROUP_NOT_SUPPORTED = "group_not_supported"
    UNSUPPORTED_AGENT = "unsupported_agent"
    INVALID_SCOPE = "invalid_scope"
    MEMORY_DISABLED = "memory_disabled"
    STORAGE_FAILURE = "storage_failure"
    INVALID_VALUE = "invalid_value"
    SENSITIVE_CONTENT = "sensitive_content"
    CAPACITY_REACHED = "capacity_reached"
    NOT_FOUND = "not_found"
    DUPLICATE_ITEM = "duplicate_item"
    REVISION_CONFLICT = "revision_conflict"
    SETTING_CONFLICT = "setting_conflict"
    CONFIG_PERSIST_FAILURE = "config_persist_failure"
    INTERNAL_ERROR = "internal_error"


def _validate_protocol_version(value: object) -> object:
    if type(value) is not int:
        raise ValueError("protocol_version must be an integer")
    return value


def _validate_management_value(value: str) -> str:
    if not value.strip():
        raise ValueError("memory value must contain non-whitespace characters")
    if any(unicodedata.category(char).startswith("C") for char in value):
        raise ValueError("memory value must not contain control characters")
    return value


def _validate_utc_datetime(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp must include timezone information")
    if value.utcoffset() != timedelta(0):
        raise ValueError("timestamp must use UTC")
    return value


class _FrozenProtocolModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class _MemoryManagementRequestBase(_FrozenProtocolModel):
    protocol_version: Literal[MEMORY_MANAGEMENT_PROTOCOL_VERSION]
    request_id: UUID4

    @field_validator("protocol_version", mode="before")
    @classmethod
    def validate_version_type(cls, value: object) -> object:
        return _validate_protocol_version(value)


class MemoryStateRequest(_MemoryManagementRequestBase):
    """Request management eligibility, capabilities, and document metadata."""

    type: Literal["memory-state-request"] = "memory-state-request"


class MemoryListRequest(_MemoryManagementRequestBase):
    """Request the complete bounded list for the current derived scope."""

    type: Literal["memory-list-request"] = "memory-list-request"


class MemoryUpsertRequest(_MemoryManagementRequestBase):
    """Create a manually authorized item or return an idempotent match."""

    type: Literal["memory-upsert-request"] = "memory-upsert-request"
    category: MemoryCategory
    value: str = Field(min_length=1, max_length=MAX_MANAGEMENT_VALUE_CHARS)
    expected_revision: int = Field(ge=0, strict=True)

    @field_validator("value")
    @classmethod
    def validate_value(cls, value: str) -> str:
        return _validate_management_value(value)


class MemoryUpdateRequest(_MemoryManagementRequestBase):
    """Update one item by stable ID under strict revision control."""

    type: Literal["memory-update-request"] = "memory-update-request"
    memory_id: str = Field(pattern=MEMORY_ID_PATTERN)
    category: MemoryCategory
    value: str = Field(min_length=1, max_length=MAX_MANAGEMENT_VALUE_CHARS)
    expected_revision: int = Field(ge=0, strict=True)

    @field_validator("value")
    @classmethod
    def validate_value(cls, value: str) -> str:
        return _validate_management_value(value)


class MemoryDeleteRequest(_MemoryManagementRequestBase):
    """Delete one item by stable ID under strict revision control."""

    type: Literal["memory-delete-request"] = "memory-delete-request"
    memory_id: str = Field(pattern=MEMORY_ID_PATTERN)
    expected_revision: int = Field(ge=0, strict=True)


class MemoryClearRequest(_MemoryManagementRequestBase):
    """Clear the exact document revision after explicit UI confirmation."""

    type: Literal["memory-clear-request"] = "memory-clear-request"
    expected_revision: int = Field(ge=0, strict=True)
    confirm: Literal[True]

    @field_validator("confirm", mode="before")
    @classmethod
    def validate_confirmation_type(cls, value: object) -> object:
        if value is not True:
            raise ValueError("confirm must be the boolean true")
        return value


class MemorySettingUpdateRequest(_MemoryManagementRequestBase):
    """Persist a total-switch change against the state shown to the client."""

    type: Literal["memory-setting-update"] = "memory-setting-update"
    enabled: bool = Field(strict=True)
    expected_enabled: bool = Field(strict=True)


MemoryManagementRequest = Annotated[
    Union[
        MemoryStateRequest,
        MemoryListRequest,
        MemoryUpsertRequest,
        MemoryUpdateRequest,
        MemoryDeleteRequest,
        MemoryClearRequest,
        MemorySettingUpdateRequest,
    ],
    Field(discriminator="type"),
]
MEMORY_MANAGEMENT_REQUEST_ADAPTER = TypeAdapter(MemoryManagementRequest)


class MemoryManagementCapabilities(_FrozenProtocolModel):
    """Explicit UI capabilities for one authorized local context."""

    list: bool = Field(strict=True)
    create: bool = Field(strict=True)
    update: bool = Field(strict=True)
    delete: bool = Field(strict=True)
    clear: bool = Field(strict=True)
    set_enabled: bool = Field(strict=True)


class _MemoryManagementResponseBase(_FrozenProtocolModel):
    protocol_version: Literal[MEMORY_MANAGEMENT_PROTOCOL_VERSION]
    request_id: UUID4

    @field_validator("protocol_version", mode="before")
    @classmethod
    def validate_version_type(cls, value: object) -> object:
        return _validate_protocol_version(value)


class MemoryStateResponse(_MemoryManagementResponseBase):
    """Successful state response for an authorized local management client."""

    type: Literal["memory-state"] = "memory-state"
    supported: Literal[True] = True
    enabled: bool = Field(strict=True)
    available: bool = Field(strict=True)
    item_count: int | None = Field(None, ge=0, strict=True)
    max_items: int = Field(ge=1, le=100, strict=True)
    revision: int | None = Field(None, ge=0, strict=True)
    plaintext_storage: Literal[True] = True
    capabilities: MemoryManagementCapabilities
    reason_code: MemoryManagementReasonCode | None = None

    @field_validator("supported", "plaintext_storage", mode="before")
    @classmethod
    def validate_fixed_true_flags(cls, value: object) -> object:
        if value is not True:
            raise ValueError("fixed protocol flags must be the boolean true")
        return value

    @model_validator(mode="after")
    def validate_state_consistency(self) -> "MemoryStateResponse":
        if self.available:
            if self.item_count is None or self.revision is None:
                raise ValueError("available state must include item_count and revision")
            if self.item_count > self.max_items:
                raise ValueError("item_count must not exceed max_items")
            if self.reason_code is not None:
                raise ValueError("available state must not include a reason code")
            expected = MemoryManagementCapabilities(
                list=True,
                create=self.enabled,
                update=self.enabled,
                delete=True,
                clear=True,
                set_enabled=True,
            )
        else:
            if self.item_count is not None or self.revision is not None:
                raise ValueError("unavailable state must hide item_count and revision")
            if self.reason_code != MemoryManagementReasonCode.STORAGE_FAILURE:
                raise ValueError("unavailable state requires storage_failure")
            expected = MemoryManagementCapabilities(
                list=False,
                create=False,
                update=False,
                delete=False,
                clear=False,
                set_enabled=True,
            )
        if self.capabilities != expected:
            raise ValueError("capabilities do not match the frozen state matrix")
        return self


class MemoryListItemResponse(_FrozenProtocolModel):
    """Public list projection that intentionally omits keys, scope, and paths."""

    id: str = Field(pattern=MEMORY_ID_PATTERN)
    category: MemoryCategory
    value: str = Field(min_length=1, max_length=MAX_MANAGEMENT_VALUE_CHARS)
    source: MemorySource
    created_at: datetime
    updated_at: datetime

    @field_validator("value")
    @classmethod
    def validate_value(cls, value: str) -> str:
        return _validate_management_value(value)

    @field_validator("created_at", "updated_at")
    @classmethod
    def validate_timestamp(cls, value: datetime) -> datetime:
        return _validate_utc_datetime(value)

    @model_validator(mode="after")
    def validate_timestamp_order(self) -> "MemoryListItemResponse":
        if self.updated_at < self.created_at:
            raise ValueError("updated_at must not be earlier than created_at")
        return self

    @field_serializer("created_at", "updated_at")
    def serialize_timestamp(self, value: datetime) -> str:
        return value.isoformat().replace("+00:00", "Z")


_CATEGORY_PRIORITY = {
    MemoryCategory.PREFERRED_ADDRESS: 0,
    MemoryCategory.PREFERENCE: 1,
    MemoryCategory.IMPORTANT_FACT: 2,
}


class MemoryListResponse(_MemoryManagementResponseBase):
    """Successful, complete, deterministically ordered v1 list response."""

    type: Literal["memory-list"] = "memory-list"
    revision: int = Field(ge=0, strict=True)
    item_count: int = Field(ge=0, strict=True)
    max_items: int = Field(ge=1, le=100, strict=True)
    items: list[MemoryListItemResponse] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def validate_list_consistency(self) -> "MemoryListResponse":
        if self.item_count != len(self.items):
            raise ValueError("item_count must equal the number of items")
        if self.item_count > self.max_items:
            raise ValueError("item_count must not exceed max_items")
        item_ids = [item.id for item in self.items]
        if len(item_ids) != len(set(item_ids)):
            raise ValueError("list item ids must be unique")
        expected = sorted(
            self.items,
            key=lambda item: (
                _CATEGORY_PRIORITY[item.category],
                -item.updated_at.timestamp(),
                item.id,
            ),
        )
        if self.items != expected:
            raise ValueError("items must use the frozen deterministic ordering")
        return self


class MemoryManagementResultResponse(_FrozenProtocolModel):
    """Content-free mutation result and state/list failure envelope."""

    type: Literal["memory-management-result"] = "memory-management-result"
    protocol_version: Literal[MEMORY_MANAGEMENT_PROTOCOL_VERSION]
    request_id: UUID4 | None
    operation: MemoryManagementOperation
    status: MemoryManagementStatus
    reason_code: MemoryManagementReasonCode | None = None
    changed: bool = Field(strict=True)
    memory_id: str | None = Field(None, pattern=MEMORY_ID_PATTERN)
    revision: int | None = Field(None, ge=0, strict=True)
    item_count: int | None = Field(None, ge=0, le=100, strict=True)
    enabled: bool | None = Field(None, strict=True)

    @field_validator("protocol_version", mode="before")
    @classmethod
    def validate_version_type(cls, value: object) -> object:
        return _validate_protocol_version(value)

    @model_validator(mode="after")
    def validate_result_consistency(self) -> "MemoryManagementResultResponse":
        if self.status == MemoryManagementStatus.SUCCESS:
            if self.reason_code is not None:
                raise ValueError("successful results must not include a reason code")
            if self.operation in {
                MemoryManagementOperation.STATE,
                MemoryManagementOperation.LIST,
            }:
                raise ValueError("successful state and list use dedicated responses")
        else:
            if self.reason_code is None:
                raise ValueError("unsuccessful results require a reason code")
            if self.changed:
                raise ValueError("unsuccessful results cannot report a change")
            failed_reasons = {
                MemoryManagementReasonCode.STORAGE_FAILURE,
                MemoryManagementReasonCode.CONFIG_PERSIST_FAILURE,
                MemoryManagementReasonCode.INTERNAL_ERROR,
            }
            if (
                self.status == MemoryManagementStatus.FAILED
                and self.reason_code not in failed_reasons
            ):
                raise ValueError("failed status requires an operational failure reason")
            if (
                self.status == MemoryManagementStatus.REJECTED
                and self.reason_code in failed_reasons
            ):
                raise ValueError("operational failure reasons require failed status")

        item_operations = {
            MemoryManagementOperation.CREATE,
            MemoryManagementOperation.UPDATE,
            MemoryManagementOperation.DELETE,
        }
        if self.status == MemoryManagementStatus.SUCCESS:
            if self.operation in item_operations and self.memory_id is None:
                raise ValueError("successful item operations require memory_id")
            if (
                self.operation == MemoryManagementOperation.SET_ENABLED
                and self.enabled is None
            ):
                raise ValueError("successful setting changes require enabled")
        if self.operation not in item_operations and self.memory_id is not None:
            raise ValueError("non-item operations must not include memory_id")
        return self


MemoryManagementResponse = Annotated[
    Union[
        MemoryStateResponse,
        MemoryListResponse,
        MemoryManagementResultResponse,
    ],
    Field(discriminator="type"),
]
MEMORY_MANAGEMENT_RESPONSE_ADAPTER = TypeAdapter(MemoryManagementResponse)
