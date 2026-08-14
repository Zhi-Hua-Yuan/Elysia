import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from open_llm_vtuber.routes import init_client_ws_route
from open_llm_vtuber.server import WebSocketServer
from open_llm_vtuber.websocket_handler import WebSocketHandler


class FakeDefaultContext:
    def __init__(self, project_root, *, enabled: bool | None = True) -> None:
        self.project_root = project_root
        self.config = (
            None
            if enabled is None
            else SimpleNamespace(
                memory_config=SimpleNamespace(enabled=enabled),
            )
        )
        self.bound_port = None
        self.applied_states: list[bool] = []
        self.transient_clear_count = 0

    def bind_memory_setting_update_port(self, port) -> None:
        if self.bound_port is port:
            return
        if self.bound_port is not None:
            raise RuntimeError("memory setting update port is already bound")
        self.bound_port = port

    def apply_memory_enabled_state(self, *, enabled: bool) -> None:
        self.config.memory_config.enabled = enabled
        self.applied_states.append(enabled)

    def clear_memory_setting_transients(self) -> None:
        self.transient_clear_count += 1


class FakeWebSocket:
    def __init__(self) -> None:
        self.accept = AsyncMock()


def _server_config():
    return SimpleNamespace(
        system_config=SimpleNamespace(enable_proxy=False),
    )


def test_client_route_uses_the_injected_handler() -> None:
    handler = SimpleNamespace(
        handle_new_connection=AsyncMock(),
        handle_websocket_communication=AsyncMock(),
        handle_disconnect=AsyncMock(),
    )
    router = init_client_ws_route(websocket_handler=handler)
    websocket = FakeWebSocket()

    asyncio.run(router.routes[0].endpoint(websocket))

    websocket.accept.assert_awaited_once()
    handler.handle_new_connection.assert_awaited_once()
    handler.handle_websocket_communication.assert_awaited_once()
    first_client_uid = handler.handle_new_connection.await_args.args[1]
    assert handler.handle_new_connection.await_args.args[0] is websocket
    assert handler.handle_websocket_communication.await_args.args == (
        websocket,
        first_client_uid,
    )
    handler.handle_disconnect.assert_not_awaited()


def test_handler_coordinator_binding_is_one_time_and_identity_idempotent() -> None:
    handler = WebSocketHandler(default_context_cache=None)
    coordinator = object()

    handler.bind_memory_setting_coordinator(coordinator)
    handler.bind_memory_setting_coordinator(coordinator)

    assert handler._memory_setting_coordinator is coordinator
    with pytest.raises(RuntimeError, match="already bound"):
        handler.bind_memory_setting_coordinator(object())
    with pytest.raises(ValueError, match="must not be None"):
        WebSocketHandler(default_context_cache=None).bind_memory_setting_coordinator(
            None
        )


def test_server_owns_handler_and_registers_startup_composition(tmp_path) -> None:
    context = FakeDefaultContext(tmp_path)
    server = WebSocketServer(
        config=_server_config(),
        default_context_cache=context,
    )

    assert server.websocket_handler.default_context_cache is context
    assert any(
        getattr(callback, "__self__", None) is server
        and getattr(callback, "__func__", None)
        is WebSocketServer.initialize_memory_setting_runtime
        for callback in server.app.router.on_startup
    )


def test_runtime_composition_is_singleton_idempotent_and_does_not_write(tmp_path) -> None:
    config_path = tmp_path / "conf.yaml"
    original_content = "memory_config:\n  enabled: true\n"
    config_path.write_text(original_content, encoding="utf-8")
    context = FakeDefaultContext(tmp_path, enabled=True)
    server = WebSocketServer(
        config=_server_config(),
        default_context_cache=context,
    )

    async def scenario() -> None:
        await server.initialize_memory_setting_runtime()
        first_writer = server.memory_setting_writer
        first_coordinator = server.memory_setting_coordinator
        await server.initialize_memory_setting_runtime()

        assert server.memory_setting_writer is first_writer
        assert server.memory_setting_coordinator is first_coordinator

    asyncio.run(scenario())

    coordinator = server.memory_setting_coordinator
    assert coordinator is not None
    assert coordinator.runtime_enabled is True
    assert coordinator._writer is server.memory_setting_writer
    assert context.bound_port is coordinator
    assert context.applied_states == [True]
    assert context.transient_clear_count == 1
    assert server.websocket_handler._memory_setting_coordinator is coordinator
    assert config_path.read_text(encoding="utf-8") == original_content


def test_runtime_composition_requires_a_loaded_default_context(tmp_path) -> None:
    context = FakeDefaultContext(tmp_path, enabled=None)
    server = WebSocketServer(
        config=_server_config(),
        default_context_cache=context,
    )

    with pytest.raises(RuntimeError, match="must be loaded"):
        asyncio.run(server.initialize_memory_setting_runtime())

    assert server.memory_setting_writer is None
    assert server.memory_setting_coordinator is None
    assert server.websocket_handler._memory_setting_coordinator is None
