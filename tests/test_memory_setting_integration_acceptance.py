import asyncio
import json
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import yaml

from open_llm_vtuber.config_manager.utils import validate_config
from open_llm_vtuber.server import WebSocketServer
from open_llm_vtuber.service_context import ServiceContext


PROJECT_ROOT = Path(__file__).resolve().parents[1]
TEMPLATE_PATH = PROJECT_ROOT / "config_templates" / "conf.elysia.example.yaml"


class FakeWebSocket:
    def __init__(self) -> None:
        self.client = SimpleNamespace(host="127.0.0.1")
        self.sent: list[str] = []

    async def send_text(self, message: str) -> None:
        self.sent.append(message)


@dataclass
class AcceptanceRuntime:
    project_root: Path
    server: WebSocketServer
    default_context: ServiceContext

    @property
    def handler(self):
        return self.server.websocket_handler

    @property
    def coordinator(self):
        coordinator = self.server.memory_setting_coordinator
        assert coordinator is not None
        return coordinator

    async def connect(self, client_uid: str) -> tuple[ServiceContext, FakeWebSocket]:
        websocket = FakeWebSocket()
        await self.handler.handle_new_connection(websocket, client_uid)
        context = self.handler.client_contexts[client_uid]
        websocket.sent.clear()
        return context, websocket

    async def request(
        self,
        websocket: FakeWebSocket,
        client_uid: str,
        payload: dict,
    ) -> dict:
        before = len(websocket.sent)
        await self.handler._route_message(websocket, client_uid, payload)
        assert len(websocket.sent) == before + 1
        return json.loads(websocket.sent[-1])


def _write_project_config(project_root: Path, *, enabled: bool) -> None:
    raw = yaml.safe_load(TEMPLATE_PATH.read_text(encoding="utf-8"))
    raw["memory_config"]["enabled"] = enabled
    (project_root / "conf.yaml").write_text(
        yaml.safe_dump(raw, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )


def _load_config(project_root: Path):
    raw = yaml.safe_load((project_root / "conf.yaml").read_text(encoding="utf-8"))
    return validate_config(raw)


def _make_default_context(project_root: Path) -> tuple[ServiceContext, object]:
    config = _load_config(project_root)
    context = ServiceContext(project_root=project_root)
    context.config = config
    context.system_config = config.system_config
    context.character_config = config.character_config
    context.live2d_model = SimpleNamespace(model_info={"name": "acceptance-model"})
    context.asr_engine = object()
    context.tts_engine = object()
    context.vad_engine = object()
    context.agent_engine = object()
    context.translate_engine = object()
    context.mcp_server_registery = object()
    context.tool_adapter = object()
    context._init_memory_service(config.memory_config)
    assert context.memory_service is not None
    return context, config


async def _assemble_runtime(
    project_root: Path,
    *,
    rewrite_config: bool = True,
    enabled: bool = False,
) -> AcceptanceRuntime:
    if rewrite_config:
        _write_project_config(project_root, enabled=enabled)
    default_context, config = _make_default_context(project_root)
    server = WebSocketServer(config=config, default_context_cache=default_context)
    original_bytes = (project_root / "conf.yaml").read_bytes()

    await server.initialize_memory_setting_runtime()
    first_writer = server.memory_setting_writer
    first_coordinator = server.memory_setting_coordinator
    await server.initialize_memory_setting_runtime()

    assert server.memory_setting_writer is first_writer
    assert server.memory_setting_coordinator is first_coordinator
    assert default_context._memory_setting_update_port is first_coordinator
    assert server.websocket_handler._memory_setting_coordinator is first_coordinator
    assert (project_root / "conf.yaml").read_bytes() == original_bytes
    return AcceptanceRuntime(project_root, server, default_context)


def _setting_payload(*, expected_enabled: bool, enabled: bool) -> dict:
    return {
        "type": "memory-setting-update",
        "protocol_version": 1,
        "request_id": str(uuid4()),
        "expected_enabled": expected_enabled,
        "enabled": enabled,
    }


def _state_payload() -> dict:
    return {
        "type": "memory-state-request",
        "protocol_version": 1,
        "request_id": str(uuid4()),
    }


def _persisted_enabled(project_root: Path) -> bool:
    raw = yaml.safe_load((project_root / "conf.yaml").read_text(encoding="utf-8"))
    return raw["memory_config"]["enabled"]


def _temporary_config_files(project_root: Path) -> list[Path]:
    return list(project_root.glob(".conf.yaml.*.tmp"))


def test_websocket_toggle_converges_disk_runtime_and_two_sessions_without_rebuild(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        runtime = await _assemble_runtime(tmp_path)
        context_a, websocket_a = await runtime.connect("client-a")
        context_b, websocket_b = await runtime.connect("client-b")
        contexts = [runtime.default_context, context_a, context_b]
        stable_objects = {
            id(context): (
                context.memory_service,
                context.live2d_model,
                context.asr_engine,
                context.tts_engine,
                context.vad_engine,
                context.agent_engine,
                context.translate_engine,
                context.memory_management_controller,
            )
            for context in contexts
        }

        enabled = await runtime.request(
            websocket_a,
            "client-a",
            _setting_payload(expected_enabled=False, enabled=True),
        )

        assert enabled["type"] == "memory-management-result"
        assert enabled["operation"] == "set_enabled"
        assert enabled["status"] == "success"
        assert enabled["changed"] is True
        assert enabled["enabled"] is True
        assert enabled["protocol_version"] == 1
        assert all(context.config.memory_config.enabled is True for context in contexts)
        assert runtime.coordinator.runtime_enabled is True
        assert _persisted_enabled(tmp_path) is True

        peer_state = await runtime.request(websocket_b, "client-b", _state_payload())
        assert peer_state["type"] == "memory-state"
        assert peer_state["enabled"] is True

        disabled = await runtime.request(
            websocket_b,
            "client-b",
            _setting_payload(expected_enabled=True, enabled=False),
        )

        assert disabled["status"] == "success"
        assert disabled["changed"] is True
        assert disabled["enabled"] is False
        assert all(
            context.config.memory_config.enabled is False for context in contexts
        )
        assert runtime.coordinator.runtime_enabled is False
        assert _persisted_enabled(tmp_path) is False
        assert _temporary_config_files(tmp_path) == []
        for context in contexts:
            assert stable_objects[id(context)] == (
                context.memory_service,
                context.live2d_model,
                context.asr_engine,
                context.tts_engine,
                context.vad_engine,
                context.agent_engine,
                context.translate_engine,
                context.memory_management_controller,
            )

    asyncio.run(scenario())


def test_new_session_converges_without_write_and_disconnected_session_stops_syncing(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        runtime = await _assemble_runtime(tmp_path)
        context_a, websocket_a = await runtime.connect("client-a")
        context_b, _ = await runtime.connect("client-b")
        await runtime.request(
            websocket_a,
            "client-a",
            _setting_payload(expected_enabled=False, enabled=True),
        )
        before_connect = (tmp_path / "conf.yaml").read_bytes()

        context_c, websocket_c = await runtime.connect("client-c")

        assert context_c.config.memory_config.enabled is True
        assert context_c._memory_setting_update_port is runtime.coordinator
        assert (tmp_path / "conf.yaml").read_bytes() == before_connect
        assert context_c.memory_service is runtime.default_context.memory_service

        await runtime.handler.handle_disconnect("client-b")
        await runtime.request(
            websocket_c,
            "client-c",
            _setting_payload(expected_enabled=True, enabled=False),
        )

        assert runtime.default_context.config.memory_config.enabled is False
        assert context_a.config.memory_config.enabled is False
        assert context_c.config.memory_config.enabled is False
        assert context_b.config.memory_config.enabled is True
        assert "client-b" not in runtime.handler.client_contexts
        assert "client-b" not in runtime.handler.client_connections
        assert _persisted_enabled(tmp_path) is False

    asyncio.run(scenario())


def test_concurrent_clients_have_one_cas_winner_and_one_conflict(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        runtime = await _assemble_runtime(tmp_path)
        context_a, websocket_a = await runtime.connect("client-a")
        context_b, websocket_b = await runtime.connect("client-b")

        responses = await asyncio.gather(
            runtime.request(
                websocket_a,
                "client-a",
                _setting_payload(expected_enabled=False, enabled=True),
            ),
            runtime.request(
                websocket_b,
                "client-b",
                _setting_payload(expected_enabled=False, enabled=True),
            ),
        )

        assert [response["status"] for response in responses].count("success") == 1
        assert [response["status"] for response in responses].count("rejected") == 1
        assert [response["changed"] for response in responses].count(True) == 1
        rejected = next(
            response for response in responses if response["status"] == "rejected"
        )
        assert rejected["reason_code"] == "setting_conflict"
        assert rejected["enabled"] is True
        assert runtime.coordinator.runtime_enabled is True
        assert runtime.default_context.config.memory_config.enabled is True
        assert context_a.config.memory_config.enabled is True
        assert context_b.config.memory_config.enabled is True
        assert _persisted_enabled(tmp_path) is True
        assert _temporary_config_files(tmp_path) == []

    asyncio.run(scenario())


def test_external_disk_conflict_self_heals_all_registered_contexts(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        runtime = await _assemble_runtime(tmp_path, enabled=True)
        context_a, websocket_a = await runtime.connect("client-a")
        context_b, _ = await runtime.connect("client-b")
        raw = yaml.safe_load((tmp_path / "conf.yaml").read_text(encoding="utf-8"))
        raw["memory_config"]["enabled"] = False
        (tmp_path / "conf.yaml").write_text(
            yaml.safe_dump(raw, allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )

        response = await runtime.request(
            websocket_a,
            "client-a",
            _setting_payload(expected_enabled=True, enabled=False),
        )

        assert response["status"] == "rejected"
        assert response["changed"] is False
        assert response["reason_code"] == "setting_conflict"
        assert response["enabled"] is False
        assert runtime.coordinator.runtime_enabled is False
        assert runtime.default_context.config.memory_config.enabled is False
        assert context_a.config.memory_config.enabled is False
        assert context_b.config.memory_config.enabled is False
        assert _persisted_enabled(tmp_path) is False

    asyncio.run(scenario())


def test_fresh_server_restores_persisted_state_after_restart(tmp_path: Path) -> None:
    async def scenario() -> None:
        first = await _assemble_runtime(tmp_path)
        _, websocket_a = await first.connect("client-a")
        await first.request(
            websocket_a,
            "client-a",
            _setting_payload(expected_enabled=False, enabled=True),
        )
        first_writer = first.server.memory_setting_writer
        first_coordinator = first.coordinator

        second = await _assemble_runtime(tmp_path, rewrite_config=False)
        context_b, websocket_b = await second.connect("client-b")
        state = await second.request(websocket_b, "client-b", _state_payload())

        assert second.server.memory_setting_writer is not first_writer
        assert second.coordinator is not first_coordinator
        assert second.coordinator.runtime_enabled is True
        assert second.default_context.config.memory_config.enabled is True
        assert context_b.config.memory_config.enabled is True
        assert state["type"] == "memory-state"
        assert state["enabled"] is True
        assert _persisted_enabled(tmp_path) is True

    asyncio.run(scenario())
