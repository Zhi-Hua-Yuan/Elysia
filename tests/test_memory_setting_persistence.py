import asyncio
from copy import deepcopy
from pathlib import Path

from loguru import logger
import pytest
import yaml

from open_llm_vtuber.memory import (
    AtomicMemoryConfigWriter,
    MemoryManagementReasonCode,
    MemoryManagementStatus,
)
from open_llm_vtuber.memory import setting_persistence


PROJECT_ROOT = Path(__file__).resolve().parents[1]
TEMPLATE_PATH = PROJECT_ROOT / "config_templates" / "conf.elysia.example.yaml"


def _create_project(
    tmp_path: Path,
    *,
    enabled: bool = False,
    include_memory_section: bool = True,
) -> tuple[Path, dict]:
    raw = yaml.safe_load(TEMPLATE_PATH.read_text(encoding="utf-8"))
    if include_memory_section:
        raw["memory_config"]["enabled"] = enabled
    else:
        raw.pop("memory_config")
    (tmp_path / "conf.yaml").write_text(
        yaml.safe_dump(raw, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    return tmp_path, raw


def _run_update(
    writer: AtomicMemoryConfigWriter,
    *,
    expected_enabled: bool,
    enabled: bool,
):
    return asyncio.run(
        writer.update_enabled(
            expected_enabled=expected_enabled,
            enabled=enabled,
        )
    )


def _temporary_files(project_root: Path) -> list[Path]:
    return list(project_root.glob(".conf.yaml.*.tmp"))


@pytest.mark.parametrize(("initial", "target"), [(False, True), (True, False)])
def test_atomic_writer_persists_only_enabled_and_reloads(
    tmp_path: Path,
    initial: bool,
    target: bool,
) -> None:
    project_root, original = _create_project(tmp_path, enabled=initial)
    writer = AtomicMemoryConfigWriter(project_root)

    result = _run_update(
        writer,
        expected_enabled=initial,
        enabled=target,
    )

    persisted = yaml.safe_load((project_root / "conf.yaml").read_text("utf-8"))
    expected = deepcopy(original)
    expected["memory_config"]["enabled"] = target
    assert result.status == MemoryManagementStatus.SUCCESS
    assert result.changed is True
    assert result.enabled is target
    assert result.reason_code is None
    assert persisted == expected
    assert _temporary_files(project_root) == []


def test_atomic_writer_preserves_environment_placeholder_instead_of_secret(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_root, _ = _create_project(tmp_path)
    placeholder = "${ELYSIA_LLM_API_KEY}"
    private_secret = "PRIVATE_ATOMIC_WRITER_SECRET"
    monkeypatch.setenv("ELYSIA_LLM_API_KEY", private_secret)

    result = _run_update(
        AtomicMemoryConfigWriter(project_root),
        expected_enabled=False,
        enabled=True,
    )

    persisted_text = (project_root / "conf.yaml").read_text("utf-8")
    assert result.status == MemoryManagementStatus.SUCCESS
    assert placeholder in persisted_text
    assert private_secret not in persisted_text


def test_atomic_writer_adds_only_enabled_for_legacy_config_without_memory_section(
    tmp_path: Path,
) -> None:
    project_root, original = _create_project(tmp_path, include_memory_section=False)

    result = _run_update(
        AtomicMemoryConfigWriter(project_root),
        expected_enabled=False,
        enabled=True,
    )

    persisted = yaml.safe_load((project_root / "conf.yaml").read_text("utf-8"))
    assert result.status == MemoryManagementStatus.SUCCESS
    assert persisted["memory_config"] == {"enabled": True}
    persisted_without_memory = deepcopy(persisted)
    persisted_without_memory.pop("memory_config")
    assert persisted_without_memory == original


def test_atomic_writer_rejects_stale_expected_state_without_writing(
    tmp_path: Path,
) -> None:
    project_root, _ = _create_project(tmp_path, enabled=True)
    config_path = project_root / "conf.yaml"
    original_bytes = config_path.read_bytes()

    result = _run_update(
        AtomicMemoryConfigWriter(project_root),
        expected_enabled=False,
        enabled=False,
    )

    assert result.status == MemoryManagementStatus.REJECTED
    assert result.changed is False
    assert result.enabled is True
    assert result.reason_code == MemoryManagementReasonCode.SETTING_CONFLICT
    assert config_path.read_bytes() == original_bytes
    assert _temporary_files(project_root) == []


def test_atomic_writer_idempotent_update_does_not_rewrite_file(tmp_path: Path) -> None:
    project_root, _ = _create_project(tmp_path, enabled=False)
    config_path = project_root / "conf.yaml"
    original_bytes = config_path.read_bytes()

    result = _run_update(
        AtomicMemoryConfigWriter(project_root),
        expected_enabled=False,
        enabled=False,
    )

    assert result.status == MemoryManagementStatus.SUCCESS
    assert result.changed is False
    assert result.enabled is False
    assert config_path.read_bytes() == original_bytes
    assert _temporary_files(project_root) == []


@pytest.mark.parametrize("failure_point", ["fsync", "replace"])
def test_atomic_writer_failure_keeps_official_config_and_cleans_temporary_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure_point: str,
) -> None:
    project_root, _ = _create_project(tmp_path)
    config_path = project_root / "conf.yaml"
    original_bytes = config_path.read_bytes()

    def fail(*_args, **_kwargs):
        raise OSError("PRIVATE_FAILURE_DETAIL")

    monkeypatch.setattr(setting_persistence.os, failure_point, fail)
    result = _run_update(
        AtomicMemoryConfigWriter(project_root),
        expected_enabled=False,
        enabled=True,
    )

    assert result.status == MemoryManagementStatus.FAILED
    assert result.changed is False
    assert result.enabled is False
    assert result.reason_code == MemoryManagementReasonCode.CONFIG_PERSIST_FAILURE
    assert config_path.read_bytes() == original_bytes
    assert _temporary_files(project_root) == []


def test_atomic_writer_temporary_creation_failure_keeps_official_config(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_root, _ = _create_project(tmp_path)
    config_path = project_root / "conf.yaml"
    original_bytes = config_path.read_bytes()

    def fail_creation(*_args, **_kwargs):
        raise OSError("cannot create temporary file")

    monkeypatch.setattr(setting_persistence.tempfile, "mkstemp", fail_creation)
    result = _run_update(
        AtomicMemoryConfigWriter(project_root),
        expected_enabled=False,
        enabled=True,
    )

    assert result.status == MemoryManagementStatus.FAILED
    assert config_path.read_bytes() == original_bytes
    assert _temporary_files(project_root) == []


def test_atomic_writer_serialization_failure_keeps_official_config(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_root, _ = _create_project(tmp_path)
    config_path = project_root / "conf.yaml"
    original_bytes = config_path.read_bytes()
    original_dump = setting_persistence.yaml.safe_dump

    def fail_file_dump(data, stream=None, **kwargs):
        if stream is not None:
            raise yaml.YAMLError("cannot serialize temporary configuration")
        return original_dump(data, stream, **kwargs)

    monkeypatch.setattr(setting_persistence.yaml, "safe_dump", fail_file_dump)
    result = _run_update(
        AtomicMemoryConfigWriter(project_root),
        expected_enabled=False,
        enabled=True,
    )

    assert result.status == MemoryManagementStatus.FAILED
    assert config_path.read_bytes() == original_bytes
    assert _temporary_files(project_root) == []


def test_atomic_writer_rejects_invalid_temporary_content_before_replace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_root, _ = _create_project(tmp_path)
    config_path = project_root / "conf.yaml"
    original_bytes = config_path.read_bytes()
    original_parser = AtomicMemoryConfigWriter._parse_yaml
    parse_calls = 0

    def corrupt_second_parse(content: bytes):
        nonlocal parse_calls
        parse_calls += 1
        parsed = original_parser(content)
        if parse_calls == 2:
            parsed["memory_config"]["enabled"] = False
        return parsed

    monkeypatch.setattr(
        AtomicMemoryConfigWriter,
        "_parse_yaml",
        staticmethod(corrupt_second_parse),
    )
    result = _run_update(
        AtomicMemoryConfigWriter(project_root),
        expected_enabled=False,
        enabled=True,
    )

    assert result.status == MemoryManagementStatus.FAILED
    assert parse_calls == 2
    assert config_path.read_bytes() == original_bytes
    assert _temporary_files(project_root) == []


def test_atomic_writer_failure_logs_do_not_expose_content_path_or_error_detail(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_root, _ = _create_project(tmp_path)
    private_secret = "PRIVATE_ATOMIC_LOG_SECRET"
    private_detail = "PRIVATE_ATOMIC_ERROR_DETAIL"
    monkeypatch.setenv("ELYSIA_LLM_API_KEY", private_secret)
    logs: list[str] = []
    sink_id = logger.add(
        lambda message: logs.append(str(message)),
        format="{level} {message}",
        level="DEBUG",
    )

    def fail_replace(*_args, **_kwargs):
        raise OSError(private_detail)

    monkeypatch.setattr(setting_persistence.os, "replace", fail_replace)
    try:
        result = _run_update(
            AtomicMemoryConfigWriter(project_root),
            expected_enabled=False,
            enabled=True,
        )
    finally:
        logger.remove(sink_id)

    output = "".join(logs)
    assert result.status == MemoryManagementStatus.FAILED
    assert "phase=replace" in output
    assert "exception_type=OSError" in output
    assert private_secret not in output
    assert private_detail not in output
    assert str(project_root) not in output


def test_atomic_writer_serializes_concurrent_compare_and_set_requests(
    tmp_path: Path,
) -> None:
    project_root, _ = _create_project(tmp_path, enabled=False)
    writer = AtomicMemoryConfigWriter(project_root)

    async def run_concurrently():
        return await asyncio.gather(
            writer.update_enabled(expected_enabled=False, enabled=True),
            writer.update_enabled(expected_enabled=False, enabled=True),
        )

    results = asyncio.run(run_concurrently())
    statuses = [result.status for result in results]
    persisted = yaml.safe_load((project_root / "conf.yaml").read_text("utf-8"))

    assert statuses.count(MemoryManagementStatus.SUCCESS) == 1
    assert statuses.count(MemoryManagementStatus.REJECTED) == 1
    assert persisted["memory_config"]["enabled"] is True
    assert _temporary_files(project_root) == []


def test_atomic_writer_rejects_non_boolean_inputs(tmp_path: Path) -> None:
    project_root, _ = _create_project(tmp_path)

    with pytest.raises(TypeError):
        _run_update(
            AtomicMemoryConfigWriter(project_root),
            expected_enabled=False,
            enabled=1,
        )
