from datetime import datetime, timedelta, timezone
from uuid import UUID

import pytest
from pydantic import ValidationError

from open_llm_vtuber.memory import (
    MEMORY_MANAGEMENT_PROTOCOL_VERSION,
    MEMORY_MANAGEMENT_REQUEST_ADAPTER,
    MEMORY_MANAGEMENT_RESPONSE_ADAPTER,
    MemoryClearRequest,
    MemoryDeleteRequest,
    MemoryListItemResponse,
    MemoryListRequest,
    MemoryListResponse,
    MemoryManagementCapabilities,
    MemoryManagementOperation,
    MemoryManagementReasonCode,
    MemoryManagementResultResponse,
    MemoryManagementStatus,
    MemorySettingUpdateRequest,
    MemoryStateRequest,
    MemoryStateResponse,
    MemoryUpdateRequest,
    MemoryUpsertRequest,
)


REQUEST_ID = "8ec55466-eec1-4bef-8133-cc883d03965a"
MEMORY_ID = "9b88f7a60a3c49c0a4fe87a044264d22"
OTHER_MEMORY_ID = "0a88f7a60a3c49c0a4fe87a044264d23"
NOW = datetime(2026, 8, 14, 8, 0, tzinfo=timezone.utc)


def _base_payload(message_type: str) -> dict:
    return {
        "type": message_type,
        "protocol_version": MEMORY_MANAGEMENT_PROTOCOL_VERSION,
        "request_id": REQUEST_ID,
    }


def _enabled_capabilities() -> MemoryManagementCapabilities:
    return MemoryManagementCapabilities(
        list=True,
        create=True,
        update=True,
        delete=True,
        clear=True,
        set_enabled=True,
    )


def _disabled_capabilities() -> MemoryManagementCapabilities:
    return MemoryManagementCapabilities(
        list=True,
        create=False,
        update=False,
        delete=True,
        clear=True,
        set_enabled=True,
    )


def _unavailable_capabilities() -> MemoryManagementCapabilities:
    return MemoryManagementCapabilities(
        list=False,
        create=False,
        update=False,
        delete=False,
        clear=False,
        set_enabled=True,
    )


@pytest.mark.parametrize(
    ("payload", "expected_type"),
    [
        (_base_payload("memory-state-request"), MemoryStateRequest),
        (_base_payload("memory-list-request"), MemoryListRequest),
        (
            {
                **_base_payload("memory-upsert-request"),
                "category": "preference",
                "value": "饮料不要太甜",
                "expected_revision": 5,
            },
            MemoryUpsertRequest,
        ),
        (
            {
                **_base_payload("memory-update-request"),
                "memory_id": MEMORY_ID,
                "category": "preference",
                "value": "饮料只要微甜",
                "expected_revision": 5,
            },
            MemoryUpdateRequest,
        ),
        (
            {
                **_base_payload("memory-delete-request"),
                "memory_id": MEMORY_ID,
                "expected_revision": 5,
            },
            MemoryDeleteRequest,
        ),
        (
            {
                **_base_payload("memory-clear-request"),
                "expected_revision": 5,
                "confirm": True,
            },
            MemoryClearRequest,
        ),
        (
            {
                **_base_payload("memory-setting-update"),
                "enabled": False,
                "expected_enabled": True,
            },
            MemorySettingUpdateRequest,
        ),
    ],
)
def test_all_frozen_request_types_parse(payload: dict, expected_type: type) -> None:
    request = MEMORY_MANAGEMENT_REQUEST_ADAPTER.validate_python(payload)

    assert isinstance(request, expected_type)
    assert request.request_id == UUID(REQUEST_ID)
    assert request.request_id.version == 4


@pytest.mark.parametrize(
    "mutate",
    [
        lambda payload: payload.update({"unexpected": "field"}),
        lambda payload: payload.update({"protocol_version": 2}),
        lambda payload: payload.update({"protocol_version": True}),
        lambda payload: payload.update({"protocol_version": "1"}),
        lambda payload: payload.update({"request_id": "not-a-uuid"}),
        lambda payload: payload.update(
            {"request_id": "00000000-0000-0000-0000-000000000000"}
        ),
    ],
)
def test_request_envelope_rejects_invalid_contract_fields(mutate) -> None:
    payload = _base_payload("memory-state-request")
    mutate(payload)

    with pytest.raises(ValidationError):
        MEMORY_MANAGEMENT_REQUEST_ADAPTER.validate_python(payload)


@pytest.mark.parametrize("revision", [True, False, -1, "5", 1.0])
def test_expected_revision_is_a_strict_non_negative_integer(revision: object) -> None:
    payload = {
        **_base_payload("memory-delete-request"),
        "memory_id": MEMORY_ID,
        "expected_revision": revision,
    }

    with pytest.raises(ValidationError):
        MEMORY_MANAGEMENT_REQUEST_ADAPTER.validate_python(payload)


@pytest.mark.parametrize("confirmation", [False, "true", 1, None])
def test_clear_confirmation_only_accepts_boolean_true(confirmation: object) -> None:
    payload = {
        **_base_payload("memory-clear-request"),
        "expected_revision": 5,
        "confirm": confirmation,
    }

    with pytest.raises(ValidationError):
        MEMORY_MANAGEMENT_REQUEST_ADAPTER.validate_python(payload)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("enabled", "false"),
        ("enabled", 0),
        ("expected_enabled", "true"),
        ("expected_enabled", 1),
    ],
)
def test_setting_booleans_are_strict(field: str, value: object) -> None:
    payload = {
        **_base_payload("memory-setting-update"),
        "enabled": False,
        "expected_enabled": True,
    }
    payload[field] = value

    with pytest.raises(ValidationError):
        MEMORY_MANAGEMENT_REQUEST_ADAPTER.validate_python(payload)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("category", "unknown"),
        ("memory_id", MEMORY_ID.upper()),
        ("memory_id", "short"),
        ("value", "   "),
        ("value", "line\nbreak"),
        ("value", "x" * 501),
    ],
)
def test_update_rejects_invalid_item_fields(field: str, value: str) -> None:
    payload = {
        **_base_payload("memory-update-request"),
        "memory_id": MEMORY_ID,
        "category": "preference",
        "value": "饮料只要微甜",
        "expected_revision": 5,
    }
    payload[field] = value

    with pytest.raises(ValidationError):
        MEMORY_MANAGEMENT_REQUEST_ADAPTER.validate_python(payload)


def test_client_requests_cannot_select_scope_source_or_internal_key() -> None:
    payload = {
        **_base_payload("memory-upsert-request"),
        "category": "important_fact",
        "value": "周末通常要加班",
        "expected_revision": 2,
    }

    for forbidden_field in (
        "profile_id",
        "conf_uid",
        "character_conf_uid",
        "storage_dir",
        "source",
        "key",
        "path",
    ):
        with pytest.raises(ValidationError):
            MEMORY_MANAGEMENT_REQUEST_ADAPTER.validate_python(
                {**payload, forbidden_field: "forbidden"}
            )


def test_available_state_freezes_enabled_and_disabled_capabilities() -> None:
    enabled = MemoryStateResponse(
        protocol_version=1,
        request_id=REQUEST_ID,
        enabled=True,
        available=True,
        item_count=3,
        max_items=24,
        revision=5,
        capabilities=_enabled_capabilities(),
    )
    disabled = MemoryStateResponse(
        protocol_version=1,
        request_id=REQUEST_ID,
        enabled=False,
        available=True,
        item_count=3,
        max_items=24,
        revision=5,
        capabilities=_disabled_capabilities(),
    )

    assert enabled.capabilities.create is True
    assert disabled.capabilities.list is True
    assert disabled.capabilities.create is False
    assert disabled.capabilities.delete is True


def test_unavailable_state_hides_document_metadata() -> None:
    state = MemoryStateResponse(
        protocol_version=1,
        request_id=REQUEST_ID,
        enabled=True,
        available=False,
        max_items=24,
        capabilities=_unavailable_capabilities(),
        reason_code=MemoryManagementReasonCode.STORAGE_FAILURE,
    )

    assert state.item_count is None
    assert state.revision is None
    with pytest.raises(ValidationError):
        MemoryStateResponse(
            protocol_version=1,
            request_id=REQUEST_ID,
            enabled=True,
            available=False,
            item_count=3,
            max_items=24,
            revision=5,
            capabilities=_unavailable_capabilities(),
            reason_code=MemoryManagementReasonCode.STORAGE_FAILURE,
        )


def test_state_rejects_capabilities_outside_frozen_matrix() -> None:
    with pytest.raises(ValidationError):
        MemoryStateResponse(
            protocol_version=1,
            request_id=REQUEST_ID,
            enabled=False,
            available=True,
            item_count=1,
            max_items=24,
            revision=2,
            capabilities=_enabled_capabilities(),
        )


@pytest.mark.parametrize(
    ("field", "value"), [("supported", 1), ("plaintext_storage", 1)]
)
def test_state_fixed_flags_require_boolean_true(field: str, value: object) -> None:
    payload = {
        "protocol_version": 1,
        "request_id": REQUEST_ID,
        "enabled": True,
        "available": True,
        "item_count": 1,
        "max_items": 24,
        "revision": 2,
        "capabilities": _enabled_capabilities(),
        field: value,
    }

    with pytest.raises(ValidationError):
        MemoryStateResponse(**payload)


def _list_item(
    *,
    memory_id: str,
    category: str,
    value: str,
    updated_at: datetime,
) -> MemoryListItemResponse:
    return MemoryListItemResponse(
        id=memory_id,
        category=category,
        value=value,
        source="manual_ui",
        created_at=NOW,
        updated_at=updated_at,
    )


def test_list_response_requires_complete_unique_deterministic_order() -> None:
    address = _list_item(
        memory_id=MEMORY_ID,
        category="preferred_address",
        value="阿源",
        updated_at=NOW,
    )
    newest_preference = _list_item(
        memory_id=OTHER_MEMORY_ID,
        category="preference",
        value="喜欢花",
        updated_at=NOW + timedelta(minutes=2),
    )
    older_preference = _list_item(
        memory_id="1a88f7a60a3c49c0a4fe87a044264d24",
        category="preference",
        value="饮料不要太甜",
        updated_at=NOW + timedelta(minutes=1),
    )
    response = MemoryListResponse(
        protocol_version=1,
        request_id=REQUEST_ID,
        revision=3,
        item_count=3,
        max_items=24,
        items=[address, newest_preference, older_preference],
    )

    assert response.items[0].category.value == "preferred_address"
    with pytest.raises(ValidationError):
        MemoryListResponse(
            protocol_version=1,
            request_id=REQUEST_ID,
            revision=3,
            item_count=3,
            max_items=24,
            items=[newest_preference, address, older_preference],
        )
    with pytest.raises(ValidationError):
        MemoryListResponse(
            protocol_version=1,
            request_id=REQUEST_ID,
            revision=3,
            item_count=2,
            max_items=24,
            items=[address, address],
        )


def test_list_projection_forbids_internal_fields_and_serializes_utc_as_z() -> None:
    payload = {
        "id": MEMORY_ID,
        "category": "preferred_address",
        "value": "阿源",
        "source": "explicit_user",
        "created_at": NOW,
        "updated_at": NOW,
    }
    item = MemoryListItemResponse(**payload)
    serialized = item.model_dump(mode="json")

    assert serialized["created_at"].endswith("Z")
    assert serialized["updated_at"].endswith("Z")
    assert set(serialized) == {
        "id",
        "category",
        "value",
        "source",
        "created_at",
        "updated_at",
    }
    for forbidden_field in ("key", "profile_id", "conf_uid", "path"):
        with pytest.raises(ValidationError):
            MemoryListItemResponse(**payload, **{forbidden_field: "forbidden"})


@pytest.mark.parametrize(
    "timestamp",
    [NOW.replace(tzinfo=None), NOW.astimezone(timezone(timedelta(hours=8)))],
)
def test_list_projection_requires_utc_timestamps(timestamp: datetime) -> None:
    with pytest.raises(ValidationError):
        _list_item(
            memory_id=MEMORY_ID,
            category="important_fact",
            value="周末通常要加班",
            updated_at=timestamp,
        )


def test_successful_item_result_is_content_free_and_requires_memory_id() -> None:
    result = MemoryManagementResultResponse(
        protocol_version=1,
        request_id=REQUEST_ID,
        operation=MemoryManagementOperation.UPDATE,
        status=MemoryManagementStatus.SUCCESS,
        changed=True,
        memory_id=MEMORY_ID,
        revision=6,
        item_count=3,
        enabled=True,
    )

    assert set(result.model_dump()) == {
        "type",
        "protocol_version",
        "request_id",
        "operation",
        "status",
        "reason_code",
        "changed",
        "memory_id",
        "revision",
        "item_count",
        "enabled",
    }
    with pytest.raises(ValidationError):
        MemoryManagementResultResponse(
            protocol_version=1,
            request_id=REQUEST_ID,
            operation=MemoryManagementOperation.UPDATE,
            status=MemoryManagementStatus.SUCCESS,
            changed=True,
        )


def test_unsuccessful_result_requires_reason_and_cannot_report_change() -> None:
    rejected = MemoryManagementResultResponse(
        protocol_version=1,
        request_id=None,
        operation=MemoryManagementOperation.STATE,
        status=MemoryManagementStatus.REJECTED,
        reason_code=MemoryManagementReasonCode.INVALID_REQUEST,
        changed=False,
    )

    assert rejected.request_id is None
    with pytest.raises(ValidationError):
        MemoryManagementResultResponse(
            protocol_version=1,
            request_id=REQUEST_ID,
            operation=MemoryManagementOperation.DELETE,
            status=MemoryManagementStatus.FAILED,
            changed=False,
        )
    with pytest.raises(ValidationError):
        MemoryManagementResultResponse(
            protocol_version=1,
            request_id=REQUEST_ID,
            operation=MemoryManagementOperation.DELETE,
            status=MemoryManagementStatus.REJECTED,
            reason_code=MemoryManagementReasonCode.NOT_FOUND,
            changed=True,
        )


def test_failure_reason_codes_require_the_frozen_status_class() -> None:
    failed = MemoryManagementResultResponse(
        protocol_version=1,
        request_id=REQUEST_ID,
        operation=MemoryManagementOperation.LIST,
        status=MemoryManagementStatus.FAILED,
        reason_code=MemoryManagementReasonCode.STORAGE_FAILURE,
        changed=False,
    )

    assert failed.status == MemoryManagementStatus.FAILED
    with pytest.raises(ValidationError):
        MemoryManagementResultResponse(
            protocol_version=1,
            request_id=REQUEST_ID,
            operation=MemoryManagementOperation.LIST,
            status=MemoryManagementStatus.REJECTED,
            reason_code=MemoryManagementReasonCode.STORAGE_FAILURE,
            changed=False,
        )
    with pytest.raises(ValidationError):
        MemoryManagementResultResponse(
            protocol_version=1,
            request_id=REQUEST_ID,
            operation=MemoryManagementOperation.DELETE,
            status=MemoryManagementStatus.FAILED,
            reason_code=MemoryManagementReasonCode.NOT_FOUND,
            changed=False,
        )


def test_state_and_list_successes_require_dedicated_responses() -> None:
    for operation in (
        MemoryManagementOperation.STATE,
        MemoryManagementOperation.LIST,
    ):
        with pytest.raises(ValidationError):
            MemoryManagementResultResponse(
                protocol_version=1,
                request_id=REQUEST_ID,
                operation=operation,
                status=MemoryManagementStatus.SUCCESS,
                changed=False,
            )


def test_setting_success_requires_enabled_and_non_item_results_reject_memory_id() -> (
    None
):
    with pytest.raises(ValidationError):
        MemoryManagementResultResponse(
            protocol_version=1,
            request_id=REQUEST_ID,
            operation=MemoryManagementOperation.SET_ENABLED,
            status=MemoryManagementStatus.SUCCESS,
            changed=True,
        )
    with pytest.raises(ValidationError):
        MemoryManagementResultResponse(
            protocol_version=1,
            request_id=REQUEST_ID,
            operation=MemoryManagementOperation.CLEAR,
            status=MemoryManagementStatus.SUCCESS,
            changed=True,
            memory_id=MEMORY_ID,
        )


def test_request_and_response_examples_round_trip_through_union_adapters() -> None:
    request = MemoryUpsertRequest(
        protocol_version=1,
        request_id=REQUEST_ID,
        category="preference",
        value="饮料不要太甜",
        expected_revision=5,
    )
    response = MemoryManagementResultResponse(
        protocol_version=1,
        request_id=REQUEST_ID,
        operation="create",
        status="success",
        changed=True,
        memory_id=MEMORY_ID,
        revision=6,
        item_count=3,
        enabled=True,
    )

    parsed_request = MEMORY_MANAGEMENT_REQUEST_ADAPTER.validate_json(
        request.model_dump_json()
    )
    parsed_response = MEMORY_MANAGEMENT_RESPONSE_ADAPTER.validate_json(
        response.model_dump_json()
    )

    assert parsed_request == request
    assert parsed_response == response


def test_reason_code_values_are_frozen() -> None:
    assert [reason.value for reason in MemoryManagementReasonCode] == [
        "invalid_request",
        "unsupported_protocol_version",
        "local_access_required",
        "proxy_not_supported",
        "group_not_supported",
        "unsupported_agent",
        "invalid_scope",
        "memory_disabled",
        "storage_failure",
        "invalid_value",
        "sensitive_content",
        "capacity_reached",
        "not_found",
        "duplicate_item",
        "revision_conflict",
        "setting_conflict",
        "config_persist_failure",
        "internal_error",
    ]
