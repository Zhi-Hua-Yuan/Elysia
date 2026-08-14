import asyncio
from dataclasses import replace
from datetime import datetime, timezone
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from loguru import logger

from open_llm_vtuber.memory import (
    MemoryAction,
    MemoryCategory,
    MemoryClearRequest,
    MemoryDeleteRequest,
    MemoryItem,
    MemoryListRequest,
    MemoryListResponse,
    MemoryListResult,
    MemoryManagementContext,
    MemoryManagementController,
    MemoryManagementOperation,
    MemoryManagementReasonCode,
    MemoryManagementResultResponse,
    MemoryManagementStatus,
    MemoryMutationResult,
    MemoryOperationResult,
    MemoryOperationStatus,
    MemoryReasonCode,
    MemoryScope,
    MemorySettingUpdateOutcome,
    MemorySettingUpdateRequest,
    MemorySource,
    MemoryStateRequest,
    MemoryStateResponse,
    MemoryUpdateRequest,
    MemoryUpsertRequest,
    PersistentMemoryService,
)


NOW = datetime(2026, 8, 14, 8, 0, tzinfo=timezone.utc)
MEMORY_ID = "10000000000000000000000000000000"


def make_context(**changes) -> MemoryManagementContext:
    service = Mock(spec=PersistentMemoryService)
    base = MemoryManagementContext(
        is_local_connection=True,
        proxy_enabled=False,
        group_active=False,
        agent_choice="basic_memory_agent",
        enabled=True,
        profile_id="local_default",
        character_conf_uid="elysia_mvp_001",
        max_items=24,
        max_item_chars=160,
        service=service,
    )
    return replace(base, **changes)


def success_list(*items: MemoryItem, revision: int = 0) -> MemoryListResult:
    return MemoryListResult(
        operation=MemoryOperationResult(
            status=MemoryOperationStatus.SUCCESS,
            action=MemoryAction.LIST,
            revision=revision,
        ),
        items=items,
    )


def mutation_result(
    *,
    status: MemoryOperationStatus = MemoryOperationStatus.SUCCESS,
    action: MemoryAction = MemoryAction.CREATE,
    reason: MemoryReasonCode | None = None,
    changed: bool = True,
    revision: int | None = 1,
    item_count: int | None = 1,
    memory_id: str | None = MEMORY_ID,
) -> MemoryMutationResult:
    return MemoryMutationResult(
        operation=MemoryOperationResult(
            status=status,
            action=action,
            reason_code=reason,
            memory_id=memory_id,
            revision=revision,
        ),
        changed=changed,
        item_count=item_count,
    )


@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        (
            {
                "is_local_connection": False,
                "proxy_enabled": True,
                "group_active": True,
                "agent_choice": "mem0_agent",
            },
            MemoryManagementReasonCode.LOCAL_ACCESS_REQUIRED,
        ),
        (
            {"proxy_enabled": True, "group_active": True, "agent_choice": "x"},
            MemoryManagementReasonCode.PROXY_NOT_SUPPORTED,
        ),
        (
            {"group_active": True, "agent_choice": "x"},
            MemoryManagementReasonCode.GROUP_NOT_SUPPORTED,
        ),
        (
            {"agent_choice": "mem0_agent"},
            MemoryManagementReasonCode.UNSUPPORTED_AGENT,
        ),
        (
            {"profile_id": "../invalid"},
            MemoryManagementReasonCode.INVALID_SCOPE,
        ),
    ],
)
def test_access_rejections_are_deterministic_and_do_not_read_storage(
    changes: dict,
    reason: MemoryManagementReasonCode,
) -> None:
    context = make_context(**changes)
    context.service.list_items = AsyncMock()
    request = MemoryStateRequest(protocol_version=1, request_id=uuid4())

    result = asyncio.run(MemoryManagementController().handle(request, context=context))

    assert isinstance(result, MemoryManagementResultResponse)
    assert result.status == MemoryManagementStatus.REJECTED
    assert result.reason_code == reason
    assert result.revision is None
    assert result.item_count is None
    assert result.enabled is None
    context.service.list_items.assert_not_awaited()


@pytest.mark.parametrize("enabled", [True, False])
def test_state_returns_the_frozen_available_capabilities(enabled: bool) -> None:
    context = make_context(enabled=enabled)
    context.service.list_items = AsyncMock(return_value=success_list(revision=3))
    request = MemoryStateRequest(protocol_version=1, request_id=uuid4())

    result = asyncio.run(MemoryManagementController().handle(request, context=context))

    assert isinstance(result, MemoryStateResponse)
    assert result.available
    assert result.enabled is enabled
    assert result.revision == 3
    assert result.item_count == 0
    assert result.capabilities.list
    assert result.capabilities.create is enabled
    assert result.capabilities.update is enabled
    assert result.capabilities.delete
    assert result.capabilities.clear
    assert result.capabilities.set_enabled
    context.service.list_items.assert_awaited_once_with(
        MemoryScope(
            profile_id="local_default",
            character_conf_uid="elysia_mvp_001",
        ),
        force_reload=True,
    )


def test_state_hides_document_metadata_when_storage_is_unavailable() -> None:
    request = MemoryStateRequest(protocol_version=1, request_id=uuid4())

    result = asyncio.run(
        MemoryManagementController().handle(
            request,
            context=make_context(service=None, enabled=False),
        )
    )

    assert isinstance(result, MemoryStateResponse)
    assert not result.available
    assert not result.enabled
    assert result.revision is None
    assert result.item_count is None
    assert result.reason_code == MemoryManagementReasonCode.STORAGE_FAILURE
    assert not result.capabilities.list
    assert result.capabilities.set_enabled


def test_list_projects_only_public_fields_in_service_order() -> None:
    item = MemoryItem(
        id=MEMORY_ID,
        category=MemoryCategory.PREFERENCE,
        key="preference:private-key",
        value="我喜欢花",
        source=MemorySource.EXPLICIT_USER,
        created_at=NOW,
        updated_at=NOW,
    )
    context = make_context(enabled=False)
    context.service.list_items = AsyncMock(return_value=success_list(item, revision=4))
    request = MemoryListRequest(protocol_version=1, request_id=uuid4())

    result = asyncio.run(MemoryManagementController().handle(request, context=context))

    assert isinstance(result, MemoryListResponse)
    assert result.revision == 4
    assert result.item_count == 1
    assert result.items[0].value == "我喜欢花"
    payload = result.model_dump(mode="json")
    assert "key" not in payload["items"][0]
    assert "profile_id" not in payload
    assert "character_conf_uid" not in payload


def test_disabled_context_rejects_create_but_allows_delete() -> None:
    context = make_context(enabled=False)
    context.service.management_upsert_item = AsyncMock()
    context.service.management_delete_item_by_id = AsyncMock(
        return_value=mutation_result(action=MemoryAction.DELETE)
    )
    controller = MemoryManagementController()
    create = MemoryUpsertRequest(
        protocol_version=1,
        request_id=uuid4(),
        category=MemoryCategory.PREFERENCE,
        value="我喜欢花",
        expected_revision=0,
    )
    delete = MemoryDeleteRequest(
        protocol_version=1,
        request_id=uuid4(),
        memory_id=MEMORY_ID,
        expected_revision=0,
    )

    create_result = asyncio.run(controller.handle(create, context=context))
    delete_result = asyncio.run(controller.handle(delete, context=context))

    assert create_result.reason_code == MemoryManagementReasonCode.MEMORY_DISABLED
    context.service.management_upsert_item.assert_not_awaited()
    assert delete_result.status == MemoryManagementStatus.SUCCESS
    context.service.management_delete_item_by_id.assert_awaited_once()


def test_requests_dispatch_strict_parameters_and_keep_request_operation() -> None:
    context = make_context()
    context.service.management_upsert_item = AsyncMock(
        return_value=mutation_result(action=MemoryAction.UPDATE, changed=False)
    )
    request = MemoryUpsertRequest(
        protocol_version=1,
        request_id=uuid4(),
        category=MemoryCategory.PREFERRED_ADDRESS,
        value="阿源",
        expected_revision=7,
    )

    result = asyncio.run(MemoryManagementController().handle(request, context=context))

    assert result.operation == MemoryManagementOperation.CREATE
    assert result.status == MemoryManagementStatus.SUCCESS
    assert not result.changed
    context.service.management_upsert_item.assert_awaited_once_with(
        MemoryScope(
            profile_id="local_default",
            character_conf_uid="elysia_mvp_001",
        ),
        category=MemoryCategory.PREFERRED_ADDRESS,
        value="阿源",
        expected_revision=7,
        max_items=24,
        max_item_chars=160,
    )


@pytest.mark.parametrize(
    ("reason", "expected_status"),
    [
        (MemoryReasonCode.INVALID_VALUE, MemoryManagementStatus.REJECTED),
        (MemoryReasonCode.SENSITIVE_CONTENT, MemoryManagementStatus.REJECTED),
        (MemoryReasonCode.CAPACITY_REACHED, MemoryManagementStatus.REJECTED),
        (MemoryReasonCode.NOT_FOUND, MemoryManagementStatus.REJECTED),
        (MemoryReasonCode.DUPLICATE_ITEM, MemoryManagementStatus.REJECTED),
        (MemoryReasonCode.REVISION_CONFLICT, MemoryManagementStatus.REJECTED),
        (MemoryReasonCode.STORAGE_FAILURE, MemoryManagementStatus.FAILED),
    ],
)
def test_service_reasons_map_to_frozen_management_results(
    reason: MemoryReasonCode,
    expected_status: MemoryManagementStatus,
) -> None:
    context = make_context()
    service_status = (
        MemoryOperationStatus.FAILED
        if reason == MemoryReasonCode.STORAGE_FAILURE
        else MemoryOperationStatus.REJECTED
    )
    context.service.management_update_item_by_id = AsyncMock(
        return_value=mutation_result(
            status=service_status,
            action=MemoryAction.UPDATE,
            reason=reason,
            changed=False,
            memory_id=None,
        )
    )
    request = MemoryUpdateRequest(
        protocol_version=1,
        request_id=uuid4(),
        memory_id=MEMORY_ID,
        category=MemoryCategory.PREFERENCE,
        value="我喜欢花",
        expected_revision=0,
    )

    result = asyncio.run(MemoryManagementController().handle(request, context=context))

    assert result.status == expected_status
    assert result.reason_code.value == reason.value
    assert not result.changed


def test_clear_dispatches_without_voice_confirmation_state() -> None:
    context = make_context(enabled=False)
    context.service.management_clear_items = AsyncMock(
        return_value=mutation_result(
            action=MemoryAction.CLEAR,
            memory_id=None,
            item_count=0,
        )
    )
    request = MemoryClearRequest(
        protocol_version=1,
        request_id=uuid4(),
        expected_revision=3,
        confirm=True,
    )

    result = asyncio.run(MemoryManagementController().handle(request, context=context))

    assert result.status == MemoryManagementStatus.SUCCESS
    context.service.management_clear_items.assert_awaited_once_with(
        MemoryScope(
            profile_id="local_default",
            character_conf_uid="elysia_mvp_001",
        ),
        expected_revision=3,
    )


def test_setting_conflict_and_idempotence_do_not_call_port() -> None:
    port = Mock()
    port.update_enabled = AsyncMock()
    controller = MemoryManagementController(port)
    context = make_context(enabled=True)
    conflict = MemorySettingUpdateRequest(
        protocol_version=1,
        request_id=uuid4(),
        enabled=False,
        expected_enabled=False,
    )
    unchanged = MemorySettingUpdateRequest(
        protocol_version=1,
        request_id=uuid4(),
        enabled=True,
        expected_enabled=True,
    )

    conflict_result = asyncio.run(controller.handle(conflict, context=context))
    unchanged_result = asyncio.run(controller.handle(unchanged, context=context))

    assert conflict_result.reason_code == MemoryManagementReasonCode.SETTING_CONFLICT
    assert conflict_result.enabled is True
    assert unchanged_result.status == MemoryManagementStatus.SUCCESS
    assert not unchanged_result.changed
    port.update_enabled.assert_not_awaited()


def test_setting_port_success_and_missing_port_failure() -> None:
    port = Mock()
    port.update_enabled = AsyncMock(
        return_value=MemorySettingUpdateOutcome(
            status=MemoryManagementStatus.SUCCESS,
            changed=True,
            enabled=False,
        )
    )
    request = MemorySettingUpdateRequest(
        protocol_version=1,
        request_id=uuid4(),
        enabled=False,
        expected_enabled=True,
    )
    context = make_context(enabled=True, service=None)

    success = asyncio.run(
        MemoryManagementController(port).handle(request, context=context)
    )
    failed = asyncio.run(MemoryManagementController().handle(request, context=context))

    assert success.status == MemoryManagementStatus.SUCCESS
    assert success.changed and success.enabled is False
    assert failed.status == MemoryManagementStatus.FAILED
    assert failed.reason_code == MemoryManagementReasonCode.CONFIG_PERSIST_FAILURE
    assert failed.enabled is True


def test_unexpected_failure_is_sanitized() -> None:
    secret = "PRIVATE_MEMORY_VALUE"
    context = make_context()
    context.service.list_items = AsyncMock(side_effect=RuntimeError(secret))
    logs: list[str] = []
    sink_id = logger.add(
        lambda message: logs.append(str(message)),
        format="{level} {message}",
        level="WARNING",
    )
    try:
        result = asyncio.run(
            MemoryManagementController().handle(
                MemoryListRequest(protocol_version=1, request_id=uuid4()),
                context=context,
            )
        )
    finally:
        logger.remove(sink_id)

    assert result.status == MemoryManagementStatus.FAILED
    assert result.reason_code == MemoryManagementReasonCode.INTERNAL_ERROR
    output = "".join(logs)
    assert "RuntimeError" in output
    assert secret not in output
