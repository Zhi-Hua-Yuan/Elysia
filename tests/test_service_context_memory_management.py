import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest

from open_llm_vtuber.config_manager import MemoryConfig
from open_llm_vtuber.memory import (
    MemoryCategory,
    MemoryManagementContext,
    MemoryManagementReasonCode,
    MemoryManagementResultResponse,
    MemoryManagementStatus,
    MemorySettingUpdateOutcome,
    MemorySettingUpdateRequest,
    MemoryStateRequest,
    MemoryUpsertRequest,
    PersistentMemoryService,
)
from open_llm_vtuber.service_context import ServiceContext


class RecordingMemorySettingUpdatePort:
    def __init__(self, outcome: MemorySettingUpdateOutcome) -> None:
        self.outcome = outcome
        self.calls: list[tuple[bool, bool]] = []

    async def update_enabled(
        self,
        *,
        expected_enabled: bool,
        enabled: bool,
    ) -> MemorySettingUpdateOutcome:
        self.calls.append((expected_enabled, enabled))
        return self.outcome


def make_context(tmp_path: Path, *, enabled: bool = True) -> ServiceContext:
    context = ServiceContext(project_root=tmp_path)
    context.config = SimpleNamespace(
        memory_config=MemoryConfig(
            enabled=enabled,
            profile_id="local_default",
            max_items=17,
            max_item_chars=123,
        )
    )
    context.system_config = SimpleNamespace(enable_proxy=False)
    context.character_config = SimpleNamespace(
        conf_uid="elysia_mvp_001",
        agent_config=SimpleNamespace(conversation_agent_choice="basic_memory_agent"),
    )
    context.memory_service = Mock(spec=PersistentMemoryService)
    return context


def test_service_context_builds_only_server_derived_management_state(
    tmp_path: Path,
) -> None:
    context = make_context(tmp_path)
    request = MemoryStateRequest(protocol_version=1, request_id=uuid4())
    expected = Mock()
    context.memory_management_controller.handle = AsyncMock(return_value=expected)

    result = asyncio.run(
        context.handle_memory_management_request(
            request,
            is_local_connection=True,
            group_active=True,
        )
    )

    assert result is expected
    runtime = context.memory_management_controller.handle.await_args.kwargs["context"]
    assert runtime == MemoryManagementContext(
        is_local_connection=True,
        proxy_enabled=False,
        group_active=True,
        agent_choice="basic_memory_agent",
        enabled=True,
        profile_id="local_default",
        character_conf_uid="elysia_mvp_001",
        max_items=17,
        max_item_chars=123,
        service=context.memory_service,
    )


def test_management_mutation_clears_voice_confirmation_but_state_does_not(
    tmp_path: Path,
) -> None:
    context = make_context(tmp_path)
    context.memory_command_controller.clear_pending = Mock()
    context.memory_management_controller.handle = AsyncMock(
        return_value=MemoryManagementResultResponse(
            protocol_version=1,
            request_id=uuid4(),
            operation="create",
            status=MemoryManagementStatus.SUCCESS,
            changed=True,
            memory_id="10000000000000000000000000000000",
            revision=1,
            item_count=1,
        )
    )
    state = MemoryStateRequest(protocol_version=1, request_id=uuid4())
    mutation = MemoryUpsertRequest(
        protocol_version=1,
        request_id=uuid4(),
        category=MemoryCategory.PREFERENCE,
        value="我喜欢花",
        expected_revision=0,
    )

    asyncio.run(
        context.handle_memory_management_request(
            state,
            is_local_connection=True,
        )
    )
    context.memory_command_controller.clear_pending.assert_not_called()

    asyncio.run(
        context.handle_memory_management_request(
            mutation,
            is_local_connection=True,
        )
    )
    context.memory_command_controller.clear_pending.assert_called_once_with()


def test_disabled_context_keeps_management_service_without_enabling_conversation(
    tmp_path: Path,
) -> None:
    context = ServiceContext(project_root=tmp_path)
    config = MemoryConfig(enabled=False, storage_dir="memory_data")

    context._init_memory_service(config)

    assert isinstance(context.memory_service, PersistentMemoryService)
    assert not context.memory_service.store.storage_root.exists()


def test_bind_memory_setting_update_port_routes_updates_without_side_effects(
    tmp_path: Path,
) -> None:
    context = make_context(tmp_path)
    original_service = context.memory_service
    original_command_controller = context.memory_command_controller
    port = RecordingMemorySettingUpdatePort(
        MemorySettingUpdateOutcome(
            status=MemoryManagementStatus.SUCCESS,
            changed=True,
            enabled=False,
        )
    )

    context.bind_memory_setting_update_port(port)
    response = asyncio.run(
        context.handle_memory_management_request(
            MemorySettingUpdateRequest(
                protocol_version=1,
                request_id=uuid4(),
                expected_enabled=True,
                enabled=False,
            ),
            is_local_connection=True,
        )
    )

    assert port.calls == [(True, False)]
    assert response.status == MemoryManagementStatus.SUCCESS
    assert response.changed is True
    assert response.enabled is False
    assert context.config.memory_config.enabled is True
    assert context.memory_service is original_service
    assert context.memory_command_controller is original_command_controller


def test_bind_memory_setting_update_port_is_idempotent_for_same_instance(
    tmp_path: Path,
) -> None:
    context = make_context(tmp_path)
    port = RecordingMemorySettingUpdatePort(
        MemorySettingUpdateOutcome(
            status=MemoryManagementStatus.SUCCESS,
            changed=False,
            enabled=True,
        )
    )

    context.bind_memory_setting_update_port(port)
    bound_controller = context.memory_management_controller
    context.bind_memory_setting_update_port(port)

    assert context.memory_management_controller is bound_controller


def test_bind_memory_setting_update_port_rejects_rebinding(
    tmp_path: Path,
) -> None:
    context = make_context(tmp_path)
    outcome = MemorySettingUpdateOutcome(
        status=MemoryManagementStatus.SUCCESS,
        changed=False,
        enabled=True,
    )
    original_port = RecordingMemorySettingUpdatePort(outcome)
    replacement_port = RecordingMemorySettingUpdatePort(outcome)
    context.bind_memory_setting_update_port(original_port)
    bound_controller = context.memory_management_controller

    with pytest.raises(
        RuntimeError,
        match="memory setting update port is already bound",
    ):
        context.bind_memory_setting_update_port(replacement_port)

    assert context.memory_management_controller is bound_controller


def test_bind_memory_setting_update_port_rejects_none(tmp_path: Path) -> None:
    context = make_context(tmp_path)
    original_controller = context.memory_management_controller

    with pytest.raises(
        ValueError,
        match="memory setting update port must not be None",
    ):
        context.bind_memory_setting_update_port(None)  # type: ignore[arg-type]

    assert context.memory_management_controller is original_controller


def test_unbound_memory_setting_update_port_fails_closed(tmp_path: Path) -> None:
    context = make_context(tmp_path)

    response = asyncio.run(
        context.handle_memory_management_request(
            MemorySettingUpdateRequest(
                protocol_version=1,
                request_id=uuid4(),
                expected_enabled=True,
                enabled=False,
            ),
            is_local_connection=True,
        )
    )

    assert response.status == MemoryManagementStatus.FAILED
    assert response.reason_code == MemoryManagementReasonCode.CONFIG_PERSIST_FAILURE
    assert response.changed is False
    assert response.enabled is True
    assert context.config.memory_config.enabled is True
