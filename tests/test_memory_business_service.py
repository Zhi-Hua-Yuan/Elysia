import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, Mock

from open_llm_vtuber.memory import (
    MemoryAction,
    MemoryBackupMode,
    MemoryCategory,
    MemoryDocument,
    MemoryItem,
    MemoryOperationStatus,
    MemoryReasonCode,
    MemoryScope,
    MemorySource,
    MemoryStoreRevisionConflictError,
    MemoryStoreWriteError,
    PersistentMemoryService,
    PersistentMemoryStore,
)


NOW = datetime(2026, 8, 13, 8, 0, tzinfo=timezone.utc)
SCOPE = MemoryScope(
    profile_id="local_default",
    character_conf_uid="elysia_mvp_001",
)


class AdvancingClock:
    def __init__(self) -> None:
        self._calls = 0

    def __call__(self) -> datetime:
        value = NOW + timedelta(seconds=self._calls)
        self._calls += 1
        return value


def make_real_service(tmp_path: Path) -> PersistentMemoryService:
    return PersistentMemoryService(
        store=PersistentMemoryStore(tmp_path, "memory_data", clock=lambda: NOW),
        clock=AdvancingClock(),
        id_factory=iter(
            [
                "10000000000000000000000000000000",
                "20000000000000000000000000000000",
                "30000000000000000000000000000000",
                "40000000000000000000000000000000",
            ]
        ).__next__,
    )


async def upsert(
    service: PersistentMemoryService,
    category: MemoryCategory,
    value: str,
    *,
    max_items: int = 24,
):
    return await service.upsert_item(
        SCOPE,
        category=category,
        value=value,
        source=MemorySource.EXPLICIT_USER,
        max_items=max_items,
        max_item_chars=160,
    )


def make_item(
    item_id: str,
    value: str,
    category: MemoryCategory = MemoryCategory.PREFERENCE,
) -> MemoryItem:
    return MemoryItem(
        id=item_id,
        category=category,
        key=(
            "preferred_address"
            if category == MemoryCategory.PREFERRED_ADDRESS
            else f"{category.value}:test"
        ),
        value=value,
        source=MemorySource.EXPLICIT_USER,
        created_at=NOW,
        updated_at=NOW,
    )


def make_document(revision: int, items: list[MemoryItem]) -> MemoryDocument:
    return MemoryDocument(
        profile_id=SCOPE.profile_id,
        character_conf_uid=SCOPE.character_conf_uid,
        revision=revision,
        updated_at=NOW,
        items=items,
    )


def test_create_list_and_new_service_instance_read_the_same_memory(
    tmp_path: Path,
) -> None:
    service = make_real_service(tmp_path)

    async def scenario():
        created = await upsert(service, MemoryCategory.PREFERENCE, "我喜欢花")
        listed = await service.list_items(SCOPE)
        restarted = PersistentMemoryService(
            PersistentMemoryStore(tmp_path, "memory_data", clock=lambda: NOW)
        )
        after_restart = await restarted.list_items(SCOPE)
        return created, listed, after_restart

    created, listed, after_restart = asyncio.run(scenario())

    assert created.changed
    assert created.operation.action == MemoryAction.CREATE
    assert created.operation.revision == 1
    assert listed.items[0].value == "我喜欢花"
    assert after_restart.items == listed.items


def test_preferred_address_updates_in_place_and_sanitizes_backup(
    tmp_path: Path,
) -> None:
    service = make_real_service(tmp_path)

    async def scenario():
        first = await upsert(service, MemoryCategory.PREFERRED_ADDRESS, "阿源")
        listed_first = await service.list_items(SCOPE)
        second = await upsert(service, MemoryCategory.PREFERRED_ADDRESS, "舰长")
        listed_second = await service.list_items(SCOPE)
        return first, listed_first, second, listed_second

    first, listed_first, second, listed_second = asyncio.run(scenario())

    old_item = listed_first.items[0]
    new_item = listed_second.items[0]
    assert second.operation.action == MemoryAction.UPDATE
    assert second.changed
    assert new_item.id == old_item.id == first.operation.memory_id
    assert new_item.created_at == old_item.created_at
    assert new_item.updated_at > old_item.updated_at
    assert new_item.value == "舰长"
    backup = service.store._get_backup_path(SCOPE)
    assert "阿源" not in backup.read_text(encoding="utf-8")


def test_duplicate_is_idempotent_across_non_address_categories(
    tmp_path: Path,
) -> None:
    service = make_real_service(tmp_path)

    async def scenario():
        first = await upsert(
            service, MemoryCategory.PREFERENCE, "Ｃａｐｔａｉｎ 喜欢花。"
        )
        duplicate = await upsert(
            service,
            MemoryCategory.IMPORTANT_FACT,
            "Captain喜欢花",
        )
        listed = await service.list_items(SCOPE)
        return first, duplicate, listed

    first, duplicate, listed = asyncio.run(scenario())

    assert first.changed
    assert not duplicate.changed
    assert duplicate.operation.action == MemoryAction.UPDATE
    assert duplicate.operation.memory_id == first.operation.memory_id
    assert duplicate.operation.revision == first.operation.revision == 1
    assert len(listed.items) == 1


def test_capacity_rejects_create_but_allows_address_update(
    tmp_path: Path,
) -> None:
    service = make_real_service(tmp_path)

    async def scenario():
        await upsert(
            service,
            MemoryCategory.PREFERRED_ADDRESS,
            "阿源",
            max_items=1,
        )
        rejected = await upsert(
            service,
            MemoryCategory.PREFERENCE,
            "我喜欢花",
            max_items=1,
        )
        updated = await upsert(
            service,
            MemoryCategory.PREFERRED_ADDRESS,
            "舰长",
            max_items=1,
        )
        return rejected, updated, await service.list_items(SCOPE)

    rejected, updated, listed = asyncio.run(scenario())

    assert rejected.operation.reason_code == MemoryReasonCode.CAPACITY_REACHED
    assert not rejected.changed
    assert updated.changed
    assert listed.items[0].value == "舰长"


def test_delete_uses_exact_then_unique_containment_and_sanitizes_backup(
    tmp_path: Path,
) -> None:
    service = make_real_service(tmp_path)

    async def scenario():
        await upsert(service, MemoryCategory.PREFERENCE, "我不喜欢太甜的饮料")
        await upsert(service, MemoryCategory.IMPORTANT_FACT, "我周末通常要加班")
        exact = await service.delete_item(SCOPE, target="我周末通常要加班")
        contained = await service.delete_item(SCOPE, target="太甜的饮料")
        return exact, contained, await service.list_items(SCOPE)

    exact, contained, listed = asyncio.run(scenario())

    assert exact.changed and contained.changed
    assert listed.items == ()
    backup = service.store._get_backup_path(SCOPE)
    backup_text = backup.read_text(encoding="utf-8")
    assert "周末通常要加班" not in backup_text
    assert "太甜的饮料" not in backup_text


def test_delete_ambiguity_and_not_found_do_not_change_revision(
    tmp_path: Path,
) -> None:
    service = make_real_service(tmp_path)

    async def scenario():
        await upsert(service, MemoryCategory.PREFERENCE, "我喜欢红茶")
        await upsert(service, MemoryCategory.PREFERENCE, "我喜欢绿茶")
        ambiguous = await service.delete_item(SCOPE, target="喜欢")
        missing = await service.delete_item(SCOPE, target="咖啡")
        return ambiguous, missing, await service.list_items(SCOPE)

    ambiguous, missing, listed = asyncio.run(scenario())

    assert ambiguous.operation.status == MemoryOperationStatus.AMBIGUOUS
    assert ambiguous.operation.reason_code == MemoryReasonCode.AMBIGUOUS_MATCH
    assert missing.operation.reason_code == MemoryReasonCode.NOT_FOUND
    assert ambiguous.operation.revision == missing.operation.revision == 2
    assert listed.operation.revision == 2
    assert len(listed.items) == 2


def test_clear_requires_current_revision_and_is_idempotent_when_empty(
    tmp_path: Path,
) -> None:
    service = make_real_service(tmp_path)

    async def scenario():
        await upsert(service, MemoryCategory.PREFERENCE, "我喜欢花")
        stale = await service.clear_items(SCOPE, expected_revision=0)
        cleared = await service.clear_items(SCOPE, expected_revision=1)
        empty = await service.clear_items(SCOPE, expected_revision=2)
        return stale, cleared, empty, await service.list_items(SCOPE)

    stale, cleared, empty, listed = asyncio.run(scenario())

    assert stale.operation.reason_code == MemoryReasonCode.REVISION_CONFLICT
    assert not stale.changed
    assert cleared.changed and cleared.operation.revision == 2
    assert not empty.changed and empty.operation.revision == 2
    assert listed.items == ()


def test_same_service_serializes_concurrent_writers(tmp_path: Path) -> None:
    service = make_real_service(tmp_path)

    async def scenario():
        results = await asyncio.gather(
            upsert(service, MemoryCategory.PREFERENCE, "我喜欢红茶"),
            upsert(service, MemoryCategory.PREFERENCE, "我喜欢绿茶"),
        )
        return results, await service.list_items(SCOPE)

    results, listed = asyncio.run(scenario())

    assert all(
        result.operation.status == MemoryOperationStatus.SUCCESS for result in results
    )
    assert {result.operation.revision for result in results} == {1, 2}
    assert len(listed.items) == 2


def test_revision_conflict_reloads_recomputes_and_retries_once() -> None:
    initial = make_document(1, [])
    external_item = make_item(
        "90000000000000000000000000000000",
        "外部写入",
        MemoryCategory.IMPORTANT_FACT,
    )
    external = make_document(2, [external_item])
    store = Mock()
    store.load = AsyncMock(side_effect=[initial, external])
    saved_documents: list[MemoryDocument] = []

    async def save_side_effect(scope, document, **kwargs):
        saved_documents.append(document)
        if len(saved_documents) == 1:
            raise MemoryStoreRevisionConflictError(1, 2)
        return document

    store.save = AsyncMock(side_effect=save_side_effect)
    service = PersistentMemoryService(
        store=store,
        clock=lambda: NOW,
        id_factory=lambda: "10000000000000000000000000000000",
    )

    result = asyncio.run(upsert(service, MemoryCategory.PREFERENCE, "我喜欢花"))

    assert result.operation.status == MemoryOperationStatus.SUCCESS
    assert result.operation.revision == 3
    assert result.item_count == 2
    assert store.load.await_count == 2
    assert store.save.await_count == 2
    assert {item.value for item in saved_documents[-1].items} == {
        "外部写入",
        "我喜欢花",
    }


def test_second_revision_conflict_returns_stable_failure() -> None:
    store = Mock()
    store.load = AsyncMock(side_effect=[make_document(1, []), make_document(2, [])])
    store.save = AsyncMock(
        side_effect=[
            MemoryStoreRevisionConflictError(1, 2),
            MemoryStoreRevisionConflictError(2, 3),
        ]
    )
    service = PersistentMemoryService(
        store=store,
        clock=lambda: NOW,
        id_factory=lambda: "10000000000000000000000000000000",
    )

    result = asyncio.run(upsert(service, MemoryCategory.PREFERENCE, "我喜欢花"))

    assert result.operation.status == MemoryOperationStatus.FAILED
    assert result.operation.reason_code == MemoryReasonCode.REVISION_CONFLICT
    assert not result.changed
    assert store.save.await_count == 2


def test_write_failure_does_not_replace_cached_document() -> None:
    initial_item = make_item(
        "90000000000000000000000000000000",
        "原有内容",
        MemoryCategory.IMPORTANT_FACT,
    )
    initial = make_document(1, [initial_item])
    store = Mock()
    store.load = AsyncMock(return_value=initial)
    store.save = AsyncMock(side_effect=MemoryStoreWriteError("PRIVATE"))
    service = PersistentMemoryService(
        store=store,
        clock=lambda: NOW,
        id_factory=lambda: "10000000000000000000000000000000",
    )

    async def scenario():
        before = await service.list_items(SCOPE)
        failed = await upsert(service, MemoryCategory.PREFERENCE, "新的内容")
        after = await service.list_items(SCOPE)
        return before, failed, after

    before, failed, after = asyncio.run(scenario())

    assert failed.operation.reason_code == MemoryReasonCode.STORAGE_FAILURE
    assert before.items == after.items == (initial_item,)
    store.load.assert_awaited_once()


def test_business_write_runs_content_policy_before_storage() -> None:
    store = Mock()
    store.load = AsyncMock()
    store.save = AsyncMock()
    service = PersistentMemoryService(store=store)

    result = asyncio.run(
        upsert(
            service,
            MemoryCategory.IMPORTANT_FACT,
            "我的 API Key 是 sk-private-secret-123456",
        )
    )

    assert result.operation.reason_code == MemoryReasonCode.SENSITIVE_CONTENT
    store.load.assert_not_awaited()
    store.save.assert_not_awaited()


def test_business_service_selects_expected_backup_modes() -> None:
    store = Mock()
    address = make_item(
        "90000000000000000000000000000000",
        "阿源",
        MemoryCategory.PREFERRED_ADDRESS,
    )
    initial = make_document(1, [address])
    store.load = AsyncMock(return_value=initial)

    async def save_side_effect(scope, document, **kwargs):
        return document

    store.save = AsyncMock(side_effect=save_side_effect)
    service = PersistentMemoryService(store=store, clock=lambda: NOW)

    result = asyncio.run(upsert(service, MemoryCategory.PREFERRED_ADDRESS, "舰长"))

    assert result.changed
    assert store.save.await_args.kwargs["backup_mode"] == MemoryBackupMode.REPLACEMENT
