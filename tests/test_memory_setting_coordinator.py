import asyncio
import gc
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
from weakref import ref

from loguru import logger
import pytest
import yaml

from open_llm_vtuber.config_manager import MemoryConfig
from open_llm_vtuber.memory import (
    AtomicConfigWriteResult,
    AtomicMemoryConfigWriter,
    MemoryManagementReasonCode,
    MemoryManagementStatus,
    MemorySettingRuntimeCoordinator,
)
from open_llm_vtuber.service_context import ServiceContext


PROJECT_ROOT = Path(__file__).resolve().parents[1]
TEMPLATE_PATH = PROJECT_ROOT / "config_templates" / "conf.elysia.example.yaml"


class StatefulWriter:
    def __init__(self, enabled: bool) -> None:
        self.enabled = enabled
        self.calls: list[tuple[bool, bool]] = []

    async def update_enabled(
        self,
        *,
        expected_enabled: bool,
        enabled: bool,
    ) -> AtomicConfigWriteResult:
        self.calls.append((expected_enabled, enabled))
        await asyncio.sleep(0)
        if self.enabled != expected_enabled:
            return AtomicConfigWriteResult(
                status=MemoryManagementStatus.REJECTED,
                changed=False,
                enabled=self.enabled,
                reason_code=MemoryManagementReasonCode.SETTING_CONFLICT,
            )

        changed = self.enabled != enabled
        self.enabled = enabled
        return AtomicConfigWriteResult(
            status=MemoryManagementStatus.SUCCESS,
            changed=changed,
            enabled=self.enabled,
        )


class ScriptedWriter:
    def __init__(self, *results: AtomicConfigWriteResult | Exception) -> None:
        self.results = list(results)
        self.calls: list[tuple[bool, bool]] = []

    async def update_enabled(
        self,
        *,
        expected_enabled: bool,
        enabled: bool,
    ) -> AtomicConfigWriteResult:
        self.calls.append((expected_enabled, enabled))
        result = self.results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


class RuntimeTarget:
    def __init__(
        self,
        enabled: bool,
        *,
        name: str = "target",
        events: list[str] | None = None,
        fail_enabled_once: bool | None = None,
        fail_always: bool = False,
    ) -> None:
        self.enabled = enabled
        self.name = name
        self.events = events
        self.fail_enabled_once = fail_enabled_once
        self.fail_always = fail_always
        self.apply_calls: list[bool] = []
        self.clear_calls = 0

    def apply_memory_enabled_state(self, *, enabled: bool) -> None:
        self.apply_calls.append(enabled)
        if self.events is not None:
            self.events.append(f"{self.name}:apply:{enabled}")
        if self.fail_always or self.fail_enabled_once is enabled:
            self.fail_enabled_once = None
            raise RuntimeError("PRIVATE_RUNTIME_TARGET_DETAIL")
        self.enabled = enabled

    def clear_memory_setting_transients(self) -> None:
        self.clear_calls += 1
        if self.events is not None:
            self.events.append(f"{self.name}:clear")


def _run(coroutine):
    return asyncio.run(coroutine)


def _success(*, changed: bool, enabled: bool) -> AtomicConfigWriteResult:
    return AtomicConfigWriteResult(
        status=MemoryManagementStatus.SUCCESS,
        changed=changed,
        enabled=enabled,
    )


def _failed(*, enabled: bool | None) -> AtomicConfigWriteResult:
    return AtomicConfigWriteResult(
        status=MemoryManagementStatus.FAILED,
        changed=False,
        enabled=enabled,
        reason_code=MemoryManagementReasonCode.CONFIG_PERSIST_FAILURE,
    )


@pytest.mark.parametrize(("initial", "target_state"), [(False, True), (True, False)])
def test_coordinator_applies_successful_transition_to_all_runtime_targets(
    initial: bool,
    target_state: bool,
) -> None:
    writer = StatefulWriter(initial)
    coordinator = MemorySettingRuntimeCoordinator(
        writer=writer,
        initial_enabled=initial,
    )
    first = RuntimeTarget(not initial)
    second = RuntimeTarget(not initial)

    async def exercise():
        await coordinator.register_target(first)
        await coordinator.register_target(second)
        return await coordinator.update_enabled(
            expected_enabled=initial,
            enabled=target_state,
        )

    result = _run(exercise())

    assert result.status == MemoryManagementStatus.SUCCESS
    assert result.changed is True
    assert result.enabled is target_state
    assert coordinator.runtime_enabled is target_state
    assert writer.enabled is target_state
    assert first.enabled is target_state
    assert second.enabled is target_state
    assert first.clear_calls == 2
    assert second.clear_calls == 2


def test_idempotent_write_repairs_runtime_drift_without_reporting_change() -> None:
    coordinator = MemorySettingRuntimeCoordinator(
        writer=StatefulWriter(True),
        initial_enabled=True,
    )
    target = RuntimeTarget(True)

    async def exercise():
        await coordinator.register_target(target)
        target.enabled = False
        return await coordinator.update_enabled(
            expected_enabled=True,
            enabled=True,
        )

    result = _run(exercise())

    assert result.status == MemoryManagementStatus.SUCCESS
    assert result.changed is False
    assert result.enabled is True
    assert target.enabled is True


def test_setting_conflict_self_heals_runtime_to_authoritative_disk_state() -> None:
    writer = StatefulWriter(True)
    coordinator = MemorySettingRuntimeCoordinator(
        writer=writer,
        initial_enabled=False,
    )
    target = RuntimeTarget(False)

    async def exercise():
        await coordinator.register_target(target)
        return await coordinator.update_enabled(
            expected_enabled=False,
            enabled=False,
        )

    result = _run(exercise())

    assert result.status == MemoryManagementStatus.REJECTED
    assert result.reason_code == MemoryManagementReasonCode.SETTING_CONFLICT
    assert result.changed is False
    assert result.enabled is True
    assert coordinator.runtime_enabled is True
    assert target.enabled is True


@pytest.mark.parametrize("reported_enabled", [False, None])
def test_persistence_failure_does_not_apply_requested_runtime_state(
    reported_enabled: bool | None,
) -> None:
    coordinator = MemorySettingRuntimeCoordinator(
        writer=ScriptedWriter(_failed(enabled=reported_enabled)),
        initial_enabled=False,
    )
    target = RuntimeTarget(False)

    async def exercise():
        await coordinator.register_target(target)
        calls_before = list(target.apply_calls)
        result = await coordinator.update_enabled(
            expected_enabled=False,
            enabled=True,
        )
        return result, calls_before

    result, calls_before = _run(exercise())

    assert result.status == MemoryManagementStatus.FAILED
    assert result.reason_code == MemoryManagementReasonCode.CONFIG_PERSIST_FAILURE
    assert result.changed is False
    assert result.enabled is False
    assert coordinator.runtime_enabled is False
    assert target.enabled is False
    assert target.apply_calls == calls_before


def test_targets_are_deduplicated_and_synchronized_in_registration_order() -> None:
    events: list[str] = []
    coordinator = MemorySettingRuntimeCoordinator(
        writer=StatefulWriter(False),
        initial_enabled=False,
    )
    first = RuntimeTarget(False, name="default", events=events)
    second = RuntimeTarget(False, name="session", events=events)

    async def exercise():
        await coordinator.register_target(first)
        await coordinator.register_target(first)
        await coordinator.register_target(second)
        events.clear()
        return await coordinator.update_enabled(
            expected_enabled=False,
            enabled=True,
        )

    result = _run(exercise())

    assert result.status == MemoryManagementStatus.SUCCESS
    assert events == [
        "default:apply:True",
        "default:clear",
        "session:apply:True",
        "session:clear",
    ]


def test_unregister_stops_future_runtime_synchronization() -> None:
    coordinator = MemorySettingRuntimeCoordinator(
        writer=StatefulWriter(False),
        initial_enabled=False,
    )
    target = RuntimeTarget(False)

    async def exercise():
        await coordinator.register_target(target)
        await coordinator.unregister_target(target)
        calls_before = list(target.apply_calls)
        result = await coordinator.update_enabled(
            expected_enabled=False,
            enabled=True,
        )
        return result, calls_before

    result, calls_before = _run(exercise())

    assert result.status == MemoryManagementStatus.SUCCESS
    assert target.enabled is False
    assert target.apply_calls == calls_before


def test_registry_uses_weak_references_for_disconnected_targets() -> None:
    coordinator = MemorySettingRuntimeCoordinator(
        writer=StatefulWriter(False),
        initial_enabled=False,
    )
    target = RuntimeTarget(False)
    target_ref = ref(target)
    _run(coordinator.register_target(target))

    del target
    gc.collect()

    result = _run(coordinator.update_enabled(expected_enabled=False, enabled=True))
    assert target_ref() is None
    assert result.status == MemoryManagementStatus.SUCCESS


def test_concurrent_compare_and_set_requests_are_serialized_by_coordinator() -> None:
    writer = StatefulWriter(False)
    coordinator = MemorySettingRuntimeCoordinator(
        writer=writer,
        initial_enabled=False,
    )
    target = RuntimeTarget(False)

    async def exercise():
        await coordinator.register_target(target)
        return await asyncio.gather(
            coordinator.update_enabled(expected_enabled=False, enabled=True),
            coordinator.update_enabled(expected_enabled=False, enabled=True),
        )

    results = _run(exercise())

    statuses = [result.status for result in results]
    assert statuses.count(MemoryManagementStatus.SUCCESS) == 1
    assert statuses.count(MemoryManagementStatus.REJECTED) == 1
    assert writer.calls == [(False, True), (False, True)]
    assert coordinator.runtime_enabled is True
    assert target.enabled is True


def test_runtime_sync_failure_rolls_disk_and_targets_back_to_previous_state() -> None:
    writer = StatefulWriter(False)
    coordinator = MemorySettingRuntimeCoordinator(
        writer=writer,
        initial_enabled=False,
    )
    target = RuntimeTarget(False, fail_enabled_once=True)

    async def exercise():
        await coordinator.register_target(target)
        return await coordinator.update_enabled(
            expected_enabled=False,
            enabled=True,
        )

    result = _run(exercise())

    assert result.status == MemoryManagementStatus.FAILED
    assert result.reason_code == MemoryManagementReasonCode.INTERNAL_ERROR
    assert result.changed is False
    assert result.enabled is False
    assert writer.calls == [(False, True), (True, False)]
    assert writer.enabled is False
    assert coordinator.runtime_enabled is False
    assert target.enabled is False


def test_failed_compensation_reports_final_known_disk_state_not_false_restore() -> None:
    writer = ScriptedWriter(
        _success(changed=True, enabled=True),
        _failed(enabled=True),
    )
    coordinator = MemorySettingRuntimeCoordinator(
        writer=writer,
        initial_enabled=False,
    )
    target = RuntimeTarget(False, fail_enabled_once=True)

    async def exercise():
        await coordinator.register_target(target)
        return await coordinator.update_enabled(
            expected_enabled=False,
            enabled=True,
        )

    result = _run(exercise())

    assert result.status == MemoryManagementStatus.FAILED
    assert result.reason_code == MemoryManagementReasonCode.INTERNAL_ERROR
    assert result.changed is False
    assert result.enabled is True
    assert coordinator.runtime_enabled is True
    assert target.enabled is True


def test_runtime_failures_are_logged_without_exception_detail() -> None:
    secret = "PRIVATE_RUNTIME_TARGET_DETAIL"
    coordinator = MemorySettingRuntimeCoordinator(
        writer=StatefulWriter(False),
        initial_enabled=False,
    )
    target = RuntimeTarget(False, fail_enabled_once=True)
    logs: list[str] = []
    sink_id = logger.add(
        lambda message: logs.append(str(message)),
        format="{level} {message}",
        level="ERROR",
    )
    try:

        async def exercise():
            await coordinator.register_target(target)
            return await coordinator.update_enabled(
                expected_enabled=False,
                enabled=True,
            )

        result = _run(exercise())
    finally:
        logger.remove(sink_id)

    output = "".join(logs)
    assert result.status == MemoryManagementStatus.FAILED
    assert "exception_type=RuntimeError" in output
    assert secret not in output


def test_service_context_target_changes_only_switch_and_pending_confirmation(
    tmp_path: Path,
) -> None:
    context = ServiceContext(project_root=tmp_path)
    context.config = SimpleNamespace(memory_config=MemoryConfig(enabled=False))
    context.memory_command_controller.clear_pending = Mock()
    context.active_memory_command_turn_id = "active-memory-turn"
    context.memory_service = object()
    context.agent_engine = object()
    context.tts_engine = object()
    service = context.memory_service
    agent = context.agent_engine
    tts = context.tts_engine
    coordinator = MemorySettingRuntimeCoordinator(
        writer=StatefulWriter(False),
        initial_enabled=False,
    )

    async def exercise():
        await coordinator.register_target(context)
        context.memory_command_controller.clear_pending.reset_mock()
        return await coordinator.update_enabled(
            expected_enabled=False,
            enabled=True,
        )

    result = _run(exercise())

    assert result.status == MemoryManagementStatus.SUCCESS
    assert context.config.memory_config.enabled is True
    context.memory_command_controller.clear_pending.assert_called_once_with()
    assert context.active_memory_command_turn_id == "active-memory-turn"
    assert context.memory_service is service
    assert context.agent_engine is agent
    assert context.tts_engine is tts


def test_service_context_runtime_target_rejects_invalid_or_missing_config(
    tmp_path: Path,
) -> None:
    context = ServiceContext(project_root=tmp_path)

    with pytest.raises(TypeError):
        context.apply_memory_enabled_state(enabled=1)
    with pytest.raises(RuntimeError):
        context.apply_memory_enabled_state(enabled=True)


def test_coordinator_with_atomic_writer_converges_disk_and_service_context(
    tmp_path: Path,
) -> None:
    raw = yaml.safe_load(TEMPLATE_PATH.read_text(encoding="utf-8"))
    raw["memory_config"]["enabled"] = False
    (tmp_path / "conf.yaml").write_text(
        yaml.safe_dump(raw, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    context = ServiceContext(project_root=tmp_path)
    context.config = SimpleNamespace(memory_config=MemoryConfig(enabled=False))
    coordinator = MemorySettingRuntimeCoordinator(
        writer=AtomicMemoryConfigWriter(tmp_path),
        initial_enabled=False,
    )

    async def exercise():
        await coordinator.register_target(context)
        return await coordinator.update_enabled(
            expected_enabled=False,
            enabled=True,
        )

    result = _run(exercise())
    persisted = yaml.safe_load((tmp_path / "conf.yaml").read_text(encoding="utf-8"))

    assert result.status == MemoryManagementStatus.SUCCESS
    assert result.changed is True
    assert persisted["memory_config"]["enabled"] is True
    assert context.config.memory_config.enabled is True


def test_coordinator_rejects_non_boolean_inputs() -> None:
    writer = StatefulWriter(False)

    with pytest.raises(TypeError):
        MemorySettingRuntimeCoordinator(writer=writer, initial_enabled=0)

    coordinator = MemorySettingRuntimeCoordinator(
        writer=writer,
        initial_enabled=False,
    )
    with pytest.raises(TypeError):
        _run(coordinator.update_enabled(expected_enabled=False, enabled=1))
