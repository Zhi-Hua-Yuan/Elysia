"""Application-layer controller for the frozen memory-management protocol."""

from __future__ import annotations

from uuid import UUID

from loguru import logger

from .management_context import (
    MemoryManagementContext,
    MemorySettingUpdatePort,
)
from .management_types import (
    MEMORY_MANAGEMENT_PROTOCOL_VERSION,
    MemoryClearRequest,
    MemoryDeleteRequest,
    MemoryListItemResponse,
    MemoryListRequest,
    MemoryListResponse,
    MemoryManagementCapabilities,
    MemoryManagementOperation,
    MemoryManagementReasonCode,
    MemoryManagementRequest,
    MemoryManagementResponse,
    MemoryManagementResultResponse,
    MemoryManagementStatus,
    MemorySettingUpdateRequest,
    MemoryStateRequest,
    MemoryStateResponse,
    MemoryUpdateRequest,
    MemoryUpsertRequest,
)
from .types import (
    MemoryMutationResult,
    MemoryOperationStatus,
    MemoryReasonCode,
    MemoryScope,
)


_SERVICE_REASON_MAP = {
    MemoryReasonCode.MEMORY_DISABLED: MemoryManagementReasonCode.MEMORY_DISABLED,
    MemoryReasonCode.INVALID_SCOPE: MemoryManagementReasonCode.INVALID_SCOPE,
    MemoryReasonCode.INVALID_VALUE: MemoryManagementReasonCode.INVALID_VALUE,
    MemoryReasonCode.SENSITIVE_CONTENT: MemoryManagementReasonCode.SENSITIVE_CONTENT,
    MemoryReasonCode.CAPACITY_REACHED: MemoryManagementReasonCode.CAPACITY_REACHED,
    MemoryReasonCode.NOT_FOUND: MemoryManagementReasonCode.NOT_FOUND,
    MemoryReasonCode.DUPLICATE_ITEM: MemoryManagementReasonCode.DUPLICATE_ITEM,
    MemoryReasonCode.REVISION_CONFLICT: MemoryManagementReasonCode.REVISION_CONFLICT,
    MemoryReasonCode.STORAGE_FAILURE: MemoryManagementReasonCode.STORAGE_FAILURE,
}

_MUTATION_REQUEST_TYPES = (
    MemoryUpsertRequest,
    MemoryUpdateRequest,
    MemoryDeleteRequest,
    MemoryClearRequest,
    MemorySettingUpdateRequest,
)


def is_memory_management_mutation_request(
    request: MemoryManagementRequest,
) -> bool:
    """Return whether a request invalidates a pending voice clear confirmation."""

    return isinstance(request, _MUTATION_REQUEST_TYPES)


class MemoryManagementController:
    """Authorize typed management requests and map them to business services."""

    def __init__(
        self,
        setting_update_port: MemorySettingUpdatePort | None = None,
    ) -> None:
        self._setting_update_port = setting_update_port

    async def handle(
        self,
        request: MemoryManagementRequest,
        *,
        context: MemoryManagementContext,
    ) -> MemoryManagementResponse:
        operation = _operation_for_request(request)
        try:
            rejection = _access_rejection(context)
            if rejection is not None:
                return _failure_result(
                    request_id=request.request_id,
                    operation=operation,
                    reason_code=rejection,
                )

            scope = _derive_scope(context)

            if isinstance(request, MemoryStateRequest):
                return await self._state(request, context=context, scope=scope)
            if isinstance(request, MemorySettingUpdateRequest):
                return await self._set_enabled(request, context=context)

            service = context.service
            if service is None:
                return _failure_result(
                    request_id=request.request_id,
                    operation=operation,
                    reason_code=MemoryManagementReasonCode.STORAGE_FAILURE,
                )

            if isinstance(request, MemoryListRequest):
                return await self._list(request, context=context, scope=scope)
            if isinstance(request, (MemoryUpsertRequest, MemoryUpdateRequest)) and not (
                context.enabled
            ):
                return _failure_result(
                    request_id=request.request_id,
                    operation=operation,
                    reason_code=MemoryManagementReasonCode.MEMORY_DISABLED,
                )
            if isinstance(request, MemoryUpsertRequest):
                result = await service.management_upsert_item(
                    scope,
                    category=request.category,
                    value=request.value,
                    expected_revision=request.expected_revision,
                    max_items=context.max_items,
                    max_item_chars=context.max_item_chars,
                )
            elif isinstance(request, MemoryUpdateRequest):
                result = await service.management_update_item_by_id(
                    scope,
                    memory_id=request.memory_id,
                    category=request.category,
                    value=request.value,
                    expected_revision=request.expected_revision,
                    max_item_chars=context.max_item_chars,
                )
            elif isinstance(request, MemoryDeleteRequest):
                result = await service.management_delete_item_by_id(
                    scope,
                    memory_id=request.memory_id,
                    expected_revision=request.expected_revision,
                )
            elif isinstance(request, MemoryClearRequest):
                result = await service.management_clear_items(
                    scope,
                    expected_revision=request.expected_revision,
                )
            else:  # pragma: no cover - protected by the frozen request union
                raise TypeError("unsupported memory management request")

            return _mutation_response(
                request_id=request.request_id,
                operation=operation,
                result=result,
            )
        except _InvalidManagementScope:
            return _failure_result(
                request_id=getattr(request, "request_id", None),
                operation=operation,
                reason_code=MemoryManagementReasonCode.INVALID_SCOPE,
            )
        except Exception as exc:
            logger.warning(
                "Memory management request failed safely (operation={}, error_type={})",
                operation.value,
                type(exc).__name__,
            )
            return _failure_result(
                request_id=getattr(request, "request_id", None),
                operation=operation,
                reason_code=MemoryManagementReasonCode.INTERNAL_ERROR,
            )

    async def _state(
        self,
        request: MemoryStateRequest,
        *,
        context: MemoryManagementContext,
        scope: MemoryScope,
    ) -> MemoryStateResponse:
        if context.service is None:
            return _unavailable_state(request, context)

        listed = await context.service.list_items(scope, force_reload=True)
        if listed.operation.status != MemoryOperationStatus.SUCCESS:
            return _unavailable_state(request, context)

        return MemoryStateResponse(
            protocol_version=MEMORY_MANAGEMENT_PROTOCOL_VERSION,
            request_id=request.request_id,
            enabled=context.enabled,
            available=True,
            item_count=len(listed.items),
            max_items=context.max_items,
            revision=listed.operation.revision,
            capabilities=_available_capabilities(context.enabled),
        )

    async def _list(
        self,
        request: MemoryListRequest,
        *,
        context: MemoryManagementContext,
        scope: MemoryScope,
    ) -> MemoryManagementResponse:
        service = context.service
        if service is None:  # pragma: no cover - checked by handle
            return _failure_result(
                request_id=request.request_id,
                operation=MemoryManagementOperation.LIST,
                reason_code=MemoryManagementReasonCode.STORAGE_FAILURE,
            )

        listed = await service.list_items(scope, force_reload=True)
        if listed.operation.status != MemoryOperationStatus.SUCCESS:
            reason = _map_service_reason(listed.operation.reason_code)
            return _failure_result(
                request_id=request.request_id,
                operation=MemoryManagementOperation.LIST,
                reason_code=(
                    reason
                    if reason == MemoryManagementReasonCode.STORAGE_FAILURE
                    else MemoryManagementReasonCode.INTERNAL_ERROR
                ),
            )

        items = [
            MemoryListItemResponse(
                id=item.id,
                category=item.category,
                value=item.value,
                source=item.source,
                created_at=item.created_at,
                updated_at=item.updated_at,
            )
            for item in listed.items
        ]
        return MemoryListResponse(
            protocol_version=MEMORY_MANAGEMENT_PROTOCOL_VERSION,
            request_id=request.request_id,
            revision=listed.operation.revision,
            item_count=len(items),
            max_items=context.max_items,
            items=items,
        )

    async def _set_enabled(
        self,
        request: MemorySettingUpdateRequest,
        *,
        context: MemoryManagementContext,
    ) -> MemoryManagementResultResponse:
        if request.expected_enabled != context.enabled:
            return _failure_result(
                request_id=request.request_id,
                operation=MemoryManagementOperation.SET_ENABLED,
                reason_code=MemoryManagementReasonCode.SETTING_CONFLICT,
                enabled=context.enabled,
            )
        if request.enabled == context.enabled:
            return MemoryManagementResultResponse(
                protocol_version=MEMORY_MANAGEMENT_PROTOCOL_VERSION,
                request_id=request.request_id,
                operation=MemoryManagementOperation.SET_ENABLED,
                status=MemoryManagementStatus.SUCCESS,
                changed=False,
                enabled=context.enabled,
            )
        if self._setting_update_port is None:
            return _failure_result(
                request_id=request.request_id,
                operation=MemoryManagementOperation.SET_ENABLED,
                reason_code=MemoryManagementReasonCode.CONFIG_PERSIST_FAILURE,
                enabled=context.enabled,
            )

        outcome = await self._setting_update_port.update_enabled(
            expected_enabled=request.expected_enabled,
            enabled=request.enabled,
        )
        return MemoryManagementResultResponse(
            protocol_version=MEMORY_MANAGEMENT_PROTOCOL_VERSION,
            request_id=request.request_id,
            operation=MemoryManagementOperation.SET_ENABLED,
            status=outcome.status,
            reason_code=outcome.reason_code,
            changed=outcome.changed,
            enabled=outcome.enabled,
        )


def _access_rejection(
    context: MemoryManagementContext,
) -> MemoryManagementReasonCode | None:
    if context.is_local_connection is not True:
        return MemoryManagementReasonCode.LOCAL_ACCESS_REQUIRED
    if context.proxy_enabled is True:
        return MemoryManagementReasonCode.PROXY_NOT_SUPPORTED
    if context.group_active is True:
        return MemoryManagementReasonCode.GROUP_NOT_SUPPORTED
    if context.agent_choice != "basic_memory_agent":
        return MemoryManagementReasonCode.UNSUPPORTED_AGENT
    if (
        not context.profile_id
        or not context.character_conf_uid
        or type(context.max_items) is not int
        or not 1 <= context.max_items <= 100
        or type(context.max_item_chars) is not int
        or not 20 <= context.max_item_chars <= 500
    ):
        return MemoryManagementReasonCode.INVALID_SCOPE
    return None


def _derive_scope(context: MemoryManagementContext) -> MemoryScope:
    try:
        return MemoryScope(
            profile_id=context.profile_id,
            character_conf_uid=context.character_conf_uid,
        )
    except (TypeError, ValueError) as exc:
        raise _InvalidManagementScope from exc


class _InvalidManagementScope(ValueError):
    pass


def _available_capabilities(enabled: bool) -> MemoryManagementCapabilities:
    return MemoryManagementCapabilities(
        list=True,
        create=enabled,
        update=enabled,
        delete=True,
        clear=True,
        set_enabled=True,
    )


def _unavailable_state(
    request: MemoryStateRequest,
    context: MemoryManagementContext,
) -> MemoryStateResponse:
    return MemoryStateResponse(
        protocol_version=MEMORY_MANAGEMENT_PROTOCOL_VERSION,
        request_id=request.request_id,
        enabled=context.enabled,
        available=False,
        item_count=None,
        max_items=context.max_items,
        revision=None,
        capabilities=MemoryManagementCapabilities(
            list=False,
            create=False,
            update=False,
            delete=False,
            clear=False,
            set_enabled=True,
        ),
        reason_code=MemoryManagementReasonCode.STORAGE_FAILURE,
    )


def _operation_for_request(
    request: MemoryManagementRequest,
) -> MemoryManagementOperation:
    if isinstance(request, MemoryStateRequest):
        return MemoryManagementOperation.STATE
    if isinstance(request, MemoryListRequest):
        return MemoryManagementOperation.LIST
    if isinstance(request, MemoryUpsertRequest):
        return MemoryManagementOperation.CREATE
    if isinstance(request, MemoryUpdateRequest):
        return MemoryManagementOperation.UPDATE
    if isinstance(request, MemoryDeleteRequest):
        return MemoryManagementOperation.DELETE
    if isinstance(request, MemoryClearRequest):
        return MemoryManagementOperation.CLEAR
    if isinstance(request, MemorySettingUpdateRequest):
        return MemoryManagementOperation.SET_ENABLED
    raise TypeError("unsupported memory management request")


def _mutation_response(
    *,
    request_id: UUID,
    operation: MemoryManagementOperation,
    result: MemoryMutationResult,
) -> MemoryManagementResultResponse:
    if result.operation.status == MemoryOperationStatus.SUCCESS:
        return MemoryManagementResultResponse(
            protocol_version=MEMORY_MANAGEMENT_PROTOCOL_VERSION,
            request_id=request_id,
            operation=operation,
            status=MemoryManagementStatus.SUCCESS,
            changed=result.changed,
            memory_id=result.operation.memory_id,
            revision=result.operation.revision,
            item_count=result.item_count,
        )

    reason = _map_service_reason(result.operation.reason_code)
    if result.operation.status == MemoryOperationStatus.REJECTED and reason not in {
        MemoryManagementReasonCode.STORAGE_FAILURE,
        MemoryManagementReasonCode.INTERNAL_ERROR,
    }:
        status = MemoryManagementStatus.REJECTED
    elif (
        result.operation.status == MemoryOperationStatus.FAILED
        and reason == MemoryManagementReasonCode.STORAGE_FAILURE
    ):
        status = MemoryManagementStatus.FAILED
    else:
        reason = MemoryManagementReasonCode.INTERNAL_ERROR
        status = MemoryManagementStatus.FAILED

    return MemoryManagementResultResponse(
        protocol_version=MEMORY_MANAGEMENT_PROTOCOL_VERSION,
        request_id=request_id,
        operation=operation,
        status=status,
        reason_code=reason,
        changed=False,
        revision=result.operation.revision,
        item_count=result.item_count,
    )


def _map_service_reason(
    reason: MemoryReasonCode | None,
) -> MemoryManagementReasonCode:
    if reason is None:
        return MemoryManagementReasonCode.INTERNAL_ERROR
    return _SERVICE_REASON_MAP.get(reason, MemoryManagementReasonCode.INTERNAL_ERROR)


def _failure_result(
    *,
    request_id: UUID | None,
    operation: MemoryManagementOperation,
    reason_code: MemoryManagementReasonCode,
    revision: int | None = None,
    item_count: int | None = None,
    enabled: bool | None = None,
) -> MemoryManagementResultResponse:
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
        revision=revision,
        item_count=item_count,
        enabled=enabled,
    )
