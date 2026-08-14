"""Coordinate persisted and in-process state for the M5 memory total switch.

The coordinator is the single serialization boundary for setting transitions. It
delegates disk writes to the atomic writer and synchronizes already-registered
runtime contexts without rebuilding any service or conversation engine.
"""

from __future__ import annotations

import asyncio
from typing import Protocol
from weakref import ReferenceType, ref

from loguru import logger

from .management_context import MemorySettingUpdateOutcome
from .management_types import (
    MemoryManagementReasonCode,
    MemoryManagementStatus,
)
from .setting_persistence import AtomicConfigWriteResult


class MemorySettingPersistencePort(Protocol):
    """Disk compare-and-set boundary consumed by the runtime coordinator."""

    async def update_enabled(
        self,
        *,
        expected_enabled: bool,
        enabled: bool,
    ) -> AtomicConfigWriteResult:
        """Persist an exact expected-state transition."""


class MemorySettingRuntimeTarget(Protocol):
    """Narrow runtime surface needed to synchronize the memory total switch."""

    def apply_memory_enabled_state(self, *, enabled: bool) -> None:
        """Apply only the in-memory enabled flag."""

    def clear_memory_setting_transients(self) -> None:
        """Invalidate transient memory-setting state without cancelling a turn."""


class MemorySettingRuntimeCoordinator:
    """Persist and synchronize one process-wide memory enabled state."""

    def __init__(
        self,
        *,
        writer: MemorySettingPersistencePort,
        initial_enabled: bool,
    ) -> None:
        if type(initial_enabled) is not bool:
            raise TypeError("initial_enabled must be a boolean")

        self._writer = writer
        self._runtime_enabled = initial_enabled
        self._lock = asyncio.Lock()
        self._target_refs: list[ReferenceType[MemorySettingRuntimeTarget]] = []

    @property
    def runtime_enabled(self) -> bool:
        """Return the coordinator's latest authoritative in-process state."""

        return self._runtime_enabled

    async def register_target(self, target: MemorySettingRuntimeTarget) -> None:
        """Register a runtime target and immediately converge it to current state."""

        target_ref = ref(target)
        async with self._lock:
            targets = self._alive_targets()
            already_registered = any(candidate is target for candidate in targets)
            if not already_registered:
                self._target_refs.append(target_ref)

            if self._synchronize_target(target, self._runtime_enabled):
                return

            if not already_registered:
                self._remove_target(target)
            raise RuntimeError("memory runtime target synchronization failed")

    async def unregister_target(self, target: MemorySettingRuntimeTarget) -> None:
        """Stop synchronizing a runtime target, using object identity."""

        async with self._lock:
            self._remove_target(target)

    async def update_enabled(
        self,
        *,
        expected_enabled: bool,
        enabled: bool,
    ) -> MemorySettingUpdateOutcome:
        """Persist a setting transition and converge all registered runtimes."""

        if type(expected_enabled) is not bool or type(enabled) is not bool:
            raise TypeError("expected_enabled and enabled must be booleans")

        async with self._lock:
            previous_enabled = self._runtime_enabled
            targets = self._alive_targets()
            try:
                write_result = await self._writer.update_enabled(
                    expected_enabled=expected_enabled,
                    enabled=enabled,
                )
            except Exception as error:
                logger.error(
                    "Memory setting persistence port failed unexpectedly "
                    "(exception_type={})",
                    type(error).__name__,
                )
                return _failed_outcome(
                    enabled=previous_enabled,
                    reason_code=MemoryManagementReasonCode.CONFIG_PERSIST_FAILURE,
                )

            if write_result.status == MemoryManagementStatus.FAILED:
                reason_code = write_result.reason_code
                if reason_code not in {
                    MemoryManagementReasonCode.CONFIG_PERSIST_FAILURE,
                    MemoryManagementReasonCode.INTERNAL_ERROR,
                }:
                    reason_code = MemoryManagementReasonCode.CONFIG_PERSIST_FAILURE
                reported_enabled = (
                    write_result.enabled
                    if type(write_result.enabled) is bool
                    else previous_enabled
                )
                return _failed_outcome(
                    enabled=reported_enabled,
                    reason_code=reason_code,
                )

            if type(write_result.enabled) is not bool:
                return _failed_outcome(
                    enabled=previous_enabled,
                    reason_code=MemoryManagementReasonCode.INTERNAL_ERROR,
                )

            authoritative_enabled = write_result.enabled
            self._runtime_enabled = authoritative_enabled
            synchronized = self._synchronize_targets(
                targets,
                authoritative_enabled,
            )

            if write_result.status == MemoryManagementStatus.REJECTED:
                if not synchronized:
                    return _failed_outcome(
                        enabled=authoritative_enabled,
                        reason_code=MemoryManagementReasonCode.INTERNAL_ERROR,
                    )
                return MemorySettingUpdateOutcome(
                    status=MemoryManagementStatus.REJECTED,
                    changed=False,
                    enabled=authoritative_enabled,
                    reason_code=MemoryManagementReasonCode.SETTING_CONFLICT,
                )

            if write_result.status != MemoryManagementStatus.SUCCESS:
                return _failed_outcome(
                    enabled=authoritative_enabled,
                    reason_code=MemoryManagementReasonCode.INTERNAL_ERROR,
                )

            if synchronized:
                return MemorySettingUpdateOutcome(
                    status=MemoryManagementStatus.SUCCESS,
                    changed=write_result.changed,
                    enabled=authoritative_enabled,
                )

            if not write_result.changed:
                self._synchronize_targets(targets, authoritative_enabled)
                return _failed_outcome(
                    enabled=authoritative_enabled,
                    reason_code=MemoryManagementReasonCode.INTERNAL_ERROR,
                )

            return await self._compensate_runtime_failure(
                targets=targets,
                previous_enabled=previous_enabled,
                committed_enabled=authoritative_enabled,
            )

    async def _compensate_runtime_failure(
        self,
        *,
        targets: list[MemorySettingRuntimeTarget],
        previous_enabled: bool,
        committed_enabled: bool,
    ) -> MemorySettingUpdateOutcome:
        """Best-effort rollback after disk commit but runtime sync failure."""

        self._runtime_enabled = previous_enabled
        self._synchronize_targets(targets, previous_enabled)

        rollback_result: AtomicConfigWriteResult | None
        try:
            rollback_result = await self._writer.update_enabled(
                expected_enabled=committed_enabled,
                enabled=previous_enabled,
            )
        except Exception as error:
            rollback_result = None
            logger.error(
                "Memory setting compensation persistence failed unexpectedly "
                "(exception_type={})",
                type(error).__name__,
            )

        authoritative_enabled = committed_enabled
        if rollback_result is not None and type(rollback_result.enabled) is bool:
            authoritative_enabled = rollback_result.enabled

        self._runtime_enabled = authoritative_enabled
        converged = self._synchronize_targets(targets, authoritative_enabled)
        rollback_restored_previous_state = bool(
            rollback_result is not None
            and rollback_result.status
            in {
                MemoryManagementStatus.SUCCESS,
                MemoryManagementStatus.REJECTED,
            }
            and rollback_result.enabled is previous_enabled
        )
        if not rollback_restored_previous_state or not converged:
            logger.error(
                "Memory setting runtime compensation incomplete "
                "(previous_state_restored={}, convergence_succeeded={})",
                rollback_restored_previous_state,
                converged,
            )

        return _failed_outcome(
            enabled=authoritative_enabled,
            reason_code=MemoryManagementReasonCode.INTERNAL_ERROR,
        )

    def _alive_targets(self) -> list[MemorySettingRuntimeTarget]:
        alive_targets: list[MemorySettingRuntimeTarget] = []
        alive_refs: list[ReferenceType[MemorySettingRuntimeTarget]] = []
        for target_ref in self._target_refs:
            target = target_ref()
            if target is None:
                continue
            if any(candidate is target for candidate in alive_targets):
                continue
            alive_targets.append(target)
            alive_refs.append(target_ref)
        self._target_refs = alive_refs
        return alive_targets

    def _remove_target(self, target: MemorySettingRuntimeTarget) -> None:
        self._target_refs = [
            target_ref
            for target_ref in self._target_refs
            if target_ref() is not None and target_ref() is not target
        ]

    def _synchronize_targets(
        self,
        targets: list[MemorySettingRuntimeTarget],
        enabled: bool,
    ) -> bool:
        synchronized = True
        for target in targets:
            if not self._synchronize_target(target, enabled):
                synchronized = False
        return synchronized

    @staticmethod
    def _synchronize_target(
        target: MemorySettingRuntimeTarget,
        enabled: bool,
    ) -> bool:
        synchronized = True
        try:
            target.apply_memory_enabled_state(enabled=enabled)
        except Exception as error:
            synchronized = False
            logger.error(
                "Memory setting runtime apply failed (exception_type={})",
                type(error).__name__,
            )

        try:
            target.clear_memory_setting_transients()
        except Exception as error:
            synchronized = False
            logger.error(
                "Memory setting runtime transient cleanup failed (exception_type={})",
                type(error).__name__,
            )
        return synchronized


def _failed_outcome(
    *,
    enabled: bool,
    reason_code: MemoryManagementReasonCode,
) -> MemorySettingUpdateOutcome:
    return MemorySettingUpdateOutcome(
        status=MemoryManagementStatus.FAILED,
        changed=False,
        enabled=enabled,
        reason_code=reason_code,
    )
