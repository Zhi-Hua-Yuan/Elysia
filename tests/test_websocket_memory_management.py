import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import UUID, uuid4

from loguru import logger

from open_llm_vtuber.config_manager import MemoryConfig
from open_llm_vtuber.memory import (
    MEMORY_MANAGEMENT_REQUEST_TYPES,
    MemoryManagementCapabilities,
    MemoryManagementReasonCode,
    MemoryManagementResultResponse,
    MemoryManagementStatus,
    MemoryStateResponse,
)
from open_llm_vtuber.service_context import ServiceContext
from open_llm_vtuber.websocket_handler import MessageType, WebSocketHandler


REQUEST_ID = "8ec55466-eec1-4bef-8133-cc883d03965a"


class FakeWebSocket:
    def __init__(self, host: str | None = "127.0.0.1") -> None:
        self.client = SimpleNamespace(host=host) if host is not None else None
        self.sent: list[str] = []

    async def send_text(self, message: str) -> None:
        self.sent.append(message)


def _state_payload() -> dict:
    return {
        "type": "memory-state-request",
        "protocol_version": 1,
        "request_id": REQUEST_ID,
    }


def _state_response() -> MemoryStateResponse:
    return MemoryStateResponse(
        protocol_version=1,
        request_id=REQUEST_ID,
        enabled=True,
        available=True,
        item_count=0,
        max_items=24,
        revision=0,
        capabilities=MemoryManagementCapabilities(
            list=True,
            create=True,
            update=True,
            delete=True,
            clear=True,
            set_enabled=True,
        ),
    )


def _handler_with_context(response=None) -> tuple[WebSocketHandler, SimpleNamespace]:
    handler = WebSocketHandler(default_context_cache=None)
    context = SimpleNamespace(
        handle_memory_management_request=AsyncMock(
            return_value=response or _state_response()
        )
    )
    handler.client_contexts["client-test"] = context
    return handler, context


def _capture_logs(callback) -> str:
    messages: list[str] = []
    sink_id = logger.add(
        lambda message: messages.append(str(message)),
        format="{level} {message}",
        level="DEBUG",
    )
    try:
        callback()
    finally:
        logger.remove(sink_id)
    return "".join(messages)


def test_all_frozen_management_types_share_one_handler() -> None:
    handler = WebSocketHandler(default_context_cache=None)

    assert set(MessageType.MEMORY_MANAGEMENT.value) == set(
        MEMORY_MANAGEMENT_REQUEST_TYPES
    )
    for message_type in MEMORY_MANAGEMENT_REQUEST_TYPES:
        assert (
            handler._message_handlers[message_type]
            == handler._handle_memory_management_request
        )


def test_valid_local_request_is_dispatched_once_and_serialized_once() -> None:
    handler, context = _handler_with_context()
    websocket = FakeWebSocket()

    asyncio.run(handler._route_message(websocket, "client-test", _state_payload()))

    context.handle_memory_management_request.assert_awaited_once()
    kwargs = context.handle_memory_management_request.await_args.kwargs
    assert kwargs == {"is_local_connection": True, "group_active": False}
    assert context.handle_memory_management_request.await_args.args[
        0
    ].request_id == UUID(REQUEST_ID)
    assert len(websocket.sent) == 1
    response = json.loads(websocket.sent[0])
    assert response["type"] == "memory-state"
    assert response["request_id"] == REQUEST_ID
    assert handler.current_conversation_tasks == {}


def test_remote_peer_is_derived_from_websocket_and_passed_to_controller() -> None:
    result = MemoryManagementResultResponse(
        protocol_version=1,
        request_id=REQUEST_ID,
        operation="state",
        status=MemoryManagementStatus.REJECTED,
        reason_code=MemoryManagementReasonCode.LOCAL_ACCESS_REQUIRED,
        changed=False,
    )
    handler, context = _handler_with_context(result)
    websocket = FakeWebSocket("192.168.1.20")

    asyncio.run(
        handler._handle_memory_management_request(
            websocket, "client-test", _state_payload()
        )
    )

    assert (
        context.handle_memory_management_request.await_args.kwargs[
            "is_local_connection"
        ]
        is False
    )
    assert json.loads(websocket.sent[0])["reason_code"] == "local_access_required"


def test_group_active_is_derived_from_server_group_membership() -> None:
    result = MemoryManagementResultResponse(
        protocol_version=1,
        request_id=REQUEST_ID,
        operation="state",
        status=MemoryManagementStatus.REJECTED,
        reason_code=MemoryManagementReasonCode.GROUP_NOT_SUPPORTED,
        changed=False,
    )
    handler, context = _handler_with_context(result)
    handler.chat_group_manager.get_client_group = Mock(
        return_value=SimpleNamespace(members={"client-test", "client-other"})
    )
    websocket = FakeWebSocket()

    asyncio.run(
        handler._handle_memory_management_request(
            websocket, "client-test", _state_payload()
        )
    )

    assert (
        context.handle_memory_management_request.await_args.kwargs["group_active"]
        is True
    )
    assert json.loads(websocket.sent[0])["reason_code"] == "group_not_supported"


def test_invalid_payload_returns_one_content_free_terminal_response() -> None:
    handler, context = _handler_with_context()
    websocket = FakeWebSocket()
    private_value = "不应出现在日志或响应里的私人记忆"
    payload = {
        "type": "memory-upsert-request",
        "protocol_version": 1,
        "request_id": REQUEST_ID,
        "category": "preference",
        "value": private_value,
        "expected_revision": 0,
        "unexpected": "field",
    }

    logs = _capture_logs(
        lambda: asyncio.run(
            handler._handle_memory_management_request(websocket, "client-test", payload)
        )
    )

    context.handle_memory_management_request.assert_not_awaited()
    assert len(websocket.sent) == 1
    serialized = websocket.sent[0]
    assert private_value not in serialized
    assert private_value not in logs
    assert "unexpected" not in logs
    response = json.loads(serialized)
    assert response["operation"] == "create"
    assert response["reason_code"] == "invalid_request"


def test_unsupported_protocol_version_is_a_frozen_rejection() -> None:
    handler, context = _handler_with_context()
    websocket = FakeWebSocket()
    payload = {**_state_payload(), "protocol_version": 2}

    asyncio.run(
        handler._handle_memory_management_request(websocket, "client-test", payload)
    )

    context.handle_memory_management_request.assert_not_awaited()
    response = json.loads(websocket.sent[0])
    assert response["status"] == "rejected"
    assert response["reason_code"] == "unsupported_protocol_version"


def test_missing_context_returns_internal_error_without_generic_error() -> None:
    handler = WebSocketHandler(default_context_cache=None)
    websocket = FakeWebSocket()

    asyncio.run(
        handler._handle_memory_management_request(
            websocket, "missing-client", _state_payload()
        )
    )

    assert len(websocket.sent) == 1
    response = json.loads(websocket.sent[0])
    assert response["type"] == "memory-management-result"
    assert response["status"] == "failed"
    assert response["reason_code"] == "internal_error"


def test_controller_exception_is_redacted_and_sent_as_internal_error() -> None:
    handler, context = _handler_with_context()
    context.handle_memory_management_request.side_effect = RuntimeError(
        "private value must not leak"
    )
    websocket = FakeWebSocket()

    logs = _capture_logs(
        lambda: asyncio.run(
            handler._handle_memory_management_request(
                websocket, "client-test", _state_payload()
            )
        )
    )

    assert "private value must not leak" not in logs
    assert "private value must not leak" not in websocket.sent[0]
    response = json.loads(websocket.sent[0])
    assert response["reason_code"] == "internal_error"


def test_response_send_failure_is_contained_without_second_send() -> None:
    handler, _ = _handler_with_context()
    websocket = FakeWebSocket()
    websocket.send_text = AsyncMock(side_effect=RuntimeError("socket closed"))

    asyncio.run(
        handler._handle_memory_management_request(
            websocket, "client-test", _state_payload()
        )
    )

    websocket.send_text.assert_awaited_once()


def test_local_websocket_round_trip_uses_real_context_controller_and_store(
    tmp_path,
) -> None:
    context = ServiceContext(project_root=tmp_path)
    memory_config = MemoryConfig(
        enabled=True,
        profile_id="local_default",
        storage_dir="memory_data",
        max_items=24,
        max_item_chars=160,
    )
    context.config = SimpleNamespace(memory_config=memory_config)
    context.system_config = SimpleNamespace(enable_proxy=False)
    context.character_config = SimpleNamespace(
        conf_uid="elysia_mvp_001",
        agent_config=SimpleNamespace(conversation_agent_choice="basic_memory_agent"),
    )
    context._init_memory_service(memory_config)

    handler = WebSocketHandler(default_context_cache=None)
    handler.client_contexts["client-test"] = context
    websocket = FakeWebSocket()
    state_id = str(uuid4())
    create_id = str(uuid4())
    list_id = str(uuid4())

    async def scenario() -> None:
        await handler._route_message(
            websocket,
            "client-test",
            {
                "type": "memory-state-request",
                "protocol_version": 1,
                "request_id": state_id,
            },
        )
        await handler._route_message(
            websocket,
            "client-test",
            {
                "type": "memory-upsert-request",
                "protocol_version": 1,
                "request_id": create_id,
                "category": "preference",
                "value": "喜欢粉色花朵",
                "expected_revision": 0,
            },
        )
        await handler._route_message(
            websocket,
            "client-test",
            {
                "type": "memory-list-request",
                "protocol_version": 1,
                "request_id": list_id,
            },
        )

    asyncio.run(scenario())

    state, created, listed = [json.loads(message) for message in websocket.sent]
    assert state["type"] == "memory-state"
    assert state["revision"] == 0
    assert created["type"] == "memory-management-result"
    assert created["operation"] == "create"
    assert created["status"] == "success"
    assert created["revision"] == 1
    assert listed["type"] == "memory-list"
    assert listed["revision"] == 1
    assert listed["items"][0]["value"] == "喜欢粉色花朵"
    assert listed["items"][0]["source"] == "manual_ui"
    assert context.history_uid == ""
    assert handler.current_conversation_tasks == {}
