import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from loguru import logger

from open_llm_vtuber.config_manager import MemoryConfig
from open_llm_vtuber.memory import (
    MemoryCommandExecutionResult,
    MemoryOperationStatus,
    MemoryReasonCode,
    MemoryScope,
)
from open_llm_vtuber.service_context import ServiceContext


def make_context(tmp_path: Path) -> ServiceContext:
    context = ServiceContext(project_root=tmp_path)
    context.config = SimpleNamespace(
        memory_config=MemoryConfig(
            enabled=True,
            explicit_capture=True,
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
    context.memory_service = Mock()
    return context


def test_service_context_passes_scope_configuration_and_eligibility(
    tmp_path: Path,
) -> None:
    context = make_context(tmp_path)
    expected = MemoryCommandExecutionResult.not_command()
    context.memory_command_controller.handle = AsyncMock(return_value=expected)

    result = asyncio.run(context.handle_explicit_memory_command("今天天气怎么样"))

    assert result is expected
    context.memory_command_controller.handle.assert_awaited_once_with(
        "今天天气怎么样",
        service=context.memory_service,
        scope=MemoryScope(
            profile_id="local_default",
            character_conf_uid="elysia_mvp_001",
        ),
        enabled=True,
        explicit_capture=True,
        eligible=True,
        max_items=17,
        max_item_chars=123,
    )


def test_proxy_and_non_basic_agent_are_passed_as_ineligible(tmp_path: Path) -> None:
    context = make_context(tmp_path)
    context.system_config.enable_proxy = True
    context.memory_command_controller.handle = AsyncMock(
        return_value=MemoryCommandExecutionResult.not_command()
    )

    asyncio.run(context.handle_explicit_memory_command("记住，我喜欢音乐"))

    assert (
        context.memory_command_controller.handle.await_args.kwargs["eligible"] is False
    )


def test_proactive_input_skips_parser_and_clears_pending(tmp_path: Path) -> None:
    context = make_context(tmp_path)
    context.memory_command_controller.handle = AsyncMock()
    context.memory_command_controller.clear_pending = Mock()

    result = asyncio.run(
        context.handle_explicit_memory_command(
            "Please say something.",
            metadata={"proactive_speak": True, "skip_memory": True},
        )
    )

    assert not result.handled
    context.memory_command_controller.handle.assert_not_awaited()
    context.memory_command_controller.clear_pending.assert_called_once_with()


def test_unexpected_controller_failure_is_sanitized_and_fail_closed(
    tmp_path: Path,
) -> None:
    context = make_context(tmp_path)
    secret = "PRIVATE_MEMORY_VALUE"
    context.memory_command_controller.handle = AsyncMock(
        side_effect=RuntimeError(secret)
    )
    context.memory_command_controller.clear_pending = Mock()
    logs: list[str] = []
    sink_id = logger.add(
        lambda message: logs.append(str(message)),
        format="{level} {message}",
        level="WARNING",
    )

    try:
        result = asyncio.run(context.handle_explicit_memory_command("记住，我喜欢音乐"))
    finally:
        logger.remove(sink_id)

    assert result.handled
    assert result.status == MemoryOperationStatus.FAILED
    assert result.reason_code == MemoryReasonCode.STORAGE_FAILURE
    assert "记住了" not in (result.feedback_text or "")
    assert secret not in "".join(logs)
    context.memory_command_controller.clear_pending.assert_called_once_with()
