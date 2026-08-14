import asyncio
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from open_llm_vtuber.memory import (
    ExplicitMemoryCommandController,
    MemoryAction,
    MemoryCategory,
    MemoryCommandKind,
    MemoryCommandExecutionResult,
    MemoryCommandParseReason,
    MemoryMutationResult,
    MemoryOperationResult,
    MemoryOperationStatus,
    MemoryReasonCode,
    MemoryScope,
    MemorySource,
    PersistentMemoryService,
    PersistentMemoryStore,
)


class MutableClock:
    def __init__(self) -> None:
        self.now = datetime(2026, 8, 14, tzinfo=timezone.utc)

    def __call__(self) -> datetime:
        return self.now


def make_service(tmp_path: Path) -> PersistentMemoryService:
    return PersistentMemoryService(
        PersistentMemoryStore(project_root=tmp_path, storage_dir="memory_data")
    )


def make_scope() -> MemoryScope:
    return MemoryScope(
        profile_id="local_default",
        character_conf_uid="elysia_mvp_001",
    )


async def handle(
    controller: ExplicitMemoryCommandController,
    service: PersistentMemoryService | None,
    text: str,
    *,
    enabled: bool = True,
    eligible: bool = True,
):
    return await controller.handle(
        text,
        service=service,
        scope=make_scope(),
        enabled=enabled,
        explicit_capture=True,
        eligible=eligible,
        max_items=24,
        max_item_chars=160,
    )


def test_save_list_and_sanitized_event_use_real_business_results(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        controller = ExplicitMemoryCommandController()
        service = make_service(tmp_path)
        secret = "我喜欢喝乌龙茶"

        saved = await handle(controller, service, f"记住，{secret}")
        assert saved.handled
        assert saved.status == MemoryOperationStatus.SUCCESS
        assert saved.action == MemoryAction.CREATE
        assert saved.changed is True
        assert saved.feedback_text == "好的，我记住了。"

        payload = saved.to_websocket_payload()
        serialized = json.dumps(payload, ensure_ascii=False)
        assert payload["type"] == "memory-operation-result"
        assert payload["command"] == MemoryCommandKind.SAVE.value
        assert secret not in serialized
        assert "text" not in payload
        assert "value" not in payload

        listed = await handle(controller, service, "你记得我什么")
        assert listed.status == MemoryOperationStatus.SUCCESS
        assert listed.item_count == 1
        assert secret in (listed.feedback_text or "")
        assert secret not in json.dumps(
            listed.to_websocket_payload(), ensure_ascii=False
        )

    asyncio.run(scenario())


def test_ordinary_chat_is_not_handled_and_cancels_pending_confirmation(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        controller = ExplicitMemoryCommandController()
        service = make_service(tmp_path)
        await handle(controller, service, "记住，我喜欢安静")
        requested = await handle(controller, service, "清空所有记忆")
        assert requested.reason_code == MemoryReasonCode.CONFIRMATION_REQUIRED
        assert controller.pending_confirmation is not None

        ordinary = await handle(controller, service, "今天天气怎么样")
        assert not ordinary.handled
        assert controller.pending_confirmation is None

    asyncio.run(scenario())


def test_invalid_command_is_handled_without_touching_storage() -> None:
    controller = ExplicitMemoryCommandController()
    service = SimpleNamespace()

    result = asyncio.run(handle(controller, service, "记住"))

    assert result.handled
    assert result.status == MemoryOperationStatus.REJECTED
    assert result.reason_code == MemoryReasonCode.INVALID_VALUE
    assert result.parse_reason == MemoryCommandParseReason.EMPTY_VALUE


def test_clear_requires_one_time_confirmation_and_sanitizes_backup(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        controller = ExplicitMemoryCommandController()
        service = make_service(tmp_path)
        scope = make_scope()
        secret = "我喜欢山茶花"
        await handle(controller, service, f"记住，{secret}")

        requested = await handle(controller, service, "清空所有记忆")
        assert requested.status == MemoryOperationStatus.REJECTED
        assert requested.reason_code == MemoryReasonCode.CONFIRMATION_REQUIRED
        assert requested.revision == 1

        confirmed = await handle(controller, service, "确认清空记忆")
        assert confirmed.status == MemoryOperationStatus.SUCCESS
        assert confirmed.action == MemoryAction.CLEAR
        assert confirmed.changed is True
        assert controller.pending_confirmation is None
        assert (await service.list_items(scope, force_reload=True)).items == ()

        repeated = await handle(controller, service, "确认清空记忆")
        assert repeated.reason_code == MemoryReasonCode.CONFIRMATION_NOT_PENDING

        backup = (
            service.store.storage_root
            / scope.profile_id
            / f"{scope.character_conf_uid}.json.bak"
        )
        assert secret not in backup.read_text(encoding="utf-8")

    asyncio.run(scenario())


def test_expired_and_cancelled_clear_never_delete(tmp_path: Path) -> None:
    async def scenario() -> None:
        clock = MutableClock()
        controller = ExplicitMemoryCommandController(clock=clock)
        service = make_service(tmp_path)
        scope = make_scope()
        await handle(controller, service, "记住，我偏好简短回复")

        await handle(controller, service, "清空所有记忆")
        cancelled = await handle(controller, service, "取消清空")
        assert cancelled.status == MemoryOperationStatus.SUCCESS
        assert len((await service.list_items(scope)).items) == 1

        await handle(controller, service, "清空所有记忆")
        clock.now += timedelta(seconds=61)
        expired = await handle(controller, service, "确认清空记忆")
        assert expired.reason_code == MemoryReasonCode.CONFIRMATION_EXPIRED
        assert len((await service.list_items(scope)).items) == 1

    asyncio.run(scenario())


def test_revision_change_after_confirmation_request_blocks_clear(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        controller = ExplicitMemoryCommandController()
        service = make_service(tmp_path)
        scope = make_scope()
        await handle(controller, service, "记住，我喜欢晴天")
        await handle(controller, service, "清空所有记忆")
        await service.upsert_item(
            scope,
            category=MemoryCategory.IMPORTANT_FACT,
            value="我周末要加班",
            source=MemorySource.EXPLICIT_USER,
            max_items=24,
            max_item_chars=160,
        )

        confirmed = await handle(controller, service, "确认清空记忆")
        assert confirmed.status == MemoryOperationStatus.FAILED
        assert confirmed.reason_code == MemoryReasonCode.REVISION_CONFLICT
        assert len((await service.list_items(scope)).items) == 2

    asyncio.run(scenario())


def test_two_concurrent_confirmations_can_clear_only_once(tmp_path: Path) -> None:
    async def scenario() -> None:
        controller = ExplicitMemoryCommandController()
        service = make_service(tmp_path)
        await handle(controller, service, "记住，我喜欢旅行")
        await handle(controller, service, "清空所有记忆")

        first, second = await asyncio.gather(
            handle(controller, service, "确认清空记忆"),
            handle(controller, service, "确认清空记忆"),
        )
        reasons = {first.reason_code, second.reason_code}
        assert MemoryReasonCode.CONFIRMATION_NOT_PENDING in reasons
        assert (
            sum(
                result.status == MemoryOperationStatus.SUCCESS
                for result in (first, second)
            )
            == 1
        )

    asyncio.run(scenario())


def test_disabled_or_ineligible_memory_commands_are_deterministically_rejected() -> (
    None
):
    controller = ExplicitMemoryCommandController()

    disabled = asyncio.run(handle(controller, None, "记住，我喜欢音乐", enabled=False))
    ineligible = asyncio.run(
        handle(controller, None, "记住，我喜欢音乐", eligible=False)
    )

    assert disabled.reason_code == MemoryReasonCode.MEMORY_DISABLED
    assert ineligible.reason_code == MemoryReasonCode.MEMORY_DISABLED
    assert "记住了" not in (disabled.feedback_text or "")


def test_execution_result_rejects_inconsistent_public_contracts() -> None:
    with pytest.raises(ValueError, match="ordinary conversation"):
        MemoryCommandExecutionResult(handled=False, changed=False)
    with pytest.raises(ValueError, match="status and feedback"):
        MemoryCommandExecutionResult(handled=True)
    with pytest.raises(ValueError, match="reason code"):
        MemoryCommandExecutionResult(
            handled=True,
            status=MemoryOperationStatus.FAILED,
            feedback_text="失败",
        )


def test_cancellation_waits_for_started_mutation_to_finish() -> None:
    async def scenario() -> None:
        started = asyncio.Event()
        release = asyncio.Event()
        completed = asyncio.Event()

        async def upsert_item(*args, **kwargs):
            started.set()
            await release.wait()
            completed.set()
            return MemoryMutationResult(
                operation=MemoryOperationResult(
                    status=MemoryOperationStatus.SUCCESS,
                    action=MemoryAction.CREATE,
                    memory_id="1" * 32,
                    revision=1,
                ),
                changed=True,
                item_count=1,
            )

        service = SimpleNamespace(upsert_item=upsert_item)
        controller = ExplicitMemoryCommandController()
        task = asyncio.create_task(handle(controller, service, "记住，我喜欢音乐"))
        await started.wait()
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done()

        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert completed.is_set()

    asyncio.run(scenario())
