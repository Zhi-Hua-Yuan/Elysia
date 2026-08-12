import asyncio
import json
import os
from datetime import datetime, timezone
from pathlib import Path

import pytest

import open_llm_vtuber.memory.store as memory_store_module
from open_llm_vtuber.memory import (
    MAX_MEMORY_FILE_BYTES,
    MemoryCategory,
    MemoryDocument,
    MemoryItem,
    MemoryScope,
    MemorySource,
    MemoryStorePathError,
    MemoryStoreReadError,
    MemoryStoreRecoveryError,
    MemoryStoreRevisionConflictError,
    MemoryStoreScopeMismatchError,
    MemoryStoreWriteError,
    PersistentMemoryStore,
)


NOW = datetime(2026, 8, 12, 8, 0, tzinfo=timezone.utc)
SCOPE = MemoryScope(
    profile_id="local_default",
    character_conf_uid="elysia_mvp_001",
)


def make_document(
    *,
    value: str = "饮料不要太甜",
    revision: int = 1,
    profile_id: str = SCOPE.profile_id,
    character_conf_uid: str = SCOPE.character_conf_uid,
) -> MemoryDocument:
    item = MemoryItem(
        id="9b88f7a60a3c49c0a4fe87a044264d22",
        category=MemoryCategory.PREFERENCE,
        key="preference:drink_sweetness",
        value=value,
        source=MemorySource.MANUAL_UI,
        created_at=NOW,
        updated_at=NOW,
    )
    return MemoryDocument(
        profile_id=profile_id,
        character_conf_uid=character_conf_uid,
        revision=revision,
        updated_at=NOW,
        items=[item],
    )


def make_store(tmp_path: Path) -> PersistentMemoryStore:
    return PersistentMemoryStore(
        project_root=tmp_path,
        storage_dir="memory_data",
        clock=lambda: NOW,
    )


def test_missing_document_returns_empty_without_creating_storage(
    tmp_path: Path,
) -> None:
    store = make_store(tmp_path)

    document = asyncio.run(store.load(SCOPE))

    assert document.profile_id == SCOPE.profile_id
    assert document.character_conf_uid == SCOPE.character_conf_uid
    assert document.revision == 0
    assert document.updated_at == NOW
    assert document.items == []
    assert not store.storage_root.exists()


@pytest.mark.parametrize(
    "storage_dir",
    ["", ".", "../escape", "memory/../escape", "/absolute/memory", "C:\\memory"],
)
def test_store_rejects_unsafe_storage_roots(
    tmp_path: Path,
    storage_dir: str,
) -> None:
    with pytest.raises(MemoryStorePathError):
        PersistentMemoryStore(tmp_path, storage_dir)


def test_first_save_creates_utf8_document_with_revision_one(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    expected = make_document(revision=1)

    saved = asyncio.run(store.save(SCOPE, expected, expected_revision=0))
    loaded = asyncio.run(store.load(SCOPE))
    target = store.storage_root / SCOPE.profile_id / f"{SCOPE.character_conf_uid}.json"

    assert saved == expected
    assert saved.revision == 1
    assert loaded == expected
    assert "饮料不要太甜" in target.read_text(encoding="utf-8")
    assert target.read_bytes().endswith(b"\n")


def test_new_store_instance_reads_the_persisted_document(tmp_path: Path) -> None:
    first_store = make_store(tmp_path)
    expected = make_document(value="跨重启保留", revision=1)

    asyncio.run(first_store.save(SCOPE, expected, expected_revision=0))
    second_store = make_store(tmp_path)

    assert second_store is not first_store
    assert asyncio.run(second_store.load(SCOPE)) == expected


def test_second_save_atomically_replaces_the_complete_document(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    asyncio.run(
        store.save(
            SCOPE,
            make_document(value="旧偏好", revision=1),
            expected_revision=0,
        )
    )

    replacement = make_document(value="新偏好", revision=2)
    asyncio.run(store.save(SCOPE, replacement, expected_revision=1))

    assert asyncio.run(store.load(SCOPE)) == replacement


def test_atomic_replace_uses_a_temporary_file_in_the_target_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = make_store(tmp_path)
    real_replace = os.replace
    observed: dict[str, Path] = {}

    def recording_replace(source: str | Path, destination: str | Path) -> None:
        observed["source"] = Path(source)
        observed["destination"] = Path(destination)
        real_replace(source, destination)

    monkeypatch.setattr(memory_store_module.os, "replace", recording_replace)

    asyncio.run(store.save(SCOPE, make_document(), expected_revision=0))

    assert observed["source"].parent == observed["destination"].parent
    assert observed["source"].name.endswith(".tmp")
    assert not observed["source"].exists()
    assert not list(observed["destination"].parent.glob("*.tmp"))


def test_replace_failure_preserves_old_document_and_cleans_temporary_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = make_store(tmp_path)
    old_document = make_document(value="旧值", revision=1)
    asyncio.run(store.save(SCOPE, old_document, expected_revision=0))

    def fail_replace(source: str | Path, destination: str | Path) -> None:
        raise PermissionError("simulated replace failure")

    monkeypatch.setattr(memory_store_module.os, "replace", fail_replace)

    with pytest.raises(MemoryStoreWriteError):
        asyncio.run(
            store.save(
                SCOPE,
                make_document(value="新值", revision=2),
                expected_revision=1,
            )
        )

    assert asyncio.run(store.load(SCOPE)) == old_document
    target_dir = store.storage_root / SCOPE.profile_id
    assert not list(target_dir.glob("*.tmp"))


def test_temporary_validation_failure_preserves_old_document(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = make_store(tmp_path)
    old_document = make_document(value="旧值", revision=1)
    asyncio.run(store.save(SCOPE, old_document, expected_revision=0))
    target = store.storage_root / SCOPE.profile_id / f"{SCOPE.character_conf_uid}.json"
    old_payload = target.read_bytes()

    real_prepare = store._prepare_validated_temp

    def fail_new_document_temp(
        path: Path,
        scope: MemoryScope,
        document: MemoryDocument,
    ) -> tuple[Path, MemoryDocument]:
        if document.revision == 2:
            raise MemoryStoreWriteError("Temporary memory document failed validation")
        return real_prepare(path, scope, document)

    monkeypatch.setattr(store, "_prepare_validated_temp", fail_new_document_temp)

    with pytest.raises(MemoryStoreWriteError, match="failed validation"):
        asyncio.run(
            store.save(
                SCOPE,
                make_document(value="新值", revision=2),
                expected_revision=1,
            )
        )

    assert target.read_bytes() == old_payload
    assert not list(target.parent.glob("*.tmp"))


def test_save_rejects_scope_mismatch_before_touching_disk(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    mismatched = make_document(character_conf_uid="another_character")

    with pytest.raises(MemoryStoreScopeMismatchError):
        asyncio.run(store.save(SCOPE, mismatched, expected_revision=0))

    assert not store.storage_root.exists()


def test_load_rejects_document_whose_payload_scope_does_not_match_path(
    tmp_path: Path,
) -> None:
    store = make_store(tmp_path)
    target = store.storage_root / SCOPE.profile_id / f"{SCOPE.character_conf_uid}.json"
    target.parent.mkdir(parents=True)
    mismatched = make_document(character_conf_uid="another_character")
    target.write_text(mismatched.model_dump_json(), encoding="utf-8")

    with pytest.raises(MemoryStoreRecoveryError):
        asyncio.run(store.load(SCOPE))


def test_load_rejects_invalid_json_without_exposing_document_content(
    tmp_path: Path,
) -> None:
    store = make_store(tmp_path)
    target = store.storage_root / SCOPE.profile_id / f"{SCOPE.character_conf_uid}.json"
    target.parent.mkdir(parents=True)
    secret = "do-not-leak-this-memory"
    target.write_text(f'{{"value":"{secret}"', encoding="utf-8")

    with pytest.raises(MemoryStoreReadError) as exc_info:
        asyncio.run(store.load(SCOPE))

    assert secret not in str(exc_info.value)


def test_load_rejects_unknown_schema_version(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    target = store.storage_root / SCOPE.profile_id / f"{SCOPE.character_conf_uid}.json"
    target.parent.mkdir(parents=True)
    payload = make_document().model_dump(mode="json")
    payload["schema_version"] = 2
    target.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    with pytest.raises(MemoryStoreReadError):
        asyncio.run(store.load(SCOPE))


def test_load_rejects_oversized_document_before_json_parsing(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    target = store.storage_root / SCOPE.profile_id / f"{SCOPE.character_conf_uid}.json"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"x" * (MAX_MEMORY_FILE_BYTES + 1))

    with pytest.raises(MemoryStoreRecoveryError):
        asyncio.run(store.load(SCOPE))


def test_load_rejects_directory_disguised_as_document(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    target = store.storage_root / SCOPE.profile_id / f"{SCOPE.character_conf_uid}.json"
    target.mkdir(parents=True)

    with pytest.raises(MemoryStoreRecoveryError):
        asyncio.run(store.load(SCOPE))


def test_revision_must_start_at_one_and_advance_exactly_once(tmp_path: Path) -> None:
    store = make_store(tmp_path)

    with pytest.raises(MemoryStoreRevisionConflictError):
        asyncio.run(store.save(SCOPE, make_document(revision=2), expected_revision=0))

    first = make_document(revision=1)
    asyncio.run(store.save(SCOPE, first, expected_revision=0))

    with pytest.raises(MemoryStoreRevisionConflictError):
        asyncio.run(store.save(SCOPE, make_document(revision=3), expected_revision=1))

    assert asyncio.run(store.load(SCOPE)) == first


def test_stale_expected_revision_does_not_touch_main_or_backup(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    first = make_document(value="第一版", revision=1)
    second = make_document(value="第二版", revision=2)
    asyncio.run(store.save(SCOPE, first, expected_revision=0))
    asyncio.run(store.save(SCOPE, second, expected_revision=1))
    target = store._get_document_path(SCOPE)
    backup = store._get_backup_path(SCOPE)
    main_payload = target.read_bytes()
    backup_payload = backup.read_bytes()

    with pytest.raises(MemoryStoreRevisionConflictError) as exc_info:
        asyncio.run(
            store.save(
                SCOPE,
                make_document(value="冲突版本", revision=2),
                expected_revision=1,
            )
        )

    assert exc_info.value.expected_revision == 1
    assert exc_info.value.actual_revision == 2
    assert target.read_bytes() == main_payload
    assert backup.read_bytes() == backup_payload
    assert not list(target.parent.glob("*.tmp"))


def test_backup_tracks_the_immediately_previous_valid_revision(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    first = make_document(value="第一版", revision=1)
    second = make_document(value="第二版", revision=2)
    third = make_document(value="第三版", revision=3)

    asyncio.run(store.save(SCOPE, first, expected_revision=0))
    backup = store._get_backup_path(SCOPE)
    assert not backup.exists()

    asyncio.run(store.save(SCOPE, second, expected_revision=1))
    assert store._load_document_file(SCOPE, backup) == first

    asyncio.run(store.save(SCOPE, third, expected_revision=2))
    assert asyncio.run(store.load(SCOPE)) == third
    assert store._load_document_file(SCOPE, backup) == second


def test_backup_replace_failure_keeps_main_and_existing_backup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = make_store(tmp_path)
    first = make_document(value="第一版", revision=1)
    second = make_document(value="第二版", revision=2)
    asyncio.run(store.save(SCOPE, first, expected_revision=0))
    asyncio.run(store.save(SCOPE, second, expected_revision=1))
    target = store._get_document_path(SCOPE)
    backup = store._get_backup_path(SCOPE)
    main_payload = target.read_bytes()
    backup_payload = backup.read_bytes()
    real_replace = os.replace

    def fail_backup_replace(source: str | Path, destination: str | Path) -> None:
        if Path(destination) == backup:
            raise PermissionError("simulated backup replace failure")
        real_replace(source, destination)

    monkeypatch.setattr(memory_store_module.os, "replace", fail_backup_replace)

    with pytest.raises(MemoryStoreWriteError):
        asyncio.run(
            store.save(
                SCOPE,
                make_document(value="第三版", revision=3),
                expected_revision=2,
            )
        )

    assert target.read_bytes() == main_payload
    assert backup.read_bytes() == backup_payload
    assert not list(target.parent.glob("*.tmp"))


def test_main_replace_failure_leaves_main_and_backup_at_current_revision(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = make_store(tmp_path)
    first = make_document(value="第一版", revision=1)
    second = make_document(value="第二版", revision=2)
    asyncio.run(store.save(SCOPE, first, expected_revision=0))
    asyncio.run(store.save(SCOPE, second, expected_revision=1))
    target = store._get_document_path(SCOPE)
    backup = store._get_backup_path(SCOPE)
    real_replace = os.replace

    def fail_main_replace(source: str | Path, destination: str | Path) -> None:
        if Path(destination) == target:
            raise PermissionError("simulated main replace failure")
        real_replace(source, destination)

    monkeypatch.setattr(memory_store_module.os, "replace", fail_main_replace)

    with pytest.raises(MemoryStoreWriteError):
        asyncio.run(
            store.save(
                SCOPE,
                make_document(value="第三版", revision=3),
                expected_revision=2,
            )
        )

    assert store._load_document_file(SCOPE, target) == second
    assert store._load_document_file(SCOPE, backup) == second
    assert not list(target.parent.glob("*.tmp"))


def test_corrupt_main_is_atomically_restored_from_valid_backup(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    first = make_document(value="可恢复版本", revision=1)
    second = make_document(value="损坏前版本", revision=2)
    asyncio.run(store.save(SCOPE, first, expected_revision=0))
    asyncio.run(store.save(SCOPE, second, expected_revision=1))
    target = store._get_document_path(SCOPE)
    backup = store._get_backup_path(SCOPE)
    target.write_text("{broken-json", encoding="utf-8")

    recovered = asyncio.run(store.load(SCOPE))

    assert recovered == first
    assert recovered.revision == 1
    assert store._load_document_file(SCOPE, target) == first
    assert store._load_document_file(SCOPE, backup) == first


def test_double_corruption_does_not_overwrite_either_file(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    asyncio.run(store.save(SCOPE, make_document(revision=1), expected_revision=0))
    asyncio.run(
        store.save(
            SCOPE,
            make_document(value="第二版", revision=2),
            expected_revision=1,
        )
    )
    target = store._get_document_path(SCOPE)
    backup = store._get_backup_path(SCOPE)
    target_payload = b"{broken-main"
    backup_payload = b"{broken-backup"
    target.write_bytes(target_payload)
    backup.write_bytes(backup_payload)

    with pytest.raises(MemoryStoreRecoveryError):
        asyncio.run(store.load(SCOPE))

    assert target.read_bytes() == target_payload
    assert backup.read_bytes() == backup_payload


def test_backup_scope_mismatch_is_rejected_without_overwriting_files(
    tmp_path: Path,
) -> None:
    store = make_store(tmp_path)
    asyncio.run(store.save(SCOPE, make_document(revision=1), expected_revision=0))
    asyncio.run(
        store.save(
            SCOPE,
            make_document(value="第二版", revision=2),
            expected_revision=1,
        )
    )
    target = store._get_document_path(SCOPE)
    backup = store._get_backup_path(SCOPE)
    corrupt_main = b"{broken-main"
    mismatched_backup = make_document(character_conf_uid="another_character")
    target.write_bytes(corrupt_main)
    backup.write_text(mismatched_backup.model_dump_json(), encoding="utf-8")
    backup_payload = backup.read_bytes()

    with pytest.raises(MemoryStoreRecoveryError):
        asyncio.run(store.load(SCOPE))

    assert target.read_bytes() == corrupt_main
    assert backup.read_bytes() == backup_payload


def test_missing_main_does_not_revive_orphaned_backup(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    backup = store._get_backup_path(SCOPE)
    backup.parent.mkdir(parents=True)
    backup.write_text(make_document().model_dump_json(), encoding="utf-8")

    loaded = asyncio.run(store.load(SCOPE))

    assert loaded.revision == 0
    assert loaded.items == []
    assert not store._get_document_path(SCOPE).exists()


def test_valid_main_ignores_corrupt_backup(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    current = make_document(revision=1)
    asyncio.run(store.save(SCOPE, current, expected_revision=0))
    backup = store._get_backup_path(SCOPE)
    backup.write_text("{broken-backup", encoding="utf-8")

    assert asyncio.run(store.load(SCOPE)) == current


def test_recovery_replace_failure_preserves_corrupt_main_and_valid_backup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = make_store(tmp_path)
    first = make_document(value="备份", revision=1)
    asyncio.run(store.save(SCOPE, first, expected_revision=0))
    asyncio.run(
        store.save(
            SCOPE,
            make_document(value="主版本", revision=2),
            expected_revision=1,
        )
    )
    target = store._get_document_path(SCOPE)
    backup = store._get_backup_path(SCOPE)
    corrupt_payload = b"{broken-main"
    backup_payload = backup.read_bytes()
    target.write_bytes(corrupt_payload)
    real_replace = os.replace

    def fail_recovery_replace(source: str | Path, destination: str | Path) -> None:
        if Path(destination) == target:
            raise PermissionError("simulated recovery failure")
        real_replace(source, destination)

    monkeypatch.setattr(memory_store_module.os, "replace", fail_recovery_replace)

    with pytest.raises(MemoryStoreRecoveryError):
        asyncio.run(store.load(SCOPE))

    assert target.read_bytes() == corrupt_payload
    assert backup.read_bytes() == backup_payload
    assert not list(target.parent.glob("*.tmp"))


def test_concurrent_compare_and_swap_allows_only_one_writer(tmp_path: Path) -> None:
    first_store = make_store(tmp_path)
    second_store = make_store(tmp_path)
    initial = make_document(value="初始", revision=1)
    asyncio.run(first_store.save(SCOPE, initial, expected_revision=0))

    async def run_competing_writes():
        return await asyncio.gather(
            first_store.save(
                SCOPE,
                make_document(value="竞争 A", revision=2),
                expected_revision=1,
            ),
            second_store.save(
                SCOPE,
                make_document(value="竞争 B", revision=2),
                expected_revision=1,
            ),
            return_exceptions=True,
        )

    results = asyncio.run(run_competing_writes())

    successes = [result for result in results if isinstance(result, MemoryDocument)]
    conflicts = [
        result
        for result in results
        if isinstance(result, MemoryStoreRevisionConflictError)
    ]
    assert len(successes) == 1
    assert len(conflicts) == 1
    assert asyncio.run(first_store.load(SCOPE)) == successes[0]
    assert (
        first_store._load_document_file(SCOPE, first_store._get_backup_path(SCOPE))
        == initial
    )


def test_lock_registry_is_shared_across_stores_but_isolated_by_scope(
    tmp_path: Path,
) -> None:
    first_store = make_store(tmp_path)
    second_store = make_store(tmp_path)
    another_scope = MemoryScope(
        profile_id="local_default",
        character_conf_uid="another_character",
    )

    assert first_store._get_scope_lock(SCOPE) is second_store._get_scope_lock(SCOPE)
    assert first_store._get_scope_lock(SCOPE) is not first_store._get_scope_lock(
        another_scope
    )
