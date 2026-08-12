"""Read-only orchestration for persistent memory documents and contexts."""

from __future__ import annotations

from .context_renderer import MemoryContextRenderer
from .store import PersistentMemoryStore
from .types import MemoryDocument, MemoryScope


class PersistentMemoryService:
    """Share validated memory documents without retaining rendered prompts."""

    def __init__(
        self,
        store: PersistentMemoryStore,
        renderer: MemoryContextRenderer | None = None,
    ) -> None:
        self._store = store
        self._renderer = renderer or MemoryContextRenderer()
        self._documents: dict[MemoryScope, MemoryDocument] = {}
        self._unavailable_scopes: set[MemoryScope] = set()

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
