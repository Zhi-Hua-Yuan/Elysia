import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

import open_llm_vtuber.websocket_handler as websocket_handler_module
from open_llm_vtuber.service_context import ServiceContext
from open_llm_vtuber.websocket_handler import WebSocketHandler


class CopyableValue:
    def model_copy(self, *, deep: bool):
        assert deep is True
        return self


class FakeDefaultContext:
    def __init__(self) -> None:
        self.config = CopyableValue()
        self.system_config = CopyableValue()
        self.character_config = CopyableValue()
        self.live2d_model = object()
        self.asr_engine = object()
        self.tts_engine = object()
        self.vad_engine = object()
        self.agent_engine = object()
        self.translate_engine = object()
        self.mcp_server_registery = object()
        self.tool_adapter = object()
        self.memory_service = object()


class RecordingSessionContext:
    instances: list["RecordingSessionContext"] = []
    events: list[str] = []

    def __init__(self) -> None:
        self.bound_port = None
        self.load_kwargs = None
        self.close_count = 0
        type(self).instances.append(self)

    async def load_cache(self, **kwargs) -> None:
        self.load_kwargs = kwargs
        type(self).events.append("load")

    def bind_memory_setting_update_port(self, port) -> None:
        self.bound_port = port
        type(self).events.append("bind")

    async def close(self) -> None:
        self.close_count += 1
        type(self).events.append("close")


class RecordingCoordinator:
    def __init__(self, *, register_error: Exception | None = None) -> None:
        self.register_error = register_error
        self.registered: list[RecordingSessionContext] = []
        self.unregistered: list[RecordingSessionContext] = []

    async def register_target(self, target) -> None:
        RecordingSessionContext.events.append("register")
        if self.register_error is not None:
            raise self.register_error
        self.registered.append(target)

    async def unregister_target(self, target) -> None:
        RecordingSessionContext.events.append("unregister")
        self.unregistered.append(target)


class FakeWebSocket:
    def __init__(self) -> None:
        self.send_text = AsyncMock()


@pytest.fixture(autouse=True)
def reset_recording_context() -> None:
    RecordingSessionContext.instances = []
    RecordingSessionContext.events = []


def _handler(monkeypatch, coordinator=None) -> tuple[WebSocketHandler, object]:
    monkeypatch.setattr(
        websocket_handler_module,
        "ServiceContext",
        RecordingSessionContext,
    )
    handler = WebSocketHandler(FakeDefaultContext())
    coordinator = coordinator or RecordingCoordinator()
    handler.bind_memory_setting_coordinator(coordinator)
    return handler, coordinator


def test_session_is_bound_and_registered_before_handler_storage(monkeypatch) -> None:
    handler, coordinator = _handler(monkeypatch)
    websocket = FakeWebSocket()

    async def store_client_data(websocket_arg, client_uid, context) -> None:
        assert websocket_arg is websocket
        assert client_uid == "client-a"
        assert context in coordinator.registered
        RecordingSessionContext.events.append("store")
        handler.client_contexts[client_uid] = context

    async def send_initial_messages(websocket_arg, client_uid, context) -> None:
        assert websocket_arg is websocket
        assert client_uid == "client-a"
        assert handler.client_contexts[client_uid] is context
        RecordingSessionContext.events.append("initial")

    monkeypatch.setattr(handler, "_store_client_data", store_client_data)
    monkeypatch.setattr(handler, "_send_initial_messages", send_initial_messages)

    asyncio.run(handler.handle_new_connection(websocket, "client-a"))

    context = RecordingSessionContext.instances[0]
    assert context.bound_port is coordinator
    assert context.load_kwargs["client_uid"] == "client-a"
    assert context.load_kwargs["send_text"] is websocket.send_text
    assert RecordingSessionContext.events == [
        "load",
        "bind",
        "register",
        "store",
        "initial",
    ]


def test_unbound_handler_rejects_session_before_context_creation(monkeypatch) -> None:
    monkeypatch.setattr(
        websocket_handler_module,
        "ServiceContext",
        RecordingSessionContext,
    )
    handler = WebSocketHandler(FakeDefaultContext())

    with pytest.raises(RuntimeError, match="runtime is not initialized"):
        asyncio.run(handler._init_service_context(AsyncMock(), "client-a"))

    assert RecordingSessionContext.instances == []


def test_registration_failure_unregisters_and_closes_partial_context(
    monkeypatch,
) -> None:
    failure = RuntimeError("registration failed")
    coordinator = RecordingCoordinator(register_error=failure)
    handler, _ = _handler(monkeypatch, coordinator)

    with pytest.raises(RuntimeError, match="registration failed"):
        asyncio.run(handler._init_service_context(AsyncMock(), "client-a"))

    context = RecordingSessionContext.instances[0]
    assert coordinator.registered == []
    assert coordinator.unregistered == [context]
    assert context.close_count == 1
    assert handler.client_contexts == {}
    assert RecordingSessionContext.events == [
        "load",
        "bind",
        "register",
        "unregister",
        "close",
    ]


def test_cancelled_registration_unregisters_and_closes_partial_context(
    monkeypatch,
) -> None:
    coordinator = RecordingCoordinator(register_error=asyncio.CancelledError())
    handler, _ = _handler(monkeypatch, coordinator)

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(handler._init_service_context(AsyncMock(), "client-a"))

    context = RecordingSessionContext.instances[0]
    assert coordinator.unregistered == [context]
    assert context.close_count == 1


def test_initial_message_failure_releases_stored_session(monkeypatch) -> None:
    handler, coordinator = _handler(monkeypatch)
    websocket = FakeWebSocket()

    async def store_client_data(websocket_arg, client_uid, context) -> None:
        handler.client_connections[client_uid] = websocket_arg
        handler.client_contexts[client_uid] = context

    async def fail_initial_messages(*_args) -> None:
        raise RuntimeError("initial message failed")

    monkeypatch.setattr(handler, "_store_client_data", store_client_data)
    monkeypatch.setattr(handler, "_send_initial_messages", fail_initial_messages)
    cleanup_client = Mock()
    monkeypatch.setattr(
        websocket_handler_module.message_handler,
        "cleanup_client",
        cleanup_client,
    )

    with pytest.raises(RuntimeError, match="initial message failed"):
        asyncio.run(handler.handle_new_connection(websocket, "client-a"))

    context = RecordingSessionContext.instances[0]
    assert coordinator.unregistered == [context]
    assert context.close_count == 1
    assert handler.client_connections == {}
    assert handler.client_contexts == {}
    cleanup_client.assert_called_once_with("client-a")


def test_disconnect_unregisters_and_closes_session_exactly_once(monkeypatch) -> None:
    handler, coordinator = _handler(monkeypatch)
    context = RecordingSessionContext()
    task = Mock()
    task.done.return_value = False
    handler.client_connections["client-a"] = object()
    handler.client_contexts["client-a"] = context
    handler.received_data_buffers["client-a"] = object()
    handler.conversation_started_at["client-a"] = (0.0, "", "")
    handler.current_conversation_tasks["client-a"] = task
    handler.chat_group_manager.client_group_map["client-a"] = ""
    handler.chat_group_manager.get_client_group = Mock(return_value=None)
    disconnect_group = AsyncMock()
    cleanup_client = Mock()
    monkeypatch.setattr(
        websocket_handler_module,
        "handle_client_disconnect",
        disconnect_group,
    )
    monkeypatch.setattr(
        websocket_handler_module.message_handler,
        "cleanup_client",
        cleanup_client,
    )

    async def scenario() -> None:
        await handler.handle_disconnect("client-a")
        await handler.handle_disconnect("client-a")

    asyncio.run(scenario())

    assert coordinator.unregistered == [context]
    assert context.close_count == 1
    assert handler.client_connections == {}
    assert handler.client_contexts == {}
    assert handler.received_data_buffers == {}
    assert handler.conversation_started_at == {}
    assert handler.current_conversation_tasks == {}
    assert handler.chat_group_manager.client_group_map == {}
    task.cancel.assert_called_once_with()
    assert disconnect_group.await_count == 2
    assert cleanup_client.call_count == 2


def test_failed_connection_cleanup_is_identity_idempotent(monkeypatch) -> None:
    handler, coordinator = _handler(monkeypatch)
    context = RecordingSessionContext()
    handler.client_contexts["client-a"] = context
    cleanup_client = Mock()
    monkeypatch.setattr(
        websocket_handler_module.message_handler,
        "cleanup_client",
        cleanup_client,
    )

    async def scenario() -> None:
        await handler._cleanup_failed_connection("client-a")
        await handler._cleanup_failed_connection("client-a")

    asyncio.run(scenario())

    assert coordinator.unregistered == [context]
    assert context.close_count == 1
    assert cleanup_client.call_count == 2


def test_closing_session_context_does_not_close_shared_agent_engine() -> None:
    context = ServiceContext()
    shared_agent = SimpleNamespace(close=AsyncMock())
    character_config = SimpleNamespace(
        agent_config=SimpleNamespace(
            agent_settings=SimpleNamespace(
                basic_memory_agent=SimpleNamespace(
                    use_mcpp=False,
                    mcp_enabled_servers=[],
                )
            )
        )
    )

    async def scenario() -> None:
        await context.load_cache(
            config=object(),
            system_config=object(),
            character_config=character_config,
            live2d_model=object(),
            asr_engine=object(),
            tts_engine=object(),
            vad_engine=object(),
            agent_engine=shared_agent,
            translate_engine=None,
        )
        await context.close()

    asyncio.run(scenario())

    shared_agent.close.assert_not_awaited()
    assert context.agent_engine is None


def test_closing_owner_context_closes_owned_agent_engine_once() -> None:
    context = ServiceContext()
    owned_agent = SimpleNamespace(close=AsyncMock())
    context.agent_engine = owned_agent
    context._owns_agent_engine = True

    async def scenario() -> None:
        await context.close()
        await context.close()

    asyncio.run(scenario())

    owned_agent.close.assert_awaited_once_with()
    assert context.agent_engine is None
