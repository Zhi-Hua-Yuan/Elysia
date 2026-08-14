import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, Mock

import pytest

from open_llm_vtuber.memory import (
    MemoryAction,
    MemoryBackupMode,
    MemoryCategory,
    MemoryDocument,
    MemoryItem,
    MemoryReasonCode,
    MemoryScope,
    MemorySource,
    MemoryStoreRevisionConflictError,
    MemoryStoreWriteError,
    PersistentMemoryService,
    PersistentMemoryStore,
)


NOW = datetime(2026, 8, 14, 8, 0, tzinfo=timezone.utc)
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


def make_service(tmp_path: Path) -> PersistentMemoryService:
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


def make_item(
    item_id: str,
    value: str,
    category: MemoryCategory = MemoryCategory.PREFERENCE,
    *,
    source: MemorySource = MemorySource.EXPLICIT_USER,
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
        source=source,
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


async def management_upsert(
    service: PersistentMemoryService,
    category: MemoryCategory,
    value: str,
    expected_revision: int,
    *,
    max_items: int = 24,
):
    return await service.management_upsert_item(
        SCOPE,
        category=category,
        value=value,
        expected_revision=expected_revision,
        max_items=max_items,
        max_item_chars=160,
    )


async def explicit_upsert(
    service: PersistentMemoryService,
    category: MemoryCategory,
    value: str,
):
    return await service.upsert_item(
        SCOPE,
        category=category,
        value=value,
        source=MemorySource.EXPLICIT_USER,
        max_items=24,
        max_item_chars=160,
    )


def test_management_upsert_creates_manual_item_and_deduplicates(
    tmp_path: Path,
) -> None:
    service = make_service(tmp_path)

    async def scenario():
        created = await management_upsert(
            service,
            MemoryCategory.PREFERENCE,
            "我喜欢花。",
            0,
        )
        duplicate = await management_upsert(
            service,
            MemoryCategory.IMPORTANT_FACT,
            "我喜欢花",
            1,
        )
        return created, duplicate, await service.list_items(SCOPE)

    created, duplicate, listed = asyncio.run(scenario())

    assert created.changed
    assert created.operation.action == MemoryAction.CREATE
    assert created.operation.revision == 1
    assert not duplicate.changed
    assert duplicate.operation.action == MemoryAction.UPDATE
    assert duplicate.operation.memory_id == created.operation.memory_id
    assert duplicate.operation.revision == 1
    assert len(listed.items) == 1
    assert listed.items[0].source == MemorySource.MANUAL_UI


def test_management_address_update_is_allowed_at_capacity_and_sanitizes_backup(
    tmp_path: Path,
) -> None:
    service = make_service(tmp_path)

    async def scenario():
        first = await management_upsert(
            service,
            MemoryCategory.PREFERRED_ADDRESS,
            "阿源",
            0,
            max_items=1,
        )
        updated = await management_upsert(
            service,
            MemoryCategory.PREFERRED_ADDRESS,
            "舰长",
            1,
            max_items=1,
        )
        return first, updated, await service.list_items(SCOPE)

    first, updated, listed = asyncio.run(scenario())

    assert updated.changed
    assert updated.operation.action == MemoryAction.UPDATE
    assert updated.operation.memory_id == first.operation.memory_id
    assert listed.items[0].value == "舰长"
    backup_text = service.store._get_backup_path(SCOPE).read_text(encoding="utf-8")
    assert "阿源" not in backup_text
    assert "舰长" in backup_text


def test_management_upsert_rejects_stale_revision_without_retry(
    tmp_path: Path,
) -> None:
    service = make_service(tmp_path)

    async def scenario():
        await management_upsert(service, MemoryCategory.PREFERENCE, "我喜欢花", 0)
        stale = await management_upsert(
            service,
            MemoryCategory.PREFERENCE,
            "我喜欢红茶",
            0,
        )
        return stale, await service.list_items(SCOPE)

    stale, listed = asyncio.run(scenario())

    assert stale.operation.reason_code == MemoryReasonCode.REVISION_CONFLICT
    assert stale.operation.revision == 1
    assert stale.item_count == 1
    assert not stale.changed
    assert [item.value for item in listed.items] == ["我喜欢花"]


def test_management_update_preserves_identity_and_rebuilds_key(
    tmp_path: Path,
) -> None:
    service = make_service(tmp_path)

    async def scenario():
        created = await explicit_upsert(
            service,
            MemoryCategory.PREFERENCE,
            "我喜欢花",
        )
        before = (await service.list_items(SCOPE)).items[0]
        updated = await service.management_update_item_by_id(
            SCOPE,
            memory_id=created.operation.memory_id,
            category=MemoryCategory.IMPORTANT_FACT,
            value="我每周养花",
            expected_revision=1,
            max_item_chars=160,
        )
        after = (await service.list_items(SCOPE)).items[0]
        return before, updated, after

    before, updated, after = asyncio.run(scenario())

    assert updated.changed
    assert updated.operation.action == MemoryAction.UPDATE
    assert updated.operation.revision == 2
    assert after.id == before.id
    assert after.created_at == before.created_at
    assert after.updated_at > before.updated_at
    assert after.category == MemoryCategory.IMPORTANT_FACT
    assert after.source == MemorySource.MANUAL_UI
    assert after.key.startswith("important_fact:")
    assert after.key != before.key


def test_management_update_is_idempotent_only_when_category_and_value_match(
    tmp_path: Path,
) -> None:
    service = make_service(tmp_path)

    async def scenario():
        created = await explicit_upsert(
            service,
            MemoryCategory.PREFERENCE,
            "我喜欢花",
        )
        before = (await service.list_items(SCOPE)).items[0]
        unchanged = await service.management_update_item_by_id(
            SCOPE,
            memory_id=created.operation.memory_id,
            category=MemoryCategory.PREFERENCE,
            value="我喜欢花。",
            expected_revision=1,
            max_item_chars=160,
        )
        after = (await service.list_items(SCOPE)).items[0]
        return before, unchanged, after

    before, unchanged, after = asyncio.run(scenario())

    assert not unchanged.changed
    assert unchanged.operation.revision == 1
    assert after == before
    assert after.source == MemorySource.EXPLICIT_USER


def test_management_update_rejects_duplicate_without_changing_document(
    tmp_path: Path,
) -> None:
    service = make_service(tmp_path)

    async def scenario():
        first = await management_upsert(
            service,
            MemoryCategory.PREFERENCE,
            "我喜欢红茶",
            0,
        )
        second = await management_upsert(
            service,
            MemoryCategory.IMPORTANT_FACT,
            "我周末加班",
            1,
        )
        duplicate = await service.management_update_item_by_id(
            SCOPE,
            memory_id=second.operation.memory_id,
            category=MemoryCategory.IMPORTANT_FACT,
            value="我喜欢红茶。",
            expected_revision=2,
            max_item_chars=160,
        )
        return first, second, duplicate, await service.list_items(SCOPE)

    first, second, duplicate, listed = asyncio.run(scenario())

    assert duplicate.operation.reason_code == MemoryReasonCode.DUPLICATE_ITEM
    assert duplicate.operation.revision == 2
    assert not duplicate.changed
    assert {item.id for item in listed.items} == {
        first.operation.memory_id,
        second.operation.memory_id,
    }
    assert {item.value for item in listed.items} == {"我喜欢红茶", "我周末加班"}


def test_management_update_rejects_second_preferred_address(
    tmp_path: Path,
) -> None:
    service = make_service(tmp_path)

    async def scenario():
        await management_upsert(
            service,
            MemoryCategory.PREFERRED_ADDRESS,
            "阿源",
            0,
        )
        fact = await management_upsert(
            service,
            MemoryCategory.IMPORTANT_FACT,
            "我周末加班",
            1,
        )
        result = await service.management_update_item_by_id(
            SCOPE,
            memory_id=fact.operation.memory_id,
            category=MemoryCategory.PREFERRED_ADDRESS,
            value="舰长",
            expected_revision=2,
            max_item_chars=160,
        )
        return result, await service.list_items(SCOPE)

    result, listed = asyncio.run(scenario())

    assert result.operation.reason_code == MemoryReasonCode.DUPLICATE_ITEM
    assert result.operation.revision == 2
    assert [item.category for item in listed.items].count(
        MemoryCategory.PREFERRED_ADDRESS
    ) == 1


def test_management_update_not_found_and_stale_revision_are_distinct(
    tmp_path: Path,
) -> None:
    service = make_service(tmp_path)
    missing_id = "90000000000000000000000000000000"

    async def scenario():
        await management_upsert(service, MemoryCategory.PREFERENCE, "我喜欢花", 0)
        stale = await service.management_update_item_by_id(
            SCOPE,
            memory_id=missing_id,
            category=MemoryCategory.PREFERENCE,
            value="我喜欢红茶",
            expected_revision=0,
            max_item_chars=160,
        )
        missing = await service.management_update_item_by_id(
            SCOPE,
            memory_id=missing_id,
            category=MemoryCategory.PREFERENCE,
            value="我喜欢红茶",
            expected_revision=1,
            max_item_chars=160,
        )
        return stale, missing

    stale, missing = asyncio.run(scenario())

    assert stale.operation.reason_code == MemoryReasonCode.REVISION_CONFLICT
    assert missing.operation.reason_code == MemoryReasonCode.NOT_FOUND
    assert stale.operation.revision == missing.operation.revision == 1


def test_management_update_sanitizes_previous_value_from_backup(
    tmp_path: Path,
) -> None:
    service = make_service(tmp_path)

    async def scenario():
        created = await management_upsert(
            service,
            MemoryCategory.PREFERENCE,
            "旧偏好",
            0,
        )
        return await service.management_update_item_by_id(
            SCOPE,
            memory_id=created.operation.memory_id,
            category=MemoryCategory.PREFERENCE,
            value="新偏好",
            expected_revision=1,
            max_item_chars=160,
        )

    updated = asyncio.run(scenario())

    assert updated.changed
    backup_text = service.store._get_backup_path(SCOPE).read_text(encoding="utf-8")
    assert "旧偏好" not in backup_text
    assert "新偏好" in backup_text


def test_management_delete_uses_id_and_sanitizes_backup(tmp_path: Path) -> None:
    service = make_service(tmp_path)

    async def scenario():
        first = await management_upsert(
            service,
            MemoryCategory.PREFERENCE,
            "相同开头甲",
            0,
        )
        second = await management_upsert(
            service,
            MemoryCategory.PREFERENCE,
            "相同开头乙",
            1,
        )
        deleted = await service.management_delete_item_by_id(
            SCOPE,
            memory_id=first.operation.memory_id,
            expected_revision=2,
        )
        return first, second, deleted, await service.list_items(SCOPE)

    first, second, deleted, listed = asyncio.run(scenario())

    assert deleted.changed
    assert deleted.operation.memory_id == first.operation.memory_id
    assert [item.id for item in listed.items] == [second.operation.memory_id]
    backup_text = service.store._get_backup_path(SCOPE).read_text(encoding="utf-8")
    assert "相同开头甲" not in backup_text
    assert "相同开头乙" in backup_text


def test_management_delete_rejects_stale_revision_and_missing_id(
    tmp_path: Path,
) -> None:
    service = make_service(tmp_path)
    missing_id = "90000000000000000000000000000000"

    async def scenario():
        created = await management_upsert(
            service,
            MemoryCategory.PREFERENCE,
            "我喜欢花",
            0,
        )
        stale = await service.management_delete_item_by_id(
            SCOPE,
            memory_id=created.operation.memory_id,
            expected_revision=0,
        )
        missing = await service.management_delete_item_by_id(
            SCOPE,
            memory_id=missing_id,
            expected_revision=1,
        )
        return stale, missing, await service.list_items(SCOPE)

    stale, missing, listed = asyncio.run(scenario())

    assert stale.operation.reason_code == MemoryReasonCode.REVISION_CONFLICT
    assert missing.operation.reason_code == MemoryReasonCode.NOT_FOUND
    assert len(listed.items) == 1


def test_management_clear_is_strict_and_idempotent(tmp_path: Path) -> None:
    service = make_service(tmp_path)

    async def scenario():
        await management_upsert(service, MemoryCategory.PREFERENCE, "我喜欢花", 0)
        stale = await service.management_clear_items(SCOPE, expected_revision=0)
        cleared = await service.management_clear_items(SCOPE, expected_revision=1)
        empty = await service.management_clear_items(SCOPE, expected_revision=2)
        return stale, cleared, empty, await service.list_items(SCOPE)

    stale, cleared, empty, listed = asyncio.run(scenario())

    assert stale.operation.reason_code == MemoryReasonCode.REVISION_CONFLICT
    assert stale.operation.revision == 1
    assert cleared.changed and cleared.operation.revision == 2
    assert not empty.changed and empty.operation.revision == 2
    assert listed.items == ()
    backup_text = service.store._get_backup_path(SCOPE).read_text(encoding="utf-8")
    assert "我喜欢花" not in backup_text


def test_management_mutations_validate_strict_identifiers_and_revisions(
    tmp_path: Path,
) -> None:
    service = make_service(tmp_path)

    with pytest.raises(ValueError, match="expected_revision"):
        asyncio.run(
            management_upsert(
                service,
                MemoryCategory.PREFERENCE,
                "我喜欢花",
                True,
            )
        )
    with pytest.raises(ValueError, match="memory_id"):
        asyncio.run(
            service.management_delete_item_by_id(
                SCOPE,
                memory_id="NOT-A-MEMORY-ID",
                expected_revision=0,
            )
        )


def test_management_content_policy_runs_before_storage() -> None:
    store = Mock()
    store.load = AsyncMock()
    store.save = AsyncMock()
    service = PersistentMemoryService(store=store)

    result = asyncio.run(
        management_upsert(
            service,
            MemoryCategory.IMPORTANT_FACT,
            "我的 API Key 是 sk-private-secret-123456",
            0,
        )
    )

    assert result.operation.reason_code == MemoryReasonCode.SENSITIVE_CONTENT
    store.load.assert_not_awaited()
    store.save.assert_not_awaited()


def test_management_cas_conflict_reloads_metadata_without_retrying() -> None:
    initial = make_document(1, [])
    external = make_document(
        2,
        [
            make_item(
                "90000000000000000000000000000000",
                "外部写入",
                MemoryCategory.IMPORTANT_FACT,
            )
        ],
    )
    store = Mock()
    store.load = AsyncMock(side_effect=[initial, external])
    store.save = AsyncMock(side_effect=MemoryStoreRevisionConflictError(1, 2))
    service = PersistentMemoryService(
        store=store,
        clock=lambda: NOW,
        id_factory=lambda: "10000000000000000000000000000000",
    )

    result = asyncio.run(
        management_upsert(
            service,
            MemoryCategory.PREFERENCE,
            "我喜欢花",
            1,
        )
    )

    assert result.operation.reason_code == MemoryReasonCode.REVISION_CONFLICT
    assert result.operation.revision == 2
    assert result.item_count == 1
    assert store.load.await_count == 2
    assert store.save.await_count == 1


def test_management_clear_reload_does_not_change_legacy_clear_conflict() -> None:
    item = make_item(
        "90000000000000000000000000000000",
        "原有内容",
        MemoryCategory.IMPORTANT_FACT,
    )
    initial = make_document(1, [item])
    external = make_document(2, [item])

    management_store = Mock()
    management_store.load = AsyncMock(side_effect=[initial, external])
    management_store.save = AsyncMock(
        side_effect=MemoryStoreRevisionConflictError(1, 2)
    )
    management_service = PersistentMemoryService(store=management_store)

    legacy_store = Mock()
    legacy_store.load = AsyncMock(return_value=initial)
    legacy_store.save = AsyncMock(side_effect=MemoryStoreRevisionConflictError(1, 2))
    legacy_service = PersistentMemoryService(store=legacy_store)

    management_result = asyncio.run(
        management_service.management_clear_items(SCOPE, expected_revision=1)
    )
    legacy_result = asyncio.run(legacy_service.clear_items(SCOPE, expected_revision=1))

    assert management_result.operation.reason_code == MemoryReasonCode.REVISION_CONFLICT
    assert management_result.operation.revision == 2
    assert management_result.item_count == 1
    assert management_store.load.await_count == 2
    assert management_store.save.await_count == 1
    assert legacy_result.operation.reason_code == MemoryReasonCode.REVISION_CONFLICT
    assert legacy_result.operation.revision is None
    assert legacy_store.load.await_count == 1
    assert legacy_store.save.await_count == 1


def test_same_scope_management_writers_allow_only_one_expected_revision(
    tmp_path: Path,
) -> None:
    service = make_service(tmp_path)

    async def scenario():
        results = await asyncio.gather(
            management_upsert(
                service,
                MemoryCategory.PREFERENCE,
                "我喜欢红茶",
                0,
            ),
            management_upsert(
                service,
                MemoryCategory.PREFERENCE,
                "我喜欢绿茶",
                0,
            ),
        )
        return results, await service.list_items(SCOPE)

    results, listed = asyncio.run(scenario())

    assert sum(result.changed for result in results) == 1
    assert (
        sum(
            result.operation.reason_code == MemoryReasonCode.REVISION_CONFLICT
            for result in results
        )
        == 1
    )
    assert len(listed.items) == 1
    assert listed.operation.revision == 1


def test_management_write_failure_does_not_pollute_cached_document() -> None:
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
        failed = await management_upsert(
            service,
            MemoryCategory.PREFERENCE,
            "新的内容",
            1,
        )
        after = await service.list_items(SCOPE)
        return failed, after

    failed, after = asyncio.run(scenario())

    assert failed.operation.reason_code == MemoryReasonCode.STORAGE_FAILURE
    assert after.items == (initial_item,)
    store.load.assert_awaited_once()


def test_management_service_selects_frozen_backup_modes() -> None:
    store = Mock()
    address = make_item(
        "90000000000000000000000000000000",
        "阿源",
        MemoryCategory.PREFERRED_ADDRESS,
    )
    initial = make_document(1, [address])
    store.load = AsyncMock(return_value=initial)
    store.save = AsyncMock(side_effect=lambda scope, document, **kwargs: document)
    service = PersistentMemoryService(store=store, clock=lambda: NOW)

    result = asyncio.run(
        service.management_update_item_by_id(
            SCOPE,
            memory_id=address.id,
            category=MemoryCategory.PREFERRED_ADDRESS,
            value="舰长",
            expected_revision=1,
            max_item_chars=160,
        )
    )

    assert result.changed
    assert store.save.await_args.kwargs["backup_mode"] == MemoryBackupMode.REPLACEMENT
