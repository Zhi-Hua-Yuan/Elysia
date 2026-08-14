import asyncio
import os
from datetime import datetime, timezone
from pathlib import Path

import pytest

import open_llm_vtuber.memory.store as memory_store_module
from open_llm_vtuber.memory import (
    MemoryBackupMode,
    MemoryCategory,
    MemoryDocument,
    MemoryItem,
    MemoryScope,
    MemorySource,
    MemoryStoreWriteError,
    PersistentMemoryStore,
)


NOW = datetime(2026, 8, 13, 8, 0, tzinfo=timezone.utc)
SCOPE = MemoryScope(
    profile_id="local_default",
    character_conf_uid="elysia_mvp_001",
)
MEMORY_ID = "10000000000000000000000000000000"


def make_store(tmp_path: Path) -> PersistentMemoryStore:
    return PersistentMemoryStore(tmp_path, "memory_data", clock=lambda: NOW)


def make_document(value: str | None, revision: int) -> MemoryDocument:
    items = []
    if value is not None:
        items.append(
            MemoryItem(
                id=MEMORY_ID,
                category=MemoryCategory.PREFERENCE,
                key="preference:test",
                value=value,
                source=MemorySource.EXPLICIT_USER,
                created_at=NOW,
                updated_at=NOW,
            )
        )
    return MemoryDocument(
        profile_id=SCOPE.profile_id,
        character_conf_uid=SCOPE.character_conf_uid,
        revision=revision,
        updated_at=NOW,
        items=items,
    )


def test_replacement_backup_contains_only_the_sanitized_document(
    tmp_path: Path,
) -> None:
    store = make_store(tmp_path)
    old_value = "已经删除的私人事实"
    current = make_document(old_value, 1)
    sanitized = make_document(None, 2)
    asyncio.run(store.save(SCOPE, current, expected_revision=0))

    asyncio.run(
        store.save(
            SCOPE,
            sanitized,
            expected_revision=1,
            backup_mode=MemoryBackupMode.REPLACEMENT,
        )
    )

    target = store._get_document_path(SCOPE)
    backup = store._get_backup_path(SCOPE)
    assert store._load_document_file(SCOPE, target) == sanitized
    assert store._load_document_file(SCOPE, backup) == sanitized
    assert old_value not in target.read_text(encoding="utf-8")
    assert old_value not in backup.read_text(encoding="utf-8")


def test_recovery_after_destructive_write_cannot_revive_old_content(
    tmp_path: Path,
) -> None:
    store = make_store(tmp_path)
    old_value = "不能恢复的旧内容"
    asyncio.run(store.save(SCOPE, make_document(old_value, 1), expected_revision=0))
    sanitized = make_document(None, 2)
    asyncio.run(
        store.save(
            SCOPE,
            sanitized,
            expected_revision=1,
            backup_mode=MemoryBackupMode.REPLACEMENT,
        )
    )
    target = store._get_document_path(SCOPE)
    target.write_text("{broken-main", encoding="utf-8")

    recovered = asyncio.run(store.load(SCOPE))

    assert recovered == sanitized
    assert old_value not in target.read_text(encoding="utf-8")


def test_replacement_backup_failure_does_not_change_main_document(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = make_store(tmp_path)
    current = make_document("仍在使用", 1)
    asyncio.run(store.save(SCOPE, current, expected_revision=0))
    target = store._get_document_path(SCOPE)
    backup = store._get_backup_path(SCOPE)
    real_replace = os.replace

    def fail_backup(source: str | Path, destination: str | Path) -> None:
        if Path(destination) == backup:
            raise PermissionError("simulated backup failure")
        real_replace(source, destination)

    monkeypatch.setattr(memory_store_module.os, "replace", fail_backup)

    with pytest.raises(MemoryStoreWriteError):
        asyncio.run(
            store.save(
                SCOPE,
                make_document(None, 2),
                expected_revision=1,
                backup_mode=MemoryBackupMode.REPLACEMENT,
            )
        )

    assert store._load_document_file(SCOPE, target) == current
    assert not list(target.parent.glob("*.tmp"))


def test_replacement_main_failure_never_reports_success_and_cleans_temps(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = make_store(tmp_path)
    current = make_document("当前内容", 1)
    desired = make_document(None, 2)
    asyncio.run(store.save(SCOPE, current, expected_revision=0))
    target = store._get_document_path(SCOPE)
    backup = store._get_backup_path(SCOPE)
    real_replace = os.replace

    def fail_main(source: str | Path, destination: str | Path) -> None:
        if Path(destination) == target:
            raise PermissionError("simulated main failure")
        real_replace(source, destination)

    monkeypatch.setattr(memory_store_module.os, "replace", fail_main)

    with pytest.raises(MemoryStoreWriteError):
        asyncio.run(
            store.save(
                SCOPE,
                desired,
                expected_revision=1,
                backup_mode=MemoryBackupMode.REPLACEMENT,
            )
        )

    assert store._load_document_file(SCOPE, target) == current
    assert store._load_document_file(SCOPE, backup) == current
    assert not list(target.parent.glob("*.tmp"))
