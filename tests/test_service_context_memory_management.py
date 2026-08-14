import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

from open_llm_vtuber.config_manager import MemoryConfig
from open_llm_vtuber.memory import (
    MemoryCategory,
    MemoryManagementContext,
    MemoryManagementResultResponse,
    MemoryManagementStatus,
    MemoryStateRequest,
    MemoryUpsertRequest,
    PersistentMemoryService,
)
from open_llm_vtuber.service_context import ServiceContext


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
