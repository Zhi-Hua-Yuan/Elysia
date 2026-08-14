"""Business orchestration for persistent memory documents and contexts."""

from __future__ import annotations

import asyncio
import hashlib
import re
import uuid
from collections.abc import Callable
from datetime import datetime, timezone

from .content_policy import MemoryContentPolicy
from .context_renderer import MemoryContextRenderer
from .normalization import normalize_memory_match_text
from .store import (
    MemoryBackupMode,
    MemoryStoreError,
    MemoryStoreReadError,
    MemoryStoreRevisionConflictError,
    PersistentMemoryStore,
)
from .types import (
    MemoryAction,
    MemoryCategory,
    MemoryDocument,
    MemoryItem,
    MemoryListResult,
    MemoryMutationResult,
    MemoryOperationResult,
    MemoryOperationStatus,
    MemoryReasonCode,
    MemoryScope,
    MemorySource,
    MEMORY_ID_PATTERN,
)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


_CATEGORY_PRIORITY = {
    MemoryCategory.PREFERRED_ADDRESS: 0,
    MemoryCategory.PREFERENCE: 1,
    MemoryCategory.IMPORTANT_FACT: 2,
}


class PersistentMemoryService:
    """Share and mutate validated documents without retaining rendered prompts."""

    def __init__(
        self,
        store: PersistentMemoryStore,
        renderer: MemoryContextRenderer | None = None,
        content_policy: MemoryContentPolicy | None = None,
        clock: Callable[[], datetime] = _utc_now,
        id_factory: Callable[[], str] | None = None,
    ) -> None:
        self._store = store
        self._renderer = renderer or MemoryContextRenderer()
        self._content_policy = content_policy or MemoryContentPolicy()
        self._clock = clock
        self._id_factory = id_factory or (lambda: uuid.uuid4().hex)
        self._documents: dict[MemoryScope, MemoryDocument] = {}
        self._unavailable_scopes: set[MemoryScope] = set()
        self._mutation_locks: dict[MemoryScope, asyncio.Lock] = {}

    @property
    def store(self) -> PersistentMemoryStore:
        """Return the shared backing store."""
        return self._store

    async def render_context(
        self,
        scope: MemoryScope,
        *,
        max_item_chars: int,
        max_context_chars: int,
        force_reload: bool = False,
    ) -> str:
        """Load or reuse a document and render its bounded context."""
        if scope in self._unavailable_scopes:
            return ""

        document = self._documents.get(scope)
        if document is None or force_reload:
            document = await self._store.load(scope)
            self._documents[scope] = document

        return self._renderer.render(
            document,
            max_item_chars=max_item_chars,
            max_context_chars=max_context_chars,
        )

    async def list_items(
        self,
        scope: MemoryScope,
        *,
        force_reload: bool = False,
    ) -> MemoryListResult:
        """Return a deterministic internal snapshot for one scope."""
        try:
            document = await self._load_document(scope, force_reload=force_reload)
        except MemoryStoreError:
            return self._list_failure(MemoryReasonCode.STORAGE_FAILURE)

        items = tuple(
            sorted(
                document.items,
                key=lambda item: (
                    _CATEGORY_PRIORITY[item.category],
                    -item.updated_at.timestamp(),
                    item.id,
                ),
            )
        )
        return MemoryListResult(
            operation=MemoryOperationResult(
                status=MemoryOperationStatus.SUCCESS,
                action=MemoryAction.LIST,
                revision=document.revision,
            ),
            items=items,
        )

    async def upsert_item(
        self,
        scope: MemoryScope,
        *,
        category: MemoryCategory,
        value: str,
        source: MemorySource,
        max_items: int,
        max_item_chars: int,
    ) -> MemoryMutationResult:
        """Create, update, or deduplicate one explicitly authorized fact."""
        if not isinstance(source, MemorySource):
            raise TypeError("source must be a MemorySource")
        if type(max_items) is not int or max_items <= 0:
            raise ValueError("max_items must be a positive integer")

        decision = self._content_policy.evaluate(
            category=category,
            value=value,
            max_item_chars=max_item_chars,
        )
        if not decision.allowed:
            return self._mutation_failure(
                action=MemoryAction.CREATE,
                reason=decision.reason_code or MemoryReasonCode.INVALID_VALUE,
            )
        normalized_value = decision.normalized_value
        if normalized_value is None:
            return self._mutation_failure(
                action=MemoryAction.CREATE,
                reason=MemoryReasonCode.INVALID_VALUE,
            )

        async with self._get_mutation_lock(scope):
            attempted_action = MemoryAction.CREATE
            for attempt in range(2):
                try:
                    document = await self._load_document(
                        scope,
                        force_reload=attempt > 0,
                    )
                    attempted_action = self._infer_upsert_action(
                        document,
                        category,
                        normalized_value,
                    )
                    result = await self._upsert_loaded_document(
                        scope,
                        document,
                        category=category,
                        value=normalized_value,
                        source=source,
                        max_items=max_items,
                    )
                    return result
                except MemoryStoreRevisionConflictError:
                    self._documents.pop(scope, None)
                    continue
                except MemoryStoreError:
                    return self._mutation_failure(
                        action=attempted_action,
                        reason=MemoryReasonCode.STORAGE_FAILURE,
                    )
            return self._mutation_failure(
                action=attempted_action,
                reason=MemoryReasonCode.REVISION_CONFLICT,
            )

    async def management_upsert_item(
        self,
        scope: MemoryScope,
        *,
        category: MemoryCategory,
        value: str,
        expected_revision: int,
        max_items: int,
        max_item_chars: int,
    ) -> MemoryMutationResult:
        """Strictly create, update, or deduplicate one manual UI item."""
        self._validate_expected_revision(expected_revision)
        if type(max_items) is not int or max_items <= 0:
            raise ValueError("max_items must be a positive integer")

        decision = self._content_policy.evaluate(
            category=category,
            value=value,
            max_item_chars=max_item_chars,
        )
        if not decision.allowed:
            return self._mutation_failure(
                action=MemoryAction.CREATE,
                reason=decision.reason_code or MemoryReasonCode.INVALID_VALUE,
            )
        normalized_value = decision.normalized_value
        if normalized_value is None:
            return self._mutation_failure(
                action=MemoryAction.CREATE,
                reason=MemoryReasonCode.INVALID_VALUE,
            )

        action = MemoryAction.CREATE
        async with self._get_mutation_lock(scope):
            try:
                document = await self._load_document(scope, force_reload=True)
                action = self._infer_upsert_action(
                    document,
                    category,
                    normalized_value,
                )
                if document.revision != expected_revision:
                    return self._revision_conflict_result(action, document)
                return await self._upsert_loaded_document(
                    scope,
                    document,
                    category=category,
                    value=normalized_value,
                    source=MemorySource.MANUAL_UI,
                    max_items=max_items,
                )
            except MemoryStoreRevisionConflictError:
                return await self._reload_revision_conflict(scope, action)
            except MemoryStoreError:
                return self._mutation_failure(
                    action=action,
                    reason=MemoryReasonCode.STORAGE_FAILURE,
                )

    async def management_update_item_by_id(
        self,
        scope: MemoryScope,
        *,
        memory_id: str,
        category: MemoryCategory,
        value: str,
        expected_revision: int,
        max_item_chars: int,
    ) -> MemoryMutationResult:
        """Strictly update one manual UI item by its stable identifier."""
        self._validate_memory_id(memory_id)
        self._validate_expected_revision(expected_revision)

        decision = self._content_policy.evaluate(
            category=category,
            value=value,
            max_item_chars=max_item_chars,
        )
        if not decision.allowed:
            return self._mutation_failure(
                action=MemoryAction.UPDATE,
                reason=decision.reason_code or MemoryReasonCode.INVALID_VALUE,
            )
        normalized_value = decision.normalized_value
        if normalized_value is None:
            return self._mutation_failure(
                action=MemoryAction.UPDATE,
                reason=MemoryReasonCode.INVALID_VALUE,
            )

        async with self._get_mutation_lock(scope):
            try:
                document = await self._load_document(scope, force_reload=True)
                if document.revision != expected_revision:
                    return self._revision_conflict_result(
                        MemoryAction.UPDATE,
                        document,
                    )

                existing = self._find_item_by_id(document, memory_id)
                if existing is None:
                    return self._mutation_failure(
                        action=MemoryAction.UPDATE,
                        reason=MemoryReasonCode.NOT_FOUND,
                        revision=document.revision,
                        item_count=len(document.items),
                    )

                match_value = normalize_memory_match_text(normalized_value)
                if (
                    existing.category == category
                    and normalize_memory_match_text(existing.value) == match_value
                ):
                    return self._idempotent_result(document, existing)

                if self._find_management_duplicate(
                    document,
                    memory_id=memory_id,
                    category=category,
                    match_value=match_value,
                ):
                    return self._mutation_failure(
                        action=MemoryAction.UPDATE,
                        reason=MemoryReasonCode.DUPLICATE_ITEM,
                        revision=document.revision,
                        item_count=len(document.items),
                    )

                now = self._clock()
                replacement = MemoryItem(
                    id=existing.id,
                    category=category,
                    key=self._build_key(category, match_value),
                    value=normalized_value,
                    source=MemorySource.MANUAL_UI,
                    created_at=existing.created_at,
                    updated_at=now,
                )
                desired = self._next_document(
                    document,
                    items=[
                        replacement if item.id == memory_id else item
                        for item in document.items
                    ],
                    now=now,
                )
                persisted = await self._store.save(
                    scope,
                    desired,
                    expected_revision=expected_revision,
                    backup_mode=MemoryBackupMode.REPLACEMENT,
                )
                self._cache_persisted(scope, persisted)
                return MemoryMutationResult(
                    operation=MemoryOperationResult(
                        status=MemoryOperationStatus.SUCCESS,
                        action=MemoryAction.UPDATE,
                        memory_id=existing.id,
                        revision=persisted.revision,
                    ),
                    changed=True,
                    item_count=len(persisted.items),
                )
            except MemoryStoreRevisionConflictError:
                return await self._reload_revision_conflict(
                    scope,
                    MemoryAction.UPDATE,
                )
            except MemoryStoreError:
                return self._mutation_failure(
                    action=MemoryAction.UPDATE,
                    reason=MemoryReasonCode.STORAGE_FAILURE,
                )

    async def management_delete_item_by_id(
        self,
        scope: MemoryScope,
        *,
        memory_id: str,
        expected_revision: int,
    ) -> MemoryMutationResult:
        """Strictly delete one manual UI item by stable identifier."""
        self._validate_memory_id(memory_id)
        self._validate_expected_revision(expected_revision)

        async with self._get_mutation_lock(scope):
            try:
                document = await self._load_document(scope, force_reload=True)
                if document.revision != expected_revision:
                    return self._revision_conflict_result(
                        MemoryAction.DELETE,
                        document,
                    )

                existing = self._find_item_by_id(document, memory_id)
                if existing is None:
                    return self._mutation_failure(
                        action=MemoryAction.DELETE,
                        reason=MemoryReasonCode.NOT_FOUND,
                        revision=document.revision,
                        item_count=len(document.items),
                    )

                desired = self._next_document(
                    document,
                    items=[item for item in document.items if item.id != memory_id],
                )
                persisted = await self._store.save(
                    scope,
                    desired,
                    expected_revision=expected_revision,
                    backup_mode=MemoryBackupMode.REPLACEMENT,
                )
                self._cache_persisted(scope, persisted)
                return MemoryMutationResult(
                    operation=MemoryOperationResult(
                        status=MemoryOperationStatus.SUCCESS,
                        action=MemoryAction.DELETE,
                        memory_id=existing.id,
                        revision=persisted.revision,
                    ),
                    changed=True,
                    item_count=len(persisted.items),
                )
            except MemoryStoreRevisionConflictError:
                return await self._reload_revision_conflict(
                    scope,
                    MemoryAction.DELETE,
                )
            except MemoryStoreError:
                return self._mutation_failure(
                    action=MemoryAction.DELETE,
                    reason=MemoryReasonCode.STORAGE_FAILURE,
                )

    async def delete_item(
        self,
        scope: MemoryScope,
        *,
        target: str,
    ) -> MemoryMutationResult:
        """Delete a uniquely matched item without fuzzy or semantic guessing."""
        if not isinstance(target, str):
            raise TypeError("delete target must be a string")
        normalized_target = normalize_memory_match_text(target)
        if not normalized_target:
            return self._mutation_failure(
                action=MemoryAction.DELETE,
                reason=MemoryReasonCode.INVALID_VALUE,
            )

        async with self._get_mutation_lock(scope):
            for attempt in range(2):
                try:
                    document = await self._load_document(
                        scope,
                        force_reload=attempt > 0,
                    )
                    candidates = self._find_delete_candidates(
                        document,
                        normalized_target,
                    )
                    if not candidates:
                        return self._mutation_failure(
                            action=MemoryAction.DELETE,
                            reason=MemoryReasonCode.NOT_FOUND,
                            revision=document.revision,
                            item_count=len(document.items),
                        )
                    if len(candidates) > 1:
                        return self._mutation_failure(
                            action=MemoryAction.DELETE,
                            reason=MemoryReasonCode.AMBIGUOUS_MATCH,
                            status=MemoryOperationStatus.AMBIGUOUS,
                            revision=document.revision,
                            item_count=len(document.items),
                        )

                    item = candidates[0]
                    desired = self._next_document(
                        document,
                        items=[
                            entry for entry in document.items if entry.id != item.id
                        ],
                    )
                    persisted = await self._store.save(
                        scope,
                        desired,
                        expected_revision=document.revision,
                        backup_mode=MemoryBackupMode.REPLACEMENT,
                    )
                    self._cache_persisted(scope, persisted)
                    return MemoryMutationResult(
                        operation=MemoryOperationResult(
                            status=MemoryOperationStatus.SUCCESS,
                            action=MemoryAction.DELETE,
                            memory_id=item.id,
                            revision=persisted.revision,
                        ),
                        changed=True,
                        item_count=len(persisted.items),
                    )
                except MemoryStoreRevisionConflictError:
                    self._documents.pop(scope, None)
                    continue
                except MemoryStoreError:
                    return self._mutation_failure(
                        action=MemoryAction.DELETE,
                        reason=MemoryReasonCode.STORAGE_FAILURE,
                    )
            return self._mutation_failure(
                action=MemoryAction.DELETE,
                reason=MemoryReasonCode.REVISION_CONFLICT,
            )

    async def clear_items(
        self,
        scope: MemoryScope,
        *,
        expected_revision: int,
    ) -> MemoryMutationResult:
        """Clear a confirmed revision without retrying against newer data."""
        return await self._clear_items_at_revision(
            scope,
            expected_revision=expected_revision,
            reload_conflict_metadata=False,
        )

    async def management_clear_items(
        self,
        scope: MemoryScope,
        *,
        expected_revision: int,
    ) -> MemoryMutationResult:
        """Strict management alias for clearing one exact document revision."""
        return await self._clear_items_at_revision(
            scope,
            expected_revision=expected_revision,
            reload_conflict_metadata=True,
        )

    async def _clear_items_at_revision(
        self,
        scope: MemoryScope,
        *,
        expected_revision: int,
        reload_conflict_metadata: bool,
    ) -> MemoryMutationResult:
        if type(expected_revision) is not int or expected_revision < 0:
            raise ValueError("expected_revision must be a non-negative integer")

        async with self._get_mutation_lock(scope):
            try:
                document = await self._load_document(scope, force_reload=True)
                if document.revision != expected_revision:
                    return self._mutation_failure(
                        action=MemoryAction.CLEAR,
                        reason=MemoryReasonCode.REVISION_CONFLICT,
                        revision=document.revision,
                        item_count=len(document.items),
                    )
                if not document.items:
                    return MemoryMutationResult(
                        operation=MemoryOperationResult(
                            status=MemoryOperationStatus.SUCCESS,
                            action=MemoryAction.CLEAR,
                            revision=document.revision,
                        ),
                        changed=False,
                        item_count=0,
                    )

                desired = self._next_document(document, items=[])
                persisted = await self._store.save(
                    scope,
                    desired,
                    expected_revision=expected_revision,
                    backup_mode=MemoryBackupMode.REPLACEMENT,
                )
                self._cache_persisted(scope, persisted)
                return MemoryMutationResult(
                    operation=MemoryOperationResult(
                        status=MemoryOperationStatus.SUCCESS,
                        action=MemoryAction.CLEAR,
                        revision=persisted.revision,
                    ),
                    changed=True,
                    item_count=0,
                )
            except MemoryStoreRevisionConflictError:
                if reload_conflict_metadata:
                    return await self._reload_revision_conflict(
                        scope,
                        MemoryAction.CLEAR,
                    )
                self._documents.pop(scope, None)
                return self._mutation_failure(
                    action=MemoryAction.CLEAR,
                    reason=MemoryReasonCode.REVISION_CONFLICT,
                )
            except MemoryStoreError:
                return self._mutation_failure(
                    action=MemoryAction.CLEAR,
                    reason=MemoryReasonCode.STORAGE_FAILURE,
                )

    async def _upsert_loaded_document(
        self,
        scope: MemoryScope,
        document: MemoryDocument,
        *,
        category: MemoryCategory,
        value: str,
        source: MemorySource,
        max_items: int,
    ) -> MemoryMutationResult:
        match_value = normalize_memory_match_text(value)
        if category == MemoryCategory.PREFERRED_ADDRESS:
            existing = next(
                (
                    item
                    for item in document.items
                    if item.category == MemoryCategory.PREFERRED_ADDRESS
                ),
                None,
            )
            if (
                existing is not None
                and normalize_memory_match_text(existing.value) == match_value
            ):
                return self._idempotent_result(document, existing)
            if existing is not None:
                now = self._clock()
                replacement = MemoryItem(
                    id=existing.id,
                    category=existing.category,
                    key=existing.key,
                    value=value,
                    source=source,
                    created_at=existing.created_at,
                    updated_at=now,
                )
                desired = self._next_document(
                    document,
                    items=[
                        replacement if item.id == existing.id else item
                        for item in document.items
                    ],
                    now=now,
                )
                persisted = await self._store.save(
                    scope,
                    desired,
                    expected_revision=document.revision,
                    backup_mode=MemoryBackupMode.REPLACEMENT,
                )
                self._cache_persisted(scope, persisted)
                return MemoryMutationResult(
                    operation=MemoryOperationResult(
                        status=MemoryOperationStatus.SUCCESS,
                        action=MemoryAction.UPDATE,
                        memory_id=existing.id,
                        revision=persisted.revision,
                    ),
                    changed=True,
                    item_count=len(persisted.items),
                )
        else:
            existing = next(
                (
                    item
                    for item in document.items
                    if item.category != MemoryCategory.PREFERRED_ADDRESS
                    and normalize_memory_match_text(item.value) == match_value
                ),
                None,
            )
            if existing is not None:
                return self._idempotent_result(document, existing)

        if len(document.items) >= max_items:
            return self._mutation_failure(
                action=MemoryAction.CREATE,
                reason=MemoryReasonCode.CAPACITY_REACHED,
                revision=document.revision,
                item_count=len(document.items),
            )

        now = self._clock()
        item = MemoryItem(
            id=self._id_factory(),
            category=category,
            key=self._build_key(category, match_value),
            value=value,
            source=source,
            created_at=now,
            updated_at=now,
        )
        desired = self._next_document(
            document,
            items=[*document.items, item],
            now=now,
        )
        persisted = await self._store.save(
            scope,
            desired,
            expected_revision=document.revision,
            backup_mode=MemoryBackupMode.PREVIOUS,
        )
        self._cache_persisted(scope, persisted)
        return MemoryMutationResult(
            operation=MemoryOperationResult(
                status=MemoryOperationStatus.SUCCESS,
                action=MemoryAction.CREATE,
                memory_id=item.id,
                revision=persisted.revision,
            ),
            changed=True,
            item_count=len(persisted.items),
        )

    async def _load_document(
        self,
        scope: MemoryScope,
        *,
        force_reload: bool = False,
    ) -> MemoryDocument:
        if force_reload:
            self.invalidate(scope)
        if scope in self._unavailable_scopes:
            raise MemoryStoreReadError("Memory scope is unavailable")

        document = self._documents.get(scope)
        if document is not None:
            return document
        try:
            document = await self._store.load(scope)
        except MemoryStoreError:
            self.mark_unavailable(scope)
            raise
        self._documents[scope] = document
        return document

    def _get_mutation_lock(self, scope: MemoryScope) -> asyncio.Lock:
        return self._mutation_locks.setdefault(scope, asyncio.Lock())

    def _next_document(
        self,
        document: MemoryDocument,
        *,
        items: list[MemoryItem],
        now: datetime | None = None,
    ) -> MemoryDocument:
        timestamp = now or self._clock()
        return MemoryDocument(
            schema_version=document.schema_version,
            profile_id=document.profile_id,
            character_conf_uid=document.character_conf_uid,
            revision=document.revision + 1,
            updated_at=timestamp,
            items=items,
        )

    @staticmethod
    def _build_key(category: MemoryCategory, match_value: str) -> str:
        if category == MemoryCategory.PREFERRED_ADDRESS:
            return MemoryCategory.PREFERRED_ADDRESS.value
        digest = hashlib.sha256(match_value.encode("utf-8")).hexdigest()[:24]
        return f"{category.value}:{digest}"

    @staticmethod
    def _infer_upsert_action(
        document: MemoryDocument,
        category: MemoryCategory,
        value: str,
    ) -> MemoryAction:
        match_value = normalize_memory_match_text(value)
        if category == MemoryCategory.PREFERRED_ADDRESS:
            if any(
                item.category == MemoryCategory.PREFERRED_ADDRESS
                for item in document.items
            ):
                return MemoryAction.UPDATE
            return MemoryAction.CREATE
        if any(
            item.category != MemoryCategory.PREFERRED_ADDRESS
            and normalize_memory_match_text(item.value) == match_value
            for item in document.items
        ):
            return MemoryAction.UPDATE
        return MemoryAction.CREATE

    @staticmethod
    def _find_delete_candidates(
        document: MemoryDocument,
        normalized_target: str,
    ) -> list[MemoryItem]:
        normalized_items = [
            (item, normalize_memory_match_text(item.value)) for item in document.items
        ]
        exact = [
            item
            for item, normalized in normalized_items
            if normalized == normalized_target
        ]
        if exact:
            return exact
        return [
            item
            for item, normalized in normalized_items
            if normalized_target in normalized
        ]

    @staticmethod
    def _find_item_by_id(
        document: MemoryDocument,
        memory_id: str,
    ) -> MemoryItem | None:
        return next((item for item in document.items if item.id == memory_id), None)

    @staticmethod
    def _find_management_duplicate(
        document: MemoryDocument,
        *,
        memory_id: str,
        category: MemoryCategory,
        match_value: str,
    ) -> MemoryItem | None:
        for item in document.items:
            if item.id == memory_id:
                continue
            if category == MemoryCategory.PREFERRED_ADDRESS:
                if item.category == MemoryCategory.PREFERRED_ADDRESS:
                    return item
                continue
            if item.category == MemoryCategory.PREFERRED_ADDRESS:
                continue
            if normalize_memory_match_text(item.value) == match_value:
                return item
        return None

    @staticmethod
    def _validate_memory_id(memory_id: str) -> None:
        if (
            not isinstance(memory_id, str)
            or re.fullmatch(MEMORY_ID_PATTERN, memory_id) is None
        ):
            raise ValueError("memory_id must be a lowercase UUID hex string")

    @staticmethod
    def _validate_expected_revision(expected_revision: int) -> None:
        if type(expected_revision) is not int or expected_revision < 0:
            raise ValueError("expected_revision must be a non-negative integer")

    @classmethod
    def _revision_conflict_result(
        cls,
        action: MemoryAction,
        document: MemoryDocument,
    ) -> MemoryMutationResult:
        return cls._mutation_failure(
            action=action,
            reason=MemoryReasonCode.REVISION_CONFLICT,
            revision=document.revision,
            item_count=len(document.items),
        )

    async def _reload_revision_conflict(
        self,
        scope: MemoryScope,
        action: MemoryAction,
    ) -> MemoryMutationResult:
        self.invalidate(scope)
        try:
            document = await self._load_document(scope, force_reload=True)
        except MemoryStoreError:
            return self._mutation_failure(
                action=action,
                reason=MemoryReasonCode.STORAGE_FAILURE,
            )
        return self._revision_conflict_result(action, document)

    @staticmethod
    def _idempotent_result(
        document: MemoryDocument,
        item: MemoryItem,
    ) -> MemoryMutationResult:
        return MemoryMutationResult(
            operation=MemoryOperationResult(
                status=MemoryOperationStatus.SUCCESS,
                action=MemoryAction.UPDATE,
                memory_id=item.id,
                revision=document.revision,
            ),
            changed=False,
            item_count=len(document.items),
        )

    def _cache_persisted(
        self,
        scope: MemoryScope,
        document: MemoryDocument,
    ) -> None:
        self._documents[scope] = document
        self._unavailable_scopes.discard(scope)

    @staticmethod
    def _mutation_failure(
        *,
        action: MemoryAction,
        reason: MemoryReasonCode,
        status: MemoryOperationStatus = MemoryOperationStatus.REJECTED,
        revision: int | None = None,
        item_count: int = 0,
    ) -> MemoryMutationResult:
        if reason in {
            MemoryReasonCode.REVISION_CONFLICT,
            MemoryReasonCode.STORAGE_FAILURE,
        }:
            status = MemoryOperationStatus.FAILED
        return MemoryMutationResult(
            operation=MemoryOperationResult(
                status=status,
                action=action,
                reason_code=reason,
                revision=revision,
            ),
            changed=False,
            item_count=item_count,
        )

    @staticmethod
    def _list_failure(reason: MemoryReasonCode) -> MemoryListResult:
        return MemoryListResult(
            operation=MemoryOperationResult(
                status=MemoryOperationStatus.FAILED,
                action=MemoryAction.LIST,
                reason_code=reason,
            )
        )

    def invalidate(self, scope: MemoryScope) -> None:
        """Drop cached state and allow a future load attempt for one scope."""
        self._documents.pop(scope, None)
        self._unavailable_scopes.discard(scope)

    def mark_unavailable(self, scope: MemoryScope) -> None:
        """Fail closed for a damaged scope until it is explicitly invalidated."""
        self._documents.pop(scope, None)
        self._unavailable_scopes.add(scope)

    def is_unavailable(self, scope: MemoryScope) -> bool:
        """Return whether a scope is disabled for the current process lifetime."""
        return scope in self._unavailable_scopes
