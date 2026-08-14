"""WebSocket transport helpers for the frozen memory-management protocol.

This module converts untrusted JSON dictionaries to frozen protocol models and
serializes typed responses.  It deliberately contains no storage, scope, Agent,
conversation, or configuration-persistence behavior.
"""

from __future__ import annotations

from dataclasses import dataclass
from ipaddress import ip_address
from typing import Any
from uuid import UUID

from pydantic import ValidationError

from .management_types import (
    MEMORY_MANAGEMENT_PROTOCOL_VERSION,
    MEMORY_MANAGEMENT_REQUEST_ADAPTER,
    MEMORY_MANAGEMENT_RESPONSE_ADAPTER,
    MemoryManagementOperation,
    MemoryManagementReasonCode,
    MemoryManagementRequest,
    MemoryManagementResponse,
    MemoryManagementResultResponse,
    MemoryManagementStatus,
)


MEMORY_MANAGEMENT_REQUEST_TYPES = frozenset(
    {
        "memory-state-request",
        "memory-list-request",
        "memory-upsert-request",
        "memory-update-request",
        "memory-delete-request",
        "memory-clear-request",
        "memory-setting-update",
    }
)

_OPERATION_BY_MESSAGE_TYPE = {
    "memory-state-request": MemoryManagementOperation.STATE,
    "memory-list-request": MemoryManagementOperation.LIST,
    "memory-upsert-request": MemoryManagementOperation.CREATE,
    "memory-update-request": MemoryManagementOperation.UPDATE,
    "memory-delete-request": MemoryManagementOperation.DELETE,
    "memory-clear-request": MemoryManagementOperation.CLEAR,
    "memory-setting-update": MemoryManagementOperation.SET_ENABLED,
}


@dataclass(frozen=True)
class MemoryManagementParseOutcome:
    """Exactly one of ``request`` and ``error_response`` is populated."""

    request: MemoryManagementRequest | None = None
    error_response: MemoryManagementResultResponse | None = None

    def __post_init__(self) -> None:
        if (self.request is None) == (self.error_response is None):
            raise ValueError(
                "parse outcome requires exactly one request or error response"
            )


def is_memory_management_request_type(message_type: object) -> bool:
    """Return whether a raw message type is one of the seven frozen requests."""

    return (
        isinstance(message_type, str)
        and message_type in MEMORY_MANAGEMENT_REQUEST_TYPES
    )


def parse_memory_management_payload(
    payload: object,
) -> MemoryManagementParseOutcome:
    """Strictly parse one known management payload without leaking its content."""

    if not isinstance(payload, dict):
        raise TypeError("memory management payload must be a dictionary")

    message_type = payload.get("type")
    if not is_memory_management_request_type(message_type):
        raise ValueError("unknown memory management request type")

    try:
        request = MEMORY_MANAGEMENT_REQUEST_ADAPTER.validate_python(payload)
    except ValidationError:
        reason = _validation_reason(payload.get("protocol_version"))
        return MemoryManagementParseOutcome(
            error_response=build_memory_management_error_response(
                message_type=message_type,
                request_id=_extract_uuid4(payload.get("request_id")),
                reason_code=reason,
            )
        )

    return MemoryManagementParseOutcome(request=request)


def build_memory_management_error_response(
    *,
    message_type: str,
    reason_code: MemoryManagementReasonCode,
    request_id: UUID | None = None,
) -> MemoryManagementResultResponse:
    """Build a content-free terminal response for transport-level failures."""

    try:
        operation = _OPERATION_BY_MESSAGE_TYPE[message_type]
    except KeyError as exc:
        raise ValueError("unknown memory management request type") from exc

    failed_reasons = {
        MemoryManagementReasonCode.STORAGE_FAILURE,
        MemoryManagementReasonCode.CONFIG_PERSIST_FAILURE,
        MemoryManagementReasonCode.INTERNAL_ERROR,
    }
    return MemoryManagementResultResponse(
        protocol_version=MEMORY_MANAGEMENT_PROTOCOL_VERSION,
        request_id=request_id,
        operation=operation,
        status=(
            MemoryManagementStatus.FAILED
            if reason_code in failed_reasons
            else MemoryManagementStatus.REJECTED
        ),
        reason_code=reason_code,
        changed=False,
    )


def serialize_memory_management_response(
    response: MemoryManagementResponse,
) -> str:
    """Validate and serialize one frozen response as UTF-8 JSON text."""

    validated = MEMORY_MANAGEMENT_RESPONSE_ADAPTER.validate_python(response)
    return validated.model_dump_json()


def is_loopback_address(host: object) -> bool:
    """Accept only numeric IPv4/IPv6 loopback addresses from the ASGI peer."""

    if not isinstance(host, str) or not host:
        return False
    candidate = host.split("%", 1)[0]
    try:
        return ip_address(candidate).is_loopback
    except ValueError:
        return False


def _validation_reason(protocol_version: Any) -> MemoryManagementReasonCode:
    if (
        type(protocol_version) is int
        and protocol_version != MEMORY_MANAGEMENT_PROTOCOL_VERSION
    ):
        return MemoryManagementReasonCode.UNSUPPORTED_PROTOCOL_VERSION
    return MemoryManagementReasonCode.INVALID_REQUEST


def _extract_uuid4(value: object) -> UUID | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = UUID(value)
    except (ValueError, AttributeError):
        return None
    if parsed.version != 4:
        return None
    return parsed
