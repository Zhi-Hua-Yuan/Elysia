"""Run one memory-setting acceptance action in a fresh Python interpreter."""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass
import json
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

from loguru import logger
import yaml

from open_llm_vtuber.config_manager.utils import validate_config
from open_llm_vtuber.server import WebSocketServer
from open_llm_vtuber.service_context import ServiceContext


class FakeWebSocket:
    """Minimal loopback WebSocket used by the real management router."""

    def __init__(self) -> None:
        self.client = SimpleNamespace(host="127.0.0.1")
        self.sent: list[str] = []

    async def send_text(self, message: str) -> None:
        self.sent.append(message)


@dataclass
class Runtime:
    server: WebSocketServer
    context: ServiceContext
    websocket: FakeWebSocket
    client_uid: str

    @property
    def session_context(self) -> ServiceContext:
        return self.server.websocket_handler.client_contexts[self.client_uid]

    async def request(self, payload: dict) -> dict:
        before = len(self.websocket.sent)
        await self.server.websocket_handler._route_message(
            self.websocket,
            self.client_uid,
            payload,
        )
        if len(self.websocket.sent) != before + 1:
            raise RuntimeError("management request did not produce one response")
        return json.loads(self.websocket.sent[-1])

    async def close(self) -> None:
        await self.server.websocket_handler.handle_disconnect(self.client_uid)


def _load_config(project_root: Path):
    raw = yaml.safe_load((project_root / "conf.yaml").read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise TypeError("configuration root must be a mapping")
    return validate_config(raw)


def _make_context(project_root: Path) -> tuple[ServiceContext, object]:
    config = _load_config(project_root)
    context = ServiceContext(project_root=project_root)
    context.config = config
    context.system_config = config.system_config
    context.character_config = config.character_config
    context.live2d_model = SimpleNamespace(model_info={"name": "restart-test-model"})
    context.asr_engine = object()
    context.tts_engine = object()
    context.vad_engine = object()
    context.agent_engine = object()
    context.translate_engine = object()
    context.mcp_server_registery = object()
    context.tool_adapter = object()
    context._init_memory_service(config.memory_config)
    if context.memory_service is None:
        raise RuntimeError("memory service was not initialized")
    return context, config


async def _assemble(project_root: Path) -> Runtime:
    context, config = _make_context(project_root)
    server = WebSocketServer(config=config, default_context_cache=context)
    await server.initialize_memory_setting_runtime()
    websocket = FakeWebSocket()
    client_uid = "restart-worker"
    await server.websocket_handler.handle_new_connection(websocket, client_uid)
    websocket.sent.clear()
    return Runtime(server, context, websocket, client_uid)


def _request_payload(message_type: str, **fields: object) -> dict:
    return {
        "type": message_type,
        "protocol_version": 1,
        "request_id": str(uuid4()),
        **fields,
    }


async def _toggle(runtime: Runtime, args: argparse.Namespace) -> dict:
    response = await runtime.request(
        _request_payload(
            "memory-setting-update",
            expected_enabled=args.expected_enabled,
            enabled=args.enabled,
        )
    )
    coordinator = runtime.server.memory_setting_coordinator
    if coordinator is None:
        raise RuntimeError("memory setting coordinator is unavailable")
    return {
        "ok": response.get("status") == "success",
        "status": response.get("status"),
        "changed": response.get("changed"),
        "enabled": response.get("enabled"),
        "runtime_enabled": coordinator.runtime_enabled,
        "default_context_enabled": runtime.context.config.memory_config.enabled,
        "session_enabled": runtime.session_context.config.memory_config.enabled,
    }


async def _seed(runtime: Runtime, args: argparse.Namespace) -> dict:
    state = await runtime.request(_request_payload("memory-state-request"))
    response = await runtime.request(
        _request_payload(
            "memory-upsert-request",
            category="preference",
            value=args.marker,
            expected_revision=state["revision"],
        )
    )
    return {
        "ok": response.get("status") == "success",
        "status": response.get("status"),
        "changed": response.get("changed"),
        "revision": response.get("revision"),
        "item_count": response.get("item_count"),
    }


async def _inspect(runtime: Runtime, args: argparse.Namespace) -> dict:
    state = await runtime.request(_request_payload("memory-state-request"))
    listed = await runtime.request(_request_payload("memory-list-request"))
    context = await runtime.session_context.get_persistent_memory_context(
        force_reload=True
    )
    items = listed.get("items", [])
    result = {
        "ok": True,
        "enabled": state["enabled"],
        "available": state["available"],
        "revision": state["revision"],
        "item_count": state["item_count"],
        "can_create": state["capabilities"]["create"],
        "can_delete": state["capabilities"]["delete"],
        "can_clear": state["capabilities"]["clear"],
        "list_item_count": listed.get("item_count"),
        "context_empty": context == "",
    }
    if args.marker is not None:
        result["list_contains_marker"] = any(
            item.get("value") == args.marker for item in items
        )
        result["context_contains_marker"] = args.marker in context
    if state["enabled"] is False:
        create = await runtime.request(
            _request_payload(
                "memory-upsert-request",
                category="preference",
                value="DISABLED_CREATE_MUST_NOT_PERSIST",
                expected_revision=state["revision"],
            )
        )
        result["disabled_create_status"] = create.get("status")
        result["disabled_create_reason"] = create.get("reason_code")
    return result


async def _run(args: argparse.Namespace) -> dict:
    runtime = await _assemble(args.project_root)
    try:
        if args.command == "toggle":
            return await _toggle(runtime, args)
        if args.command == "seed":
            return await _seed(runtime, args)
        return await _inspect(runtime, args)
    finally:
        await runtime.close()


def _parse_boolean(value: str) -> bool:
    if value == "true":
        return True
    if value == "false":
        return False
    raise argparse.ArgumentTypeError("expected true or false")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("toggle", "seed", "inspect"))
    parser.add_argument("project_root", type=Path)
    parser.add_argument("--expected-enabled", type=_parse_boolean)
    parser.add_argument("--enabled", type=_parse_boolean)
    parser.add_argument("--marker")
    args = parser.parse_args()
    if args.command == "toggle" and (
        args.expected_enabled is None or args.enabled is None
    ):
        parser.error("toggle requires expected and target states")
    if args.command == "seed" and args.marker is None:
        parser.error("seed requires a marker")
    return args


def main() -> int:
    logger.remove()
    args = _parse_args()
    try:
        result = asyncio.run(_run(args))
    except Exception as error:
        print(json.dumps({"ok": False, "error_type": type(error).__name__}))
        return 2
    print(json.dumps(result, separators=(",", ":")))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
