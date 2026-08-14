"""Atomic persistence for the M5 memory enabled setting.

This module owns disk persistence only. Runtime state synchronization is a separate
M5.4.2.5 step and must not be added here.
"""

from __future__ import annotations

import asyncio
from copy import deepcopy
from dataclasses import dataclass
import os
from pathlib import Path
import re
import tempfile
from typing import TYPE_CHECKING, Any

from loguru import logger
import yaml

from .management_types import (
    MemoryManagementReasonCode,
    MemoryManagementStatus,
)

if TYPE_CHECKING:
    from open_llm_vtuber.config_manager.main import Config


_ENVIRONMENT_REFERENCE = re.compile(r"\$\{(\w+)\}")


@dataclass(frozen=True)
class AtomicConfigWriteResult:
    """Content-free result of one expected-state configuration transition."""

    status: MemoryManagementStatus
    changed: bool
    enabled: bool | None
    reason_code: MemoryManagementReasonCode | None = None

    def __post_init__(self) -> None:
        if self.status == MemoryManagementStatus.SUCCESS:
            if self.reason_code is not None:
                raise ValueError("successful writes cannot have a reason")
            if self.enabled is None:
                raise ValueError("successful writes require the persisted state")
            return

        if self.changed:
            raise ValueError("unsuccessful writes cannot report a change")
        if self.reason_code is None:
            raise ValueError("unsuccessful writes require a reason")
        if (
            self.status == MemoryManagementStatus.REJECTED
            and self.reason_code != MemoryManagementReasonCode.SETTING_CONFLICT
        ):
            raise ValueError("rejected writes require setting_conflict")
        if self.status == MemoryManagementStatus.FAILED and self.reason_code not in {
            MemoryManagementReasonCode.CONFIG_PERSIST_FAILURE,
            MemoryManagementReasonCode.INTERNAL_ERROR,
        }:
            raise ValueError("failed writes require an operational reason")


class AtomicMemoryConfigWriter:
    """Persist only ``memory_config.enabled`` to the project's ``conf.yaml``."""

    def __init__(self, project_root: str | Path) -> None:
        root = Path(project_root).resolve()
        config_path = (root / "conf.yaml").resolve(strict=False)
        if config_path.parent != root:
            raise ValueError("configuration path must remain inside the project root")

        self._config_path = config_path
        self._lock = asyncio.Lock()

    async def update_enabled(
        self,
        *,
        expected_enabled: bool,
        enabled: bool,
    ) -> AtomicConfigWriteResult:
        """Apply an atomic compare-and-set update to the memory total switch."""

        if type(expected_enabled) is not bool or type(enabled) is not bool:
            raise TypeError("expected_enabled and enabled must be booleans")

        async with self._lock:
            return await asyncio.to_thread(
                self._update_enabled_sync,
                expected_enabled=expected_enabled,
                enabled=enabled,
            )

    def _update_enabled_sync(
        self,
        *,
        expected_enabled: bool,
        enabled: bool,
    ) -> AtomicConfigWriteResult:
        phase = "read"
        current_enabled: bool | None = None
        temporary_descriptor: int | None = None
        temporary_path: Path | None = None

        try:
            original_bytes = self._config_path.read_bytes()
            original_raw = self._parse_yaml(original_bytes)

            phase = "validate_source"
            original_config = self._validate_raw_config(original_raw)
            current_enabled = original_config.memory_config.enabled

            if current_enabled != expected_enabled:
                return AtomicConfigWriteResult(
                    status=MemoryManagementStatus.REJECTED,
                    changed=False,
                    enabled=current_enabled,
                    reason_code=MemoryManagementReasonCode.SETTING_CONFLICT,
                )

            if current_enabled == enabled:
                return AtomicConfigWriteResult(
                    status=MemoryManagementStatus.SUCCESS,
                    changed=False,
                    enabled=current_enabled,
                )

            phase = "prepare_target"
            target_raw = deepcopy(original_raw)
            memory_section = target_raw.setdefault("memory_config", {})
            if not isinstance(memory_section, dict):
                raise TypeError("memory_config must be a mapping")
            memory_section["enabled"] = enabled

            target_config = self._validate_raw_config(target_raw)
            self._ensure_only_enabled_changed(original_config, target_config)

            phase = "create_temporary"
            temporary_descriptor, temporary_name = tempfile.mkstemp(
                prefix=".conf.yaml.",
                suffix=".tmp",
                dir=self._config_path.parent,
            )
            temporary_path = Path(temporary_name)

            phase = "write_temporary"
            temporary_file = os.fdopen(
                temporary_descriptor,
                "w",
                encoding="utf-8",
                newline="\n",
            )
            temporary_descriptor = None
            with temporary_file as file:
                yaml.safe_dump(
                    target_raw,
                    file,
                    allow_unicode=True,
                    sort_keys=False,
                )
                file.flush()
                phase = "sync_temporary"
                os.fsync(file.fileno())

            phase = "verify_temporary"
            verified_raw = self._parse_yaml(temporary_path.read_bytes())
            if verified_raw != target_raw:
                raise ValueError("temporary configuration changed during serialization")
            verified_config = self._validate_raw_config(verified_raw)
            if verified_config.memory_config.enabled != enabled:
                raise ValueError("temporary configuration has the wrong enabled state")
            self._ensure_only_enabled_changed(original_config, verified_config)

            phase = "check_source_unchanged"
            latest_bytes = self._config_path.read_bytes()
            if latest_bytes != original_bytes:
                latest_raw = self._parse_yaml(latest_bytes)
                latest_config = self._validate_raw_config(latest_raw)
                return AtomicConfigWriteResult(
                    status=MemoryManagementStatus.REJECTED,
                    changed=False,
                    enabled=latest_config.memory_config.enabled,
                    reason_code=MemoryManagementReasonCode.SETTING_CONFLICT,
                )

            phase = "replace"
            os.replace(temporary_path, self._config_path)
            temporary_path = None

            return AtomicConfigWriteResult(
                status=MemoryManagementStatus.SUCCESS,
                changed=True,
                enabled=enabled,
            )
        except Exception as error:
            logger.error(
                "Memory configuration persistence failed (phase={}, exception_type={})",
                phase,
                type(error).__name__,
            )
            return AtomicConfigWriteResult(
                status=MemoryManagementStatus.FAILED,
                changed=False,
                enabled=current_enabled,
                reason_code=MemoryManagementReasonCode.CONFIG_PERSIST_FAILURE,
            )
        finally:
            if temporary_descriptor is not None:
                try:
                    os.close(temporary_descriptor)
                except Exception as close_error:
                    logger.error(
                        "Memory configuration temporary descriptor close failed "
                        "(exception_type={})",
                        type(close_error).__name__,
                    )
            if temporary_path is not None:
                try:
                    temporary_path.unlink(missing_ok=True)
                except Exception as cleanup_error:
                    logger.error(
                        "Memory configuration temporary cleanup failed "
                        "(exception_type={})",
                        type(cleanup_error).__name__,
                    )

    @staticmethod
    def _parse_yaml(content: bytes) -> dict[str, Any]:
        text = content.decode("utf-8-sig")
        loaded = yaml.safe_load(text)
        if not isinstance(loaded, dict):
            raise TypeError("configuration root must be a mapping")
        return loaded

    @staticmethod
    def _validate_raw_config(raw_config: dict[str, Any]) -> Config:
        from open_llm_vtuber.config_manager.main import Config

        serialized = yaml.safe_dump(
            raw_config,
            allow_unicode=True,
            sort_keys=False,
        )

        def replace_environment_reference(match: re.Match[str]) -> str:
            name = match.group(1)
            return os.getenv(name, match.group(0))

        validation_text = _ENVIRONMENT_REFERENCE.sub(
            replace_environment_reference,
            serialized,
        )
        validation_data = yaml.safe_load(validation_text)
        if not isinstance(validation_data, dict):
            raise TypeError("configuration root must be a mapping")
        return Config.model_validate(validation_data)

    @staticmethod
    def _ensure_only_enabled_changed(source: Config, target: Config) -> None:
        source_data = source.model_dump(mode="python", by_alias=True)
        target_data = target.model_dump(mode="python", by_alias=True)
        source_data["memory_config"]["enabled"] = False
        target_data["memory_config"]["enabled"] = False
        if source_data != target_data:
            raise ValueError("configuration change exceeded memory_config.enabled")
