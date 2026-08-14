from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from open_llm_vtuber.memory import (
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
)


NOW = datetime(2026, 8, 12, 8, 0, tzinfo=timezone.utc)
MEMORY_ID = "9b88f7a60a3c49c0a4fe87a044264d22"


def make_item(**overrides) -> MemoryItem:
    data = {
        "id": MEMORY_ID,
        "category": MemoryCategory.PREFERENCE,
        "key": "preference:drink_sweetness",
        "value": "饮料不要太甜",
        "source": MemorySource.EXPLICIT_USER,
        "created_at": NOW,
        "updated_at": NOW,
    }
    data.update(overrides)
    return MemoryItem(**data)


def test_memory_scope_is_unicode_safe_immutable_and_hashable() -> None:
    scope = MemoryScope(profile_id="local_default", character_conf_uid="爱莉希雅_001")

    assert hash(scope)
    with pytest.raises(ValidationError):
        scope.profile_id = "another_profile"


@pytest.mark.parametrize(
    "character_conf_uid",
    ["", ".", "..", "../elysia", "elysia/name", "CON", "name."],
)
def test_memory_scope_rejects_unsafe_character_identifiers(
    character_conf_uid: str,
) -> None:
    with pytest.raises(ValidationError):
        MemoryScope(profile_id="local_default", character_conf_uid=character_conf_uid)


def test_memory_item_accepts_each_frozen_category() -> None:
    address = make_item(
        category=MemoryCategory.PREFERRED_ADDRESS,
        key="preferred_address",
        value="阿源",
    )
    preference = make_item()
    fact = make_item(
        id="19f340279ac648ec813a8dbe25ed72af",
        category=MemoryCategory.IMPORTANT_FACT,
        key="important_fact:weekend_work",
        value="周末通常要加班",
    )

    assert {address.category, preference.category, fact.category} == set(MemoryCategory)


def test_memory_item_normalizes_outer_whitespace() -> None:
    item = make_item(value="  饮料不要太甜  ")

    assert item.value == "饮料不要太甜"


@pytest.mark.parametrize(
    ("field_name", "invalid_value"),
    [
        ("id", "NOT_A_UUID"),
        ("category", "unknown"),
        ("source", "inferred_by_model"),
        ("key", "User supplied key"),
        ("value", "contains\nnewline"),
    ],
)
def test_memory_item_rejects_invalid_contract_values(
    field_name: str, invalid_value: str
) -> None:
    with pytest.raises(ValidationError):
        make_item(**{field_name: invalid_value})


def test_preferred_address_requires_its_fixed_key() -> None:
    with pytest.raises(ValidationError):
        make_item(
            category=MemoryCategory.PREFERRED_ADDRESS,
            key="preference:wrong",
            value="阿源",
        )


def test_non_address_item_cannot_claim_the_address_key() -> None:
    with pytest.raises(ValidationError):
        make_item(key="preferred_address")


def test_memory_item_requires_utc_and_ordered_timestamps() -> None:
    with pytest.raises(ValidationError):
        make_item(created_at=NOW.replace(tzinfo=None))
    with pytest.raises(ValidationError):
        make_item(created_at=NOW.astimezone(timezone(timedelta(hours=8))))
    with pytest.raises(ValidationError):
        make_item(created_at=NOW, updated_at=NOW - timedelta(seconds=1))


def test_memory_document_serializes_as_versioned_json_contract() -> None:
    document = MemoryDocument(
        profile_id="local_default",
        character_conf_uid="elysia_mvp_001",
        revision=3,
        updated_at=NOW,
        items=[make_item()],
    )

    payload = document.model_dump(mode="json")
    assert payload["schema_version"] == 1
    assert payload["items"][0]["category"] == "preference"
    assert payload["items"][0]["source"] == "explicit_user"
    assert payload["updated_at"].endswith("Z")


def test_memory_document_rejects_duplicate_ids() -> None:
    with pytest.raises(ValidationError):
        MemoryDocument(
            profile_id="local_default",
            character_conf_uid="elysia_mvp_001",
            updated_at=NOW,
            items=[make_item(), make_item()],
        )


def test_memory_document_allows_only_one_preferred_address() -> None:
    first = make_item(
        category=MemoryCategory.PREFERRED_ADDRESS,
        key="preferred_address",
        value="阿源",
    )
    second = make_item(
        id="19f340279ac648ec813a8dbe25ed72af",
        category=MemoryCategory.PREFERRED_ADDRESS,
        key="preferred_address",
        value="舰长",
    )

    with pytest.raises(ValidationError):
        MemoryDocument(
            profile_id="local_default",
            character_conf_uid="elysia_mvp_001",
            updated_at=NOW,
            items=[first, second],
        )


def test_memory_document_rejects_unknown_schema_or_stale_timestamp() -> None:
    with pytest.raises(ValidationError):
        MemoryDocument(
            schema_version=2,
            profile_id="local_default",
            character_conf_uid="elysia_mvp_001",
            updated_at=NOW,
        )
    with pytest.raises(ValidationError):
        MemoryDocument(
            profile_id="local_default",
            character_conf_uid="elysia_mvp_001",
            updated_at=NOW - timedelta(seconds=1),
            items=[make_item()],
        )


def test_successful_item_result_requires_id_and_no_reason() -> None:
    result = MemoryOperationResult(
        status=MemoryOperationStatus.SUCCESS,
        action=MemoryAction.CREATE,
        memory_id=MEMORY_ID,
        revision=1,
    )

    assert result.reason_code is None
    with pytest.raises(ValidationError):
        MemoryOperationResult(
            status=MemoryOperationStatus.SUCCESS,
            action=MemoryAction.UPDATE,
        )
    with pytest.raises(ValidationError):
        MemoryOperationResult(
            status=MemoryOperationStatus.SUCCESS,
            action=MemoryAction.DELETE,
            memory_id=MEMORY_ID,
            reason_code=MemoryReasonCode.NOT_FOUND,
        )


def test_unsuccessful_result_requires_stable_reason_code() -> None:
    result = MemoryOperationResult(
        status=MemoryOperationStatus.REJECTED,
        action=MemoryAction.CREATE,
        reason_code=MemoryReasonCode.MEMORY_DISABLED,
    )

    assert result.reason_code == MemoryReasonCode.MEMORY_DISABLED
    with pytest.raises(ValidationError):
        MemoryOperationResult(
            status=MemoryOperationStatus.FAILED,
            action=MemoryAction.CLEAR,
        )


def test_mutation_result_separates_idempotence_from_failure() -> None:
    unchanged = MemoryMutationResult(
        operation=MemoryOperationResult(
            status=MemoryOperationStatus.SUCCESS,
            action=MemoryAction.UPDATE,
            memory_id=MEMORY_ID,
            revision=3,
        ),
        changed=False,
        item_count=1,
    )

    assert not unchanged.changed
    with pytest.raises(ValidationError):
        MemoryMutationResult(
            operation=MemoryOperationResult(
                status=MemoryOperationStatus.REJECTED,
                action=MemoryAction.DELETE,
                reason_code=MemoryReasonCode.NOT_FOUND,
            ),
            changed=True,
            item_count=1,
        )
    with pytest.raises(ValidationError):
        MemoryMutationResult(
            operation=MemoryOperationResult(
                status=MemoryOperationStatus.SUCCESS,
                action=MemoryAction.LIST,
                revision=3,
            ),
            changed=False,
            item_count=1,
        )


def test_list_result_requires_list_operation_and_hides_items_on_failure() -> None:
    item = make_item()
    listed = MemoryListResult(
        operation=MemoryOperationResult(
            status=MemoryOperationStatus.SUCCESS,
            action=MemoryAction.LIST,
            revision=1,
        ),
        items=(item,),
    )

    assert listed.items == (item,)
    with pytest.raises(ValidationError):
        MemoryListResult(
            operation=MemoryOperationResult(
                status=MemoryOperationStatus.FAILED,
                action=MemoryAction.LIST,
                reason_code=MemoryReasonCode.STORAGE_FAILURE,
            ),
            items=(item,),
        )
