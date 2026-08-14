import asyncio
import hashlib
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from open_llm_vtuber.config_manager import MemoryConfig
from open_llm_vtuber.memory import (
    MemoryAction,
    MemoryMutationResult,
    MemoryOperationResult,
    MemoryOperationStatus,
    MemoryReasonCode,
)
from open_llm_vtuber.service_context import ServiceContext


def configure_context(
    tmp_path: Path,
    memory_config: MemoryConfig,
) -> ServiceContext:
    context = ServiceContext(project_root=tmp_path)
    context.config = SimpleNamespace(memory_config=memory_config)
    context.system_config = SimpleNamespace(enable_proxy=False)
    context.character_config = SimpleNamespace(
        conf_uid="elysia_mvp_001",
        agent_config=SimpleNamespace(conversation_agent_choice="basic_memory_agent"),
    )
    context._init_memory_service(memory_config)
    return context


def test_memory_survives_history_change_service_rebuild_and_disable_cycle(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        enabled = MemoryConfig(
            enabled=True,
            profile_id="m5_3_4_acceptance",
            storage_dir="memory_data",
        )
        first = configure_context(tmp_path, enabled)

        saved = await first.handle_explicit_memory_command("记住，我喜欢山茶花")
        assert saved.status == MemoryOperationStatus.SUCCESS
        assert "我喜欢山茶花" in await first.get_persistent_memory_context()

        first.history_uid = "history-a"
        context_a = await first.get_persistent_memory_context()
        first.history_uid = "history-b"
        context_b = await first.get_persistent_memory_context()
        assert context_a == context_b

        rebuilt = configure_context(tmp_path, enabled)
        rebuilt_context = await rebuilt.get_persistent_memory_context(force_reload=True)
        assert "我喜欢山茶花" in rebuilt_context

        memory_path = (
            tmp_path / "memory_data" / "m5_3_4_acceptance" / "elysia_mvp_001.json"
        )
        before_disable = hashlib.sha256(memory_path.read_bytes()).digest()
        disabled = enabled.model_copy(update={"enabled": False})
        rebuilt.config.memory_config = disabled
        rebuilt._init_memory_service(disabled)
        assert await rebuilt.get_persistent_memory_context() == ""
        assert hashlib.sha256(memory_path.read_bytes()).digest() == before_disable

        rebuilt.config.memory_config = enabled
        rebuilt._init_memory_service(enabled)
        assert "我喜欢山茶花" in await rebuilt.get_persistent_memory_context(
            force_reload=True
        )

    asyncio.run(scenario())


def test_ordinary_statement_creates_no_storage_and_remains_agent_eligible(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        config = MemoryConfig(
            enabled=True,
            profile_id="m5_3_4_ordinary",
            storage_dir="memory_data",
        )
        context = configure_context(tmp_path, config)

        result = await context.handle_explicit_memory_command("我今天有点累")

        assert not result.handled
        assert not (tmp_path / "memory_data").exists()

    asyncio.run(scenario())


def test_failed_memory_command_does_not_block_next_ordinary_turn(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        config = MemoryConfig(enabled=True)
        context = configure_context(tmp_path, config)
        failure = MemoryMutationResult(
            operation=MemoryOperationResult(
                status=MemoryOperationStatus.FAILED,
                action=MemoryAction.CREATE,
                reason_code=MemoryReasonCode.STORAGE_FAILURE,
            ),
            changed=False,
            item_count=0,
        )
        service = SimpleNamespace(upsert_item=AsyncMock(return_value=failure))
        context.memory_service = service

        failed = await context.handle_explicit_memory_command("记住，我喜欢音乐")
        ordinary = await context.handle_explicit_memory_command("我们继续聊天吧")

        assert failed.status == MemoryOperationStatus.FAILED
        assert failed.reason_code == MemoryReasonCode.STORAGE_FAILURE
        assert "记住了" not in (failed.feedback_text or "")
        assert not ordinary.handled
        service.upsert_item.assert_awaited_once()

    asyncio.run(scenario())
