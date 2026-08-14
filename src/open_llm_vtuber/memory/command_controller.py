"""Connection-local orchestration for explicit persistent-memory commands."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

from .command_parser import MemoryCommandParser
from .command_types import (
    MemoryCommand,
    MemoryCommandKind,
    MemoryCommandParseReason,
    MemoryCommandParseStatus,
    PendingMemoryConfirmation,
)
from .feedback import (
    DEFAULT_LIST_FEEDBACK_CHARS,
    DEFAULT_LIST_FEEDBACK_ITEMS,
    feedback_expression,
    feedback_for_list,
    feedback_for_parse_failure,
    feedback_for_reason,
    feedback_for_success,
)
from .service import PersistentMemoryService
from .types import (
    MemoryAction,
    MemoryCategory,
    MemoryMutationResult,
    MemoryOperationStatus,
    MemoryReasonCode,
    MemoryScope,
    MemorySource,
)

DEFAULT_CLEAR_CONFIRMATION_SECONDS = 60

_T = TypeVar("_T")


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class MemoryCommandExecutionResult:
    """Internal command result with a sanitized WebSocket representation."""

    handled: bool
    command_kind: MemoryCommandKind | None = None
    status: MemoryOperationStatus | None = None
    action: MemoryAction | None = None
    reason_code: MemoryReasonCode | None = None
    parse_reason: MemoryCommandParseReason | None = None
    memory_id: str | None = None
    revision: int | None = None
    changed: bool | None = None
    item_count: int | None = None
    feedback_text: str | None = None
    expression: str | None = None

    def __post_init__(self) -> None:
        details = (
            self.command_kind,
            self.status,
            self.action,
            self.reason_code,
            self.parse_reason,
            self.memory_id,
            self.revision,
            self.changed,
            self.item_count,
            self.feedback_text,
            self.expression,
        )
        if not self.handled and any(value is not None for value in details):
            raise ValueError("ordinary conversation results cannot include details")
        if self.handled and (self.status is None or not self.feedback_text):
            raise ValueError("handled command results require status and feedback")
        if self.status == MemoryOperationStatus.SUCCESS:
            if self.reason_code is not None or self.parse_reason is not None:
                raise ValueError("successful command results cannot include a reason")
        elif self.handled and self.reason_code is None:
            raise ValueError("unsuccessful command results require a reason code")
        if self.changed and self.status != MemoryOperationStatus.SUCCESS:
            raise ValueError("only successful commands can report a change")

    @classmethod
    def not_command(cls) -> "MemoryCommandExecutionResult":
        return cls(handled=False)

    def to_websocket_payload(self) -> dict[str, Any]:
        """Build an event that never contains command text or memory values."""
        if not self.handled:
            raise ValueError("ordinary conversation has no memory operation payload")
        return {
            "type": "memory-operation-result",
            "command": self.command_kind.value if self.command_kind else None,
            "status": self.status.value if self.status else None,
            "action": self.action.value if self.action else None,
            "reason_code": self.reason_code.value if self.reason_code else None,
            "parse_reason": self.parse_reason.value if self.parse_reason else None,
            "changed": self.changed,
            "item_count": self.item_count,
            "revision": self.revision,
            "memory_id": self.memory_id,
        }


class ExplicitMemoryCommandController:
    """Parse and execute commands while owning one connection's confirmation."""

    def __init__(
        self,
        *,
        parser: MemoryCommandParser | None = None,
        clock: Callable[[], datetime] = _utc_now,
        confirmation_seconds: int = DEFAULT_CLEAR_CONFIRMATION_SECONDS,
        list_feedback_items: int = DEFAULT_LIST_FEEDBACK_ITEMS,
        list_feedback_chars: int = DEFAULT_LIST_FEEDBACK_CHARS,
    ) -> None:
        if confirmation_seconds <= 0:
            raise ValueError("confirmation_seconds must be positive")
        if list_feedback_items <= 0 or list_feedback_chars <= 0:
            raise ValueError("list feedback limits must be positive")
        self._parser = parser or MemoryCommandParser()
        self._clock = clock
        self._confirmation_seconds = confirmation_seconds
        self._list_feedback_items = list_feedback_items
        self._list_feedback_chars = list_feedback_chars
        self._pending: PendingMemoryConfirmation | None = None
        self._lock = asyncio.Lock()

    @property
    def pending_confirmation(self) -> PendingMemoryConfirmation | None:
        return self._pending

    def clear_pending(self) -> None:
        """Drop connection-local confirmation during switches or disconnects."""
        self._pending = None

    async def handle(
        self,
        text: str,
        *,
        service: PersistentMemoryService | None,
        scope: MemoryScope | None,
        enabled: bool,
        explicit_capture: bool,
        eligible: bool,
        max_items: int,
        max_item_chars: int,
    ) -> MemoryCommandExecutionResult:
        """Handle one final input text or return an ordinary-chat sentinel."""
        parsed = self._parser.parse(text)
        async with self._lock:
            if parsed.status == MemoryCommandParseStatus.NOT_COMMAND:
                self._pending = None
                return MemoryCommandExecutionResult.not_command()
            if parsed.status == MemoryCommandParseStatus.INVALID:
                self._pending = None
                assert parsed.reason is not None
                return self._parse_failure(parsed.reason)

            command = parsed.command
            assert command is not None
            if command.kind not in {
                MemoryCommandKind.CONFIRM_CLEAR,
                MemoryCommandKind.CANCEL_CLEAR,
            }:
                self._pending = None

            if not enabled or not explicit_capture or not eligible:
                self._pending = None
                return self._reason_failure(
                    command,
                    MemoryReasonCode.MEMORY_DISABLED,
                )
            if scope is None:
                self._pending = None
                return self._reason_failure(command, MemoryReasonCode.INVALID_SCOPE)
            if service is None:
                self._pending = None
                return self._reason_failure(command, MemoryReasonCode.STORAGE_FAILURE)

            if command.kind == MemoryCommandKind.SAVE:
                return await self._save(
                    command,
                    service=service,
                    scope=scope,
                    max_items=max_items,
                    max_item_chars=max_item_chars,
                )
            if command.kind == MemoryCommandKind.LIST:
                return await self._list(command, service=service, scope=scope)
            if command.kind == MemoryCommandKind.DELETE:
                return await self._delete(command, service=service, scope=scope)
            if command.kind == MemoryCommandKind.CLEAR:
                return await self._request_clear(command, service=service, scope=scope)
            if command.kind == MemoryCommandKind.CONFIRM_CLEAR:
                return await self._confirm_clear(
                    command,
                    service=service,
                    scope=scope,
                )
            return self._cancel_clear(command, scope=scope)

    async def _save(
        self,
        command: MemoryCommand,
        *,
        service: PersistentMemoryService,
        scope: MemoryScope,
        max_items: int,
        max_item_chars: int,
    ) -> MemoryCommandExecutionResult:
        assert command.category is not None and command.value is not None
        result = await self._finish_mutation(
            service.upsert_item(
                scope,
                category=command.category,
                value=command.value,
                source=MemorySource.EXPLICIT_USER,
                max_items=max_items,
                max_item_chars=max_item_chars,
            )
        )
        return self._from_mutation(command, result, category=command.category)

    async def _list(
        self,
        command: MemoryCommand,
        *,
        service: PersistentMemoryService,
        scope: MemoryScope,
    ) -> MemoryCommandExecutionResult:
        result = await service.list_items(scope, force_reload=True)
        operation = result.operation
        if operation.status != MemoryOperationStatus.SUCCESS:
            return self._reason_failure(
                command,
                operation.reason_code or MemoryReasonCode.STORAGE_FAILURE,
                action=MemoryAction.LIST,
                status=operation.status,
                revision=operation.revision,
            )
        return MemoryCommandExecutionResult(
            handled=True,
            command_kind=command.kind,
            status=operation.status,
            action=MemoryAction.LIST,
            revision=operation.revision,
            changed=False,
            item_count=len(result.items),
            feedback_text=feedback_for_list(
                result.items,
                max_items=self._list_feedback_items,
                max_chars=self._list_feedback_chars,
            ),
            expression=feedback_expression(success=True),
        )

    async def _delete(
        self,
        command: MemoryCommand,
        *,
        service: PersistentMemoryService,
        scope: MemoryScope,
    ) -> MemoryCommandExecutionResult:
        assert command.target is not None
        result = await self._finish_mutation(
            service.delete_item(scope, target=command.target)
        )
        return self._from_mutation(command, result)

    async def _request_clear(
        self,
        command: MemoryCommand,
        *,
        service: PersistentMemoryService,
        scope: MemoryScope,
    ) -> MemoryCommandExecutionResult:
        result = await service.list_items(scope, force_reload=True)
        operation = result.operation
        if operation.status != MemoryOperationStatus.SUCCESS:
            return self._reason_failure(
                command,
                operation.reason_code or MemoryReasonCode.STORAGE_FAILURE,
                action=MemoryAction.CLEAR,
                status=operation.status,
                revision=operation.revision,
            )
        if not result.items:
            return MemoryCommandExecutionResult(
                handled=True,
                command_kind=command.kind,
                status=MemoryOperationStatus.SUCCESS,
                action=MemoryAction.CLEAR,
                revision=operation.revision,
                changed=False,
                item_count=0,
                feedback_text=feedback_for_success(
                    action=MemoryAction.CLEAR,
                    changed=False,
                ),
                expression=feedback_expression(success=True),
            )

        revision = operation.revision
        if revision is None:
            return self._reason_failure(command, MemoryReasonCode.STORAGE_FAILURE)
        self._pending = PendingMemoryConfirmation(
            scope=scope,
            expected_revision=revision,
            expires_at=self._now() + timedelta(seconds=self._confirmation_seconds),
        )
        return self._reason_failure(
            command,
            MemoryReasonCode.CONFIRMATION_REQUIRED,
            action=MemoryAction.CLEAR,
            status=MemoryOperationStatus.REJECTED,
            revision=revision,
            item_count=len(result.items),
        )

    async def _confirm_clear(
        self,
        command: MemoryCommand,
        *,
        service: PersistentMemoryService,
        scope: MemoryScope,
    ) -> MemoryCommandExecutionResult:
        pending = self._pending
        self._pending = None
        if pending is None or pending.scope != scope:
            return self._reason_failure(
                command,
                MemoryReasonCode.CONFIRMATION_NOT_PENDING,
                action=MemoryAction.CLEAR,
            )
        if self._now() >= pending.expires_at:
            return self._reason_failure(
                command,
                MemoryReasonCode.CONFIRMATION_EXPIRED,
                action=MemoryAction.CLEAR,
            )
        result = await self._finish_mutation(
            service.clear_items(
                scope,
                expected_revision=pending.expected_revision,
            )
        )
        return self._from_mutation(command, result)

    def _cancel_clear(
        self,
        command: MemoryCommand,
        *,
        scope: MemoryScope,
    ) -> MemoryCommandExecutionResult:
        pending = self._pending
        self._pending = None
        if pending is None or pending.scope != scope:
            return self._reason_failure(
                command,
                MemoryReasonCode.CONFIRMATION_NOT_PENDING,
                action=MemoryAction.CLEAR,
            )
        if self._now() >= pending.expires_at:
            return self._reason_failure(
                command,
                MemoryReasonCode.CONFIRMATION_EXPIRED,
                action=MemoryAction.CLEAR,
            )
        return MemoryCommandExecutionResult(
            handled=True,
            command_kind=command.kind,
            status=MemoryOperationStatus.SUCCESS,
            action=MemoryAction.CLEAR,
            revision=pending.expected_revision,
            changed=False,
            feedback_text="好的，我取消了清空操作。",
            expression=feedback_expression(success=True),
        )

    async def _finish_mutation(
        self,
        operation: Awaitable[MemoryMutationResult],
    ) -> MemoryMutationResult:
        """Delay cancellation until a potentially committed write is reconciled."""
        task = asyncio.create_task(operation)
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError as cancellation:
            while not task.done():
                try:
                    await asyncio.shield(task)
                except asyncio.CancelledError:
                    continue
                except Exception:
                    break
            if task.done() and not task.cancelled():
                task.exception()
            raise cancellation

    def _from_mutation(
        self,
        command: MemoryCommand,
        result: MemoryMutationResult,
        *,
        category: MemoryCategory | None = None,
    ) -> MemoryCommandExecutionResult:
        operation = result.operation
        if operation.status != MemoryOperationStatus.SUCCESS:
            return self._reason_failure(
                command,
                operation.reason_code or MemoryReasonCode.STORAGE_FAILURE,
                action=operation.action,
                status=operation.status,
                revision=operation.revision,
                item_count=result.item_count,
            )
        return MemoryCommandExecutionResult(
            handled=True,
            command_kind=command.kind,
            status=operation.status,
            action=operation.action,
            memory_id=operation.memory_id,
            revision=operation.revision,
            changed=result.changed,
            item_count=result.item_count,
            feedback_text=feedback_for_success(
                action=operation.action,
                changed=result.changed,
                category=category,
            ),
            expression=feedback_expression(success=True),
        )

    def _parse_failure(
        self,
        reason: MemoryCommandParseReason,
    ) -> MemoryCommandExecutionResult:
        return MemoryCommandExecutionResult(
            handled=True,
            status=MemoryOperationStatus.REJECTED,
            reason_code=MemoryReasonCode.INVALID_VALUE,
            parse_reason=reason,
            changed=False,
            feedback_text=feedback_for_parse_failure(reason),
            expression=feedback_expression(success=False),
        )

    def _reason_failure(
        self,
        command: MemoryCommand,
        reason: MemoryReasonCode,
        *,
        action: MemoryAction | None = None,
        status: MemoryOperationStatus | None = None,
        revision: int | None = None,
        item_count: int | None = None,
    ) -> MemoryCommandExecutionResult:
        resolved_action = action or self._command_action(command)
        resolved_status = status or (
            MemoryOperationStatus.AMBIGUOUS
            if reason == MemoryReasonCode.AMBIGUOUS_MATCH
            else MemoryOperationStatus.FAILED
            if reason
            in {
                MemoryReasonCode.REVISION_CONFLICT,
                MemoryReasonCode.STORAGE_FAILURE,
            }
            else MemoryOperationStatus.REJECTED
        )
        return MemoryCommandExecutionResult(
            handled=True,
            command_kind=command.kind,
            status=resolved_status,
            action=resolved_action,
            reason_code=reason,
            revision=revision,
            changed=False,
            item_count=item_count,
            feedback_text=feedback_for_reason(reason),
            expression=feedback_expression(
                success=False,
                requires_confirmation=(
                    reason == MemoryReasonCode.CONFIRMATION_REQUIRED
                ),
            ),
        )

    def _now(self) -> datetime:
        now = self._clock()
        if now.tzinfo is None or now.utcoffset() != timedelta(0):
            raise ValueError("memory command clock must return a UTC datetime")
        return now

    @staticmethod
    def _command_action(command: MemoryCommand) -> MemoryAction:
        if command.kind == MemoryCommandKind.SAVE:
            return MemoryAction.CREATE
        if command.kind == MemoryCommandKind.LIST:
            return MemoryAction.LIST
        if command.kind == MemoryCommandKind.DELETE:
            return MemoryAction.DELETE
        return MemoryAction.CLEAR
