"""Atomic local JSON storage for lightweight persistent memory."""

from __future__ import annotations

import asyncio
import os
import tempfile
import threading
from collections.abc import Callable
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path, PurePosixPath, PureWindowsPath

from .types import MemoryDocument, MemoryScope


MAX_MEMORY_FILE_BYTES = 256 * 1024


class MemoryBackupMode(str, Enum):
    """Choose whether backup retains the old or sanitized replacement document."""

    PREVIOUS = "previous"
    REPLACEMENT = "replacement"


class MemoryStoreError(Exception):
    """Base class for non-sensitive persistent memory storage errors."""


class MemoryStorePathError(MemoryStoreError):
    """Raised when a configured or derived storage path is unsafe."""


class MemoryStoreReadError(MemoryStoreError):
    """Raised when a memory document cannot be read or validated."""


class MemoryStoreWriteError(MemoryStoreError):
    """Raised when a memory document cannot be persisted atomically."""


class MemoryStoreScopeMismatchError(MemoryStoreError):
    """Raised when a document does not belong to the requested scope."""


class MemoryStoreRevisionConflictError(MemoryStoreError):
    """Raised when compare-and-swap revision validation fails."""

    def __init__(self, expected_revision: int, actual_revision: int) -> None:
        self.expected_revision = expected_revision
        self.actual_revision = actual_revision
        super().__init__(
            "Memory document revision conflict "
            f"(expected={expected_revision}, actual={actual_revision})"
        )


class MemoryStoreRecoveryError(MemoryStoreReadError):
    """Raised when a damaged main document cannot be restored from backup."""


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


_SCOPE_LOCKS: dict[str, threading.RLock] = {}
_SCOPE_LOCKS_GUARD = threading.Lock()


def _get_process_scope_lock(path: Path) -> threading.RLock:
    lock_key = os.path.normcase(str(path.resolve(strict=False)))
    with _SCOPE_LOCKS_GUARD:
        return _SCOPE_LOCKS.setdefault(lock_key, threading.RLock())


class PersistentMemoryStore:
    """Load and atomically replace one JSON document per memory scope."""

    def __init__(
        self,
        project_root: Path,
        storage_dir: str,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        self._project_root = Path(project_root).resolve()
        self._clock = clock
        self._storage_root = self._resolve_storage_root(storage_dir)

    @property
    def storage_root(self) -> Path:
        """Return the validated absolute storage root."""
        return self._storage_root

    async def load(self, scope: MemoryScope) -> MemoryDocument:
        """Load a validated document without blocking the event loop."""
        return await asyncio.to_thread(self._load_sync, scope)

    async def save(
        self,
        scope: MemoryScope,
        document: MemoryDocument,
        *,
        expected_revision: int,
        backup_mode: MemoryBackupMode = MemoryBackupMode.PREVIOUS,
    ) -> MemoryDocument:
        """Atomically replace a document using revision compare-and-swap."""
        return await asyncio.to_thread(
            self._save_sync,
            scope,
            document,
            expected_revision,
            backup_mode,
        )

    def _resolve_storage_root(self, storage_dir: str) -> Path:
        if not storage_dir or storage_dir != storage_dir.strip():
            raise MemoryStorePathError("Memory storage directory is invalid")

        posix_path = PurePosixPath(storage_dir)
        windows_path = PureWindowsPath(storage_dir)
        if (
            posix_path.is_absolute()
            or windows_path.is_absolute()
            or bool(windows_path.drive)
            or ".." in posix_path.parts
            or ".." in windows_path.parts
            or storage_dir in {".", "./", ".\\"}
        ):
            raise MemoryStorePathError("Memory storage directory is unsafe")

        storage_root = (self._project_root / storage_dir).resolve()
        if not _is_relative_to(storage_root, self._project_root):
            raise MemoryStorePathError("Memory storage directory escapes project root")
        return storage_root

    def _get_document_path(self, scope: MemoryScope) -> Path:
        profile_dir = self._storage_root / scope.profile_id
        target = profile_dir / f"{scope.character_conf_uid}.json"

        if profile_dir.is_symlink():
            raise MemoryStorePathError("Memory profile directory must not be a symlink")
        if target.is_symlink():
            raise MemoryStorePathError("Memory document must not be a symlink")

        resolved_target = target.resolve(strict=False)
        if not _is_relative_to(resolved_target, self._storage_root):
            raise MemoryStorePathError("Memory document path escapes storage root")
        return target

    def _get_backup_path(self, scope: MemoryScope) -> Path:
        target = self._get_document_path(scope)
        backup = target.with_name(f"{target.name}.bak")
        if backup.is_symlink():
            raise MemoryStorePathError("Memory backup must not be a symlink")
        if not _is_relative_to(backup.resolve(strict=False), self._storage_root):
            raise MemoryStorePathError("Memory backup path escapes storage root")
        return backup

    def _get_scope_lock(self, scope: MemoryScope) -> threading.RLock:
        return _get_process_scope_lock(self._get_document_path(scope))

    def _empty_document(self, scope: MemoryScope) -> MemoryDocument:
        return MemoryDocument(
            profile_id=scope.profile_id,
            character_conf_uid=scope.character_conf_uid,
            revision=0,
            updated_at=self._clock(),
            items=[],
        )

    @staticmethod
    def _validate_scope(
        scope: MemoryScope,
        document: MemoryDocument,
    ) -> None:
        if (
            document.profile_id != scope.profile_id
            or document.character_conf_uid != scope.character_conf_uid
        ):
            raise MemoryStoreScopeMismatchError(
                "Memory document does not match the requested scope"
            )

    def _parse_document_bytes(
        self,
        scope: MemoryScope,
        payload: bytes,
    ) -> MemoryDocument:
        if len(payload) > MAX_MEMORY_FILE_BYTES:
            raise MemoryStoreReadError("Memory document exceeds the size limit")

        try:
            document = MemoryDocument.model_validate_json(payload)
        except Exception as exc:
            raise MemoryStoreReadError(
                f"Memory document is invalid ({type(exc).__name__})"
            ) from exc

        self._validate_scope(scope, document)
        return document

    @staticmethod
    def _read_limited_bytes(path: Path) -> bytes:
        with path.open("rb") as memory_file:
            payload = memory_file.read(MAX_MEMORY_FILE_BYTES + 1)
        if len(payload) > MAX_MEMORY_FILE_BYTES:
            raise MemoryStoreReadError("Memory document exceeds the size limit")
        return payload

    def _load_sync(self, scope: MemoryScope) -> MemoryDocument:
        with self._get_scope_lock(scope):
            return self._load_locked(scope)

    def _load_document_file(
        self,
        scope: MemoryScope,
        path: Path,
    ) -> MemoryDocument:
        if path.is_symlink():
            raise MemoryStorePathError("Memory document must not be a symlink")
        if not path.is_file():
            raise MemoryStoreReadError("Memory document is not a regular file")
        if path.stat().st_size > MAX_MEMORY_FILE_BYTES:
            raise MemoryStoreReadError("Memory document exceeds the size limit")
        return self._parse_document_bytes(
            scope,
            self._read_limited_bytes(path),
        )

    def _load_locked(self, scope: MemoryScope) -> MemoryDocument:
        try:
            target = self._get_document_path(scope)
            if not target.exists():
                return self._empty_document(scope)
            try:
                return self._load_document_file(scope, target)
            except (MemoryStoreReadError, MemoryStoreScopeMismatchError) as main_error:
                return self._recover_from_backup_locked(scope, main_error)
        except MemoryStoreError:
            raise
        except Exception as exc:
            raise MemoryStoreReadError(
                f"Failed to load memory document ({type(exc).__name__})"
            ) from exc

    def _save_sync(
        self,
        scope: MemoryScope,
        document: MemoryDocument,
        expected_revision: int,
        backup_mode: MemoryBackupMode,
    ) -> MemoryDocument:
        with self._get_scope_lock(scope):
            return self._save_locked(
                scope,
                document,
                expected_revision,
                backup_mode,
            )

    def _validate_revision(
        self,
        current: MemoryDocument,
        desired: MemoryDocument,
        expected_revision: int,
    ) -> None:
        if expected_revision < 0:
            raise MemoryStoreRevisionConflictError(
                expected_revision,
                current.revision,
            )
        if current.revision != expected_revision:
            raise MemoryStoreRevisionConflictError(
                expected_revision,
                current.revision,
            )
        required_revision = expected_revision + 1
        if desired.revision != required_revision:
            raise MemoryStoreRevisionConflictError(
                required_revision,
                desired.revision,
            )

    def _prepare_validated_temp(
        self,
        target: Path,
        scope: MemoryScope,
        document: MemoryDocument,
    ) -> tuple[Path, MemoryDocument]:
        payload = (document.model_dump_json(indent=2) + "\n").encode("utf-8")
        if len(payload) > MAX_MEMORY_FILE_BYTES:
            raise MemoryStoreWriteError("Memory document exceeds the size limit")

        descriptor, temporary_name = tempfile.mkstemp(
            dir=target.parent,
            prefix=f".{target.name}.",
            suffix=".tmp",
        )
        temporary_path = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "wb") as temporary_file:
                temporary_file.write(payload)
                temporary_file.flush()
                os.fsync(temporary_file.fileno())
            try:
                persisted_document = self._parse_document_bytes(
                    scope,
                    self._read_limited_bytes(temporary_path),
                )
            except MemoryStoreScopeMismatchError:
                raise
            except MemoryStoreReadError as exc:
                raise MemoryStoreWriteError(
                    "Temporary memory document failed validation"
                ) from exc
            return temporary_path, persisted_document
        except Exception:
            try:
                os.close(descriptor)
            except OSError:
                # os.fdopen owns and closes the descriptor after it succeeds.
                pass
            temporary_path.unlink(missing_ok=True)
            raise

    @staticmethod
    def _cleanup_temporary_files(*paths: Path | None) -> None:
        for path in paths:
            if path is None:
                continue
            try:
                path.unlink(missing_ok=True)
            except OSError:
                # Cleanup failure must not mask the original storage error.
                pass

    def _save_locked(
        self,
        scope: MemoryScope,
        document: MemoryDocument,
        expected_revision: int,
        backup_mode: MemoryBackupMode,
    ) -> MemoryDocument:
        main_temporary_path: Path | None = None
        backup_temporary_path: Path | None = None
        rollback_backup_temporary_path: Path | None = None
        try:
            validated_document = MemoryDocument.model_validate(
                document.model_dump(mode="python")
            )
            if not isinstance(backup_mode, MemoryBackupMode):
                raise TypeError("backup_mode must be a MemoryBackupMode")
            self._validate_scope(scope, validated_document)

            current_document = self._load_locked(scope)
            self._validate_revision(
                current_document,
                validated_document,
                expected_revision,
            )

            target = self._get_document_path(scope)
            backup = self._get_backup_path(scope)
            target.parent.mkdir(parents=True, exist_ok=True)

            # Re-resolve after mkdir so a pre-existing symlink cannot redirect writes.
            target = self._get_document_path(scope)
            backup = self._get_backup_path(scope)
            resolved_parent = target.parent.resolve(strict=True)
            if not _is_relative_to(resolved_parent, self._storage_root):
                raise MemoryStorePathError("Memory profile directory is unsafe")

            main_temporary_path, persisted_document = self._prepare_validated_temp(
                target,
                scope,
                validated_document,
            )
            if target.exists():
                backup_document = (
                    current_document
                    if backup_mode == MemoryBackupMode.PREVIOUS
                    else validated_document
                )
                backup_temporary_path, _ = self._prepare_validated_temp(
                    backup,
                    scope,
                    backup_document,
                )
                rollback_backup_temporary_path, _ = self._prepare_validated_temp(
                    backup,
                    scope,
                    current_document,
                )
                os.replace(backup_temporary_path, backup)
                backup_temporary_path = None

            try:
                os.replace(main_temporary_path, target)
            except Exception:
                if rollback_backup_temporary_path is not None:
                    os.replace(rollback_backup_temporary_path, backup)
                    rollback_backup_temporary_path = None
                raise
            main_temporary_path = None
            return persisted_document
        except MemoryStoreError:
            raise
        except Exception as exc:
            raise MemoryStoreWriteError(
                f"Failed to save memory document ({type(exc).__name__})"
            ) from exc
        finally:
            self._cleanup_temporary_files(
                main_temporary_path,
                backup_temporary_path,
                rollback_backup_temporary_path,
            )

    def _recover_from_backup_locked(
        self,
        scope: MemoryScope,
        main_error: MemoryStoreError,
    ) -> MemoryDocument:
        recovery_temporary_path: Path | None = None
        try:
            target = self._get_document_path(scope)
            backup = self._get_backup_path(scope)
            if not backup.exists():
                raise MemoryStoreRecoveryError(
                    f"Memory document recovery failed ({type(main_error).__name__})"
                ) from main_error

            backup_document = self._load_document_file(scope, backup)
            recovery_temporary_path, recovered_document = self._prepare_validated_temp(
                target, scope, backup_document
            )
            os.replace(recovery_temporary_path, target)
            recovery_temporary_path = None
            return recovered_document
        except MemoryStoreRecoveryError:
            raise
        except MemoryStoreError as exc:
            raise MemoryStoreRecoveryError(
                f"Memory document recovery failed ({type(exc).__name__})"
            ) from exc
        except Exception as exc:
            raise MemoryStoreRecoveryError(
                f"Memory document recovery failed ({type(exc).__name__})"
            ) from exc
        finally:
            self._cleanup_temporary_files(recovery_temporary_path)
