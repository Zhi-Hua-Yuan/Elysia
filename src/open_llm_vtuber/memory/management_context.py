"""Internal runtime inputs and ports for memory management.

These types are deliberately not part of the frozen WebSocket protocol. They carry
server-derived state only and must never be populated from client-controlled scope
or path fields.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from .management_types import (
    MemoryManagementReasonCode,
    MemoryManagementStatus,
)
from .service import PersistentMemoryService


@dataclass(frozen=True)
class MemoryManagementContext:
    """Server-derived state used to authorize and execute one request."""

    is_local_connection: bool
    proxy_enabled: bool
    group_active: bool
    agent_choice: str | None
    enabled: bool
    profile_id: str | None
    character_conf_uid: str | None
    max_items: int
    max_item_chars: int
    service: PersistentMemoryService | None


@dataclass(frozen=True)
class MemorySettingUpdateOutcome:
    """Content-free outcome returned by the future configuration adapter."""

    status: MemoryManagementStatus
    changed: bool
    enabled: bool
    reason_code: MemoryManagementReasonCode | None = None

    def __post_init__(self) -> None:
        if self.status == MemoryManagementStatus.SUCCESS:
            if self.reason_code is not None:
                raise ValueError("successful setting outcomes cannot have a reason")
            return

        if self.changed:
            raise ValueError("unsuccessful setting outcomes cannot report a change")
        if self.reason_code is None:
            raise ValueError("unsuccessful setting outcomes require a reason")
        if (
            self.status == MemoryManagementStatus.REJECTED
            and self.reason_code != MemoryManagementReasonCode.SETTING_CONFLICT
        ):
            raise ValueError("rejected setting outcomes require setting_conflict")
        if self.status == MemoryManagementStatus.FAILED and self.reason_code not in {
            MemoryManagementReasonCode.CONFIG_PERSIST_FAILURE,
            MemoryManagementReasonCode.INTERNAL_ERROR,
        }:
            raise ValueError("failed setting outcomes require an operational reason")


class MemorySettingUpdatePort(Protocol):
    """Persistence boundary implemented in M5.4.2.5."""

    async def update_enabled(
        self,
        *,
        expected_enabled: bool,
        enabled: bool,
    ) -> MemorySettingUpdateOutcome:
        """Persist an exact expected-state transition."""
