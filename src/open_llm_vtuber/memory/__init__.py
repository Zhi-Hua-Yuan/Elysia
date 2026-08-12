"""Contracts for Elysia's lightweight persistent memory."""

from .types import (
    MemoryAction,
    MemoryCategory,
    MemoryDocument,
    MemoryItem,
    MemoryOperationResult,
    MemoryOperationStatus,
    MemoryReasonCode,
    MemoryScope,
    MemorySource,
)
from .store import (
    MAX_MEMORY_FILE_BYTES,
    MemoryStoreError,
    MemoryStorePathError,
    MemoryStoreReadError,
    MemoryStoreRecoveryError,
    MemoryStoreRevisionConflictError,
    MemoryStoreScopeMismatchError,
    MemoryStoreWriteError,
    PersistentMemoryStore,
)
from .context_renderer import (
    CONTEXT_CLOSE,
    CONTEXT_NOTICE,
    CONTEXT_OPEN,
    MemoryContextRenderer,
    render_memory_context,
)
from .service import PersistentMemoryService

__all__ = [
    "MemoryAction",
    "MemoryCategory",
    "MemoryDocument",
    "MemoryItem",
    "MemoryOperationResult",
    "MemoryOperationStatus",
    "MemoryReasonCode",
    "MemoryScope",
    "MemorySource",
    "MAX_MEMORY_FILE_BYTES",
    "MemoryStoreError",
    "MemoryStorePathError",
    "MemoryStoreReadError",
    "MemoryStoreRecoveryError",
    "MemoryStoreRevisionConflictError",
    "MemoryStoreScopeMismatchError",
    "MemoryStoreWriteError",
    "PersistentMemoryStore",
    "CONTEXT_CLOSE",
    "CONTEXT_NOTICE",
    "CONTEXT_OPEN",
    "MemoryContextRenderer",
    "render_memory_context",
    "PersistentMemoryService",
]
