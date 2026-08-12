import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, Mock

from open_llm_vtuber.memory import (
    MemoryCategory,
    MemoryDocument,
    MemoryItem,
    MemoryScope,
    MemorySource,
    PersistentMemoryService,
)


NOW = datetime(2026, 8, 12, 8, 0, tzinfo=timezone.utc)
SCOPE = MemoryScope(
    profile_id="local_default",
    character_conf_uid="elysia_mvp_001",
)
OTHER_SCOPE = MemoryScope(
    profile_id="local_default",
    character_conf_uid="another_character",
)


def make_document(scope: MemoryScope, value: str = "喜欢花") -> MemoryDocument:
    item = MemoryItem(
        id="10000000000000000000000000000000",
        category=MemoryCategory.PREFERENCE,
        key="preference:test",
        value=value,
        source=MemorySource.EXPLICIT_USER,
        created_at=NOW,
        updated_at=NOW,
    )
    return MemoryDocument(
        profile_id=scope.profile_id,
        character_conf_uid=scope.character_conf_uid,
        revision=1,
        updated_at=NOW,
        items=[item],
    )


def test_service_loads_once_then_renders_the_cached_document() -> None:
    document = make_document(SCOPE)
    store = Mock()
    store.load = AsyncMock(return_value=document)
    renderer = Mock()
    renderer.render.return_value = "safe context"
    service = PersistentMemoryService(store=store, renderer=renderer)

    first = asyncio.run(
        service.render_context(
            SCOPE,
            max_item_chars=160,
            max_context_chars=800,
        )
    )
    second = asyncio.run(
        service.render_context(
            SCOPE,
            max_item_chars=120,
            max_context_chars=600,
        )
    )

    assert first == second == "safe context"
    store.load.assert_awaited_once_with(SCOPE)
    assert renderer.render.call_count == 2
    renderer.render.assert_called_with(
        document,
        max_item_chars=120,
        max_context_chars=600,
    )


def test_force_reload_replaces_the_cached_document() -> None:
    first_document = make_document(SCOPE, "第一版")
    second_document = make_document(SCOPE, "第二版")
    store = Mock()
    store.load = AsyncMock(side_effect=[first_document, second_document])
    renderer = Mock()
    renderer.render.side_effect = lambda document, **_: document.items[0].value
    service = PersistentMemoryService(store=store, renderer=renderer)

    first = asyncio.run(
        service.render_context(
            SCOPE,
            max_item_chars=160,
            max_context_chars=800,
        )
    )
    second = asyncio.run(
        service.render_context(
            SCOPE,
            max_item_chars=160,
            max_context_chars=800,
            force_reload=True,
        )
    )

    assert (first, second) == ("第一版", "第二版")
    assert store.load.await_count == 2


def test_different_scopes_are_cached_independently() -> None:
    store = Mock()
    store.load = AsyncMock(
        side_effect=[make_document(SCOPE), make_document(OTHER_SCOPE)]
    )
    renderer = Mock()
    renderer.render.return_value = "context"
    service = PersistentMemoryService(store=store, renderer=renderer)

    for scope in (SCOPE, OTHER_SCOPE, SCOPE, OTHER_SCOPE):
        asyncio.run(
            service.render_context(
                scope,
                max_item_chars=160,
                max_context_chars=800,
            )
        )

    assert store.load.await_count == 2
    assert store.load.await_args_list[0].args == (SCOPE,)
    assert store.load.await_args_list[1].args == (OTHER_SCOPE,)


def test_invalidate_removes_cache_and_allows_reload() -> None:
    store = Mock()
    store.load = AsyncMock(return_value=make_document(SCOPE))
    renderer = Mock()
    renderer.render.return_value = "context"
    service = PersistentMemoryService(store=store, renderer=renderer)

    asyncio.run(
        service.render_context(
            SCOPE,
            max_item_chars=160,
            max_context_chars=800,
        )
    )
    service.invalidate(SCOPE)
    asyncio.run(
        service.render_context(
            SCOPE,
            max_item_chars=160,
            max_context_chars=800,
        )
    )

    assert store.load.await_count == 2


def test_unavailable_scope_fails_closed_until_invalidated() -> None:
    store = Mock()
    store.load = AsyncMock(return_value=make_document(SCOPE))
    renderer = Mock()
    renderer.render.return_value = "context"
    service = PersistentMemoryService(store=store, renderer=renderer)
    service.mark_unavailable(SCOPE)

    unavailable = asyncio.run(
        service.render_context(
            SCOPE,
            max_item_chars=160,
            max_context_chars=800,
        )
    )

    assert unavailable == ""
    assert service.is_unavailable(SCOPE)
    store.load.assert_not_awaited()
    renderer.render.assert_not_called()

    service.invalidate(SCOPE)
    available = asyncio.run(
        service.render_context(
            SCOPE,
            max_item_chars=160,
            max_context_chars=800,
        )
    )

    assert available == "context"
    assert not service.is_unavailable(SCOPE)


def test_service_caches_documents_not_rendered_context_strings() -> None:
    document = make_document(SCOPE)
    store = Mock()
    store.load = AsyncMock(return_value=document)
    renderer = Mock()
    renderer.render.side_effect = ["budget A", "budget B"]
    service = PersistentMemoryService(store=store, renderer=renderer)

    first = asyncio.run(
        service.render_context(
            SCOPE,
            max_item_chars=160,
            max_context_chars=800,
        )
    )
    second = asyncio.run(
        service.render_context(
            SCOPE,
            max_item_chars=80,
            max_context_chars=400,
        )
    )

    assert (first, second) == ("budget A", "budget B")
    store.load.assert_awaited_once()
    assert renderer.render.call_count == 2
