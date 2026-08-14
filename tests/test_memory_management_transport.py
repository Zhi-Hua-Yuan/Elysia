import json
from datetime import datetime, timezone
from uuid import UUID

import pytest

from open_llm_vtuber.memory import (
    MEMORY_MANAGEMENT_REQUEST_TYPES,
    MemoryListItemResponse,
    MemoryListResponse,
    MemoryManagementOperation,
    MemoryManagementReasonCode,
    MemoryManagementStatus,
    build_memory_management_error_response,
    is_loopback_address,
    is_memory_management_request_type,
    parse_memory_management_payload,
    serialize_memory_management_response,
)


REQUEST_ID = "8ec55466-eec1-4bef-8133-cc883d03965a"
MEMORY_ID = "9b88f7a60a3c49c0a4fe87a044264d22"


def _base_payload(message_type: str) -> dict:
    return {
        "type": message_type,
        "protocol_version": 1,
        "request_id": REQUEST_ID,
    }


@pytest.mark.parametrize(
    "payload",
    [
        _base_payload("memory-state-request"),
        _base_payload("memory-list-request"),
        {
            **_base_payload("memory-upsert-request"),
            "category": "preference",
            "value": "饮料不要太甜",
            "expected_revision": 0,
        },
        {
            **_base_payload("memory-update-request"),
            "memory_id": MEMORY_ID,
            "category": "preference",
            "value": "饮料只要微甜",
            "expected_revision": 1,
        },
        {
            **_base_payload("memory-delete-request"),
            "memory_id": MEMORY_ID,
            "expected_revision": 1,
        },
        {
            **_base_payload("memory-clear-request"),
            "expected_revision": 1,
            "confirm": True,
        },
        {
            **_base_payload("memory-setting-update"),
            "enabled": False,
            "expected_enabled": True,
        },
    ],
)
def test_transport_parses_all_frozen_request_types(payload: dict) -> None:
    outcome = parse_memory_management_payload(payload)

    assert outcome.request is not None
    assert outcome.error_response is None
    assert outcome.request.request_id == UUID(REQUEST_ID)


@pytest.mark.parametrize(
    ("version", "reason"),
    [
        (2, MemoryManagementReasonCode.UNSUPPORTED_PROTOCOL_VERSION),
        (0, MemoryManagementReasonCode.UNSUPPORTED_PROTOCOL_VERSION),
        ("1", MemoryManagementReasonCode.INVALID_REQUEST),
        (True, MemoryManagementReasonCode.INVALID_REQUEST),
        (None, MemoryManagementReasonCode.INVALID_REQUEST),
    ],
)
def test_transport_maps_version_failures_without_validation_details(
    version: object,
    reason: MemoryManagementReasonCode,
) -> None:
    payload = _base_payload("memory-state-request")
    payload["protocol_version"] = version

    outcome = parse_memory_management_payload(payload)

    assert outcome.request is None
    assert outcome.error_response.reason_code == reason
    assert outcome.error_response.request_id == UUID(REQUEST_ID)


@pytest.mark.parametrize(
    "request_id",
    [None, "not-a-uuid", "00000000-0000-0000-0000-000000000000"],
)
def test_invalid_request_id_is_not_echoed(request_id: object) -> None:
    payload = _base_payload("memory-list-request")
    payload["request_id"] = request_id

    outcome = parse_memory_management_payload(payload)

    assert outcome.error_response.request_id is None
    assert (
        outcome.error_response.reason_code == MemoryManagementReasonCode.INVALID_REQUEST
    )


def test_invalid_payload_response_does_not_contain_memory_content() -> None:
    secret_value = "绝不能出现在错误响应中的私人偏好"
    payload = {
        **_base_payload("memory-upsert-request"),
        "category": "preference",
        "value": secret_value,
        "expected_revision": 0,
        "unexpected": "field",
    }

    outcome = parse_memory_management_payload(payload)
    serialized = serialize_memory_management_response(outcome.error_response)

    assert secret_value not in serialized
    assert "unexpected" not in serialized
    assert json.loads(serialized)["reason_code"] == "invalid_request"


def test_list_response_serializes_uuid_enum_and_utc_timestamp() -> None:
    now = datetime(2026, 8, 14, 8, 0, tzinfo=timezone.utc)
    response = MemoryListResponse(
        protocol_version=1,
        request_id=REQUEST_ID,
        revision=1,
        item_count=1,
        max_items=24,
        items=[
            MemoryListItemResponse(
                id=MEMORY_ID,
                category="preference",
                value="喜欢花",
                source="manual_ui",
                created_at=now,
                updated_at=now,
            )
        ],
    )

    payload = json.loads(serialize_memory_management_response(response))

    assert payload["request_id"] == REQUEST_ID
    assert payload["items"][0]["category"] == "preference"
    assert payload["items"][0]["created_at"].endswith("Z")
    assert "key" not in payload["items"][0]


def test_error_builder_uses_frozen_status_class() -> None:
    rejected = build_memory_management_error_response(
        message_type="memory-delete-request",
        request_id=UUID(REQUEST_ID),
        reason_code=MemoryManagementReasonCode.INVALID_REQUEST,
    )
    failed = build_memory_management_error_response(
        message_type="memory-delete-request",
        request_id=UUID(REQUEST_ID),
        reason_code=MemoryManagementReasonCode.INTERNAL_ERROR,
    )

    assert rejected.operation == MemoryManagementOperation.DELETE
    assert rejected.status == MemoryManagementStatus.REJECTED
    assert failed.status == MemoryManagementStatus.FAILED


@pytest.mark.parametrize(
    "host",
    ["127.0.0.1", "127.23.45.67", "::1", "::1%loopback"],
)
def test_loopback_address_accepts_numeric_loopback_only(host: str) -> None:
    assert is_loopback_address(host) is True


@pytest.mark.parametrize(
    "host",
    [None, "", "localhost", "192.168.1.20", "8.8.8.8", "not-an-ip"],
)
def test_loopback_address_rejects_untrusted_or_non_numeric_hosts(
    host: object,
) -> None:
    assert is_loopback_address(host) is False


def test_request_type_detection_is_exact() -> None:
    assert set(MEMORY_MANAGEMENT_REQUEST_TYPES) == {
        "memory-state-request",
        "memory-list-request",
        "memory-upsert-request",
        "memory-update-request",
        "memory-delete-request",
        "memory-clear-request",
        "memory-setting-update",
    }
    assert is_memory_management_request_type("memory-list-request") is True
    assert is_memory_management_request_type("memory-list") is False
    assert is_memory_management_request_type("memory-unknown-request") is False


def test_transport_rejects_unknown_type_before_domain_dispatch() -> None:
    with pytest.raises(ValueError):
        parse_memory_management_payload(
            {
                **_base_payload("memory-unknown-request"),
            }
        )
