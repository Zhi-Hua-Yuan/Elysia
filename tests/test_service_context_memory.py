import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from loguru import logger

from open_llm_vtuber.config_manager import MemoryConfig
from open_llm_vtuber.memory import MemoryScope, PersistentMemoryService
from open_llm_vtuber.service_context import ServiceContext


def make_context(
    tmp_path: Path,
    *,
    enabled: bool = True,
    agent_choice: str = "basic_memory_agent",
    enable_proxy: bool = False,
) -> tuple[ServiceContext, SimpleNamespace]:
    context = ServiceContext(project_root=tmp_path)
    memory_config = MemoryConfig(
        enabled=enabled,
        profile_id="local_default",
        storage_dir="memory_data",
        max_item_chars=123,
        max_context_chars=456,
    )
    context.config = SimpleNamespace(memory_config=memory_config)
    context.system_config = SimpleNamespace(enable_proxy=enable_proxy)
    context.character_config = SimpleNamespace(
        conf_uid="elysia_mvp_001",
        agent_config=SimpleNamespace(conversation_agent_choice=agent_choice),
    )
    service = SimpleNamespace(
        render_context=AsyncMock(return_value="safe memory context"),
        mark_unavailable=Mock(),
    )
    context.memory_service = service
    return context, service


def test_service_context_passes_current_scope_limits_and_reload_flag(
    tmp_path: Path,
) -> None:
    context, service = make_context(tmp_path)

    result = asyncio.run(context.get_persistent_memory_context(force_reload=True))

    assert result == "safe memory context"
    service.render_context.assert_awaited_once_with(
        MemoryScope(
            profile_id="local_default",
            character_conf_uid="elysia_mvp_001",
        ),
        max_item_chars=123,
        max_context_chars=456,
        force_reload=True,
    )


def test_character_change_uses_a_new_memory_scope(tmp_path: Path) -> None:
    context, service = make_context(tmp_path)

    asyncio.run(context.get_persistent_memory_context())
    context.character_config.conf_uid = "another_character"
    asyncio.run(context.get_persistent_memory_context())

    scopes = [call.args[0] for call in service.render_context.await_args_list]
    assert scopes == [
        MemoryScope(
            profile_id="local_default",
            character_conf_uid="elysia_mvp_001",
        ),
        MemoryScope(
            profile_id="local_default",
            character_conf_uid="another_character",
        ),
    ]


def test_disabled_memory_does_not_call_service(tmp_path: Path) -> None:
    context, service = make_context(tmp_path, enabled=False)

    assert asyncio.run(context.get_persistent_memory_context()) == ""
    service.render_context.assert_not_awaited()


def test_missing_service_returns_empty_context(tmp_path: Path) -> None:
    context, _ = make_context(tmp_path)
    context.memory_service = None

    assert asyncio.run(context.get_persistent_memory_context()) == ""


def test_group_proxy_and_non_basic_agent_are_ineligible(tmp_path: Path) -> None:
    group_context, group_service = make_context(tmp_path)
    proxy_context, proxy_service = make_context(tmp_path, enable_proxy=True)
    other_agent_context, other_agent_service = make_context(
        tmp_path,
        agent_choice="mem0_agent",
    )

    assert (
        asyncio.run(
            group_context.get_persistent_memory_context(group_conversation=True)
        )
        == ""
    )
    assert asyncio.run(proxy_context.get_persistent_memory_context()) == ""
    assert asyncio.run(other_agent_context.get_persistent_memory_context()) == ""
    group_service.render_context.assert_not_awaited()
    proxy_service.render_context.assert_not_awaited()
    other_agent_service.render_context.assert_not_awaited()


def test_memory_failure_is_desensitized_and_scope_is_disabled(tmp_path: Path) -> None:
    secret = "PRIVATE_MEMORY_VALUE"
    context = ServiceContext(project_root=tmp_path)
    context.config = SimpleNamespace(
        memory_config=MemoryConfig(enabled=True, profile_id="local_default")
    )
    context.system_config = SimpleNamespace(enable_proxy=False)
    context.character_config = SimpleNamespace(
        conf_uid="elysia_mvp_001",
        agent_config=SimpleNamespace(conversation_agent_choice="basic_memory_agent"),
    )
    failing_store = Mock()
    failing_store.load = AsyncMock(side_effect=RuntimeError(secret))
    service = PersistentMemoryService(store=failing_store)
    context.memory_service = service
    logs: list[str] = []
    sink_id = logger.add(
        lambda message: logs.append(str(message)),
        format="{level} {message}",
        level="WARNING",
    )

    try:
        first = asyncio.run(context.get_persistent_memory_context())
        second = asyncio.run(context.get_persistent_memory_context())
    finally:
        logger.remove(sink_id)

    assert first == second == ""
    failing_store.load.assert_awaited_once()
    assert service.is_unavailable(
        MemoryScope(
            profile_id="local_default",
            character_conf_uid="elysia_mvp_001",
        )
    )
    output = "".join(logs)
    assert "RuntimeError" in output
    assert secret not in output
    assert "memory_data" not in output


def test_memory_service_initialization_supports_disabled_management_and_reuses_root(
    tmp_path: Path,
) -> None:
    context = ServiceContext(project_root=tmp_path)

    context._init_memory_service(MemoryConfig(enabled=False))
    disabled_service = context.memory_service
    assert isinstance(disabled_service, PersistentMemoryService)
    assert not (tmp_path / "memory_data").exists()

    config = MemoryConfig(enabled=True, storage_dir="memory_data")
    context._init_memory_service(config)
    service = context.memory_service
    assert isinstance(service, PersistentMemoryService)
    assert service is disabled_service
    assert service.store.storage_root == (tmp_path / "memory_data").resolve()
    assert not service.store.storage_root.exists()

    context._init_memory_service(config)
    assert context.memory_service is service


def test_memory_initialization_failure_is_non_blocking_and_desensitized(
    tmp_path: Path,
) -> None:
    context = ServiceContext(project_root=tmp_path)
    secret = "PRIVATE_STORAGE_PATH"
    logs: list[str] = []
    sink_id = logger.add(
        lambda message: logs.append(str(message)),
        format="{level} {message}",
        level="WARNING",
    )

    try:
        with patch(
            "open_llm_vtuber.service_context.PersistentMemoryStore",
            side_effect=OSError(secret),
        ):
            context._init_memory_service(MemoryConfig(enabled=True))
    finally:
        logger.remove(sink_id)

    assert context.memory_service is None
    output = "".join(logs)
    assert "OSError" in output
    assert secret not in output


def test_agent_initialization_passes_the_dynamic_memory_provider(
    tmp_path: Path,
) -> None:
    context = ServiceContext(project_root=tmp_path)
    context.system_config = SimpleNamespace(model_dump=lambda: {})
    context.character_config = SimpleNamespace(
        agent_config=None,
        avatar="",
        tts_preprocessor_config=None,
        persona_prompt="BASE PERSONA",
    )
    context.live2d_model = SimpleNamespace()
    context.construct_system_prompt = AsyncMock(return_value="BASE PERSONA")
    agent_config = SimpleNamespace(
        conversation_agent_choice="basic_memory_agent",
        agent_settings=SimpleNamespace(model_dump=lambda: {}),
        llm_configs=SimpleNamespace(model_dump=lambda: {}),
    )
    created_agent = Mock()

    with patch(
        "open_llm_vtuber.service_context.AgentFactory.create_agent",
        return_value=created_agent,
    ) as create_agent:
        asyncio.run(context.init_agent(agent_config, "BASE PERSONA"))

    assert context.agent_engine is created_agent
    provider = create_agent.call_args.kwargs["persistent_memory_context_provider"]
    assert provider.__self__ is context
    assert provider.__func__ is ServiceContext.get_persistent_memory_context


def test_load_cache_shares_the_same_memory_service(tmp_path: Path) -> None:
    shared_service = Mock(spec=PersistentMemoryService)
    basic_settings = SimpleNamespace(use_mcpp=False, mcp_enabled_servers=[])
    character_config = SimpleNamespace(
        agent_config=SimpleNamespace(
            agent_settings=SimpleNamespace(basic_memory_agent=basic_settings)
        )
    )
    session = ServiceContext(project_root=tmp_path)

    asyncio.run(
        session.load_cache(
            config=SimpleNamespace(),
            system_config=SimpleNamespace(),
            character_config=character_config,
            live2d_model=None,
            asr_engine=None,
            tts_engine=None,
            vad_engine=None,
            agent_engine=None,
            translate_engine=None,
            memory_service=shared_service,
        )
    )

    assert session.memory_service is shared_service


def test_config_switch_preserves_memory_and_live_configuration(tmp_path: Path) -> None:
    context = ServiceContext(project_root=tmp_path)
    context.system_config = SimpleNamespace(model_dump=lambda: {"enable_proxy": False})
    context.character_config = SimpleNamespace(
        conf_uid="old_character",
        conf_name="Old Character",
        live2d_model_name="mao_pro",
    )
    context.config = SimpleNamespace(
        live_config=SimpleNamespace(model_dump=lambda: {"bilibili_live": {}}),
        memory_config=SimpleNamespace(
            model_dump=lambda: {
                "enabled": True,
                "profile_id": "local_default",
                "storage_dir": "memory_data",
            }
        ),
    )
    context.live2d_model = SimpleNamespace(model_info={"name": "mao_pro"})
    context.load_from_config = AsyncMock()
    context.memory_command_controller.clear_pending = Mock()
    context.active_memory_command_turn_id = "pending-memory-turn"
    websocket = AsyncMock()
    captured: dict = {}

    def capture_config(data: dict):
        captured.update(data)
        return SimpleNamespace()

    with (
        patch(
            "open_llm_vtuber.service_context.read_yaml",
            return_value={"character_config": {"conf_uid": "new_character"}},
        ),
        patch(
            "open_llm_vtuber.service_context.validate_config",
            side_effect=capture_config,
        ),
    ):
        asyncio.run(context.handle_config_switch(websocket, "conf.yaml"))

    assert captured["live_config"] == {"bilibili_live": {}}
    assert captured["memory_config"]["enabled"] is True
    assert captured["memory_config"]["profile_id"] == "local_default"
    context.memory_command_controller.clear_pending.assert_called_once_with()
    assert context.active_memory_command_turn_id is None
