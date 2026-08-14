"""Process-boundary acceptance tests for the M5 memory total switch."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest
import yaml


PROJECT_ROOT = Path(__file__).resolve().parents[1]
TEMPLATE_PATH = PROJECT_ROOT / "config_templates" / "conf.elysia.example.yaml"
WORKER_PATH = PROJECT_ROOT / "tests" / "_support" / "memory_setting_restart_worker.py"
MEMORY_MARKER = "CROSS_PROCESS_MEMORY_MARKER"
SECRET = "PRIVATE_RESTART_TEST_SECRET"


def _create_project(tmp_path: Path, *, enabled: bool = False) -> Path:
    raw = yaml.safe_load(TEMPLATE_PATH.read_text(encoding="utf-8"))
    raw["memory_config"]["enabled"] = enabled
    (tmp_path / "conf.yaml").write_text(
        yaml.safe_dump(raw, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    return tmp_path


def _run_worker(
    project_root: Path,
    command: str,
    *arguments: str,
    expect_success: bool = True,
) -> tuple[dict, str]:
    child_environment = os.environ.copy()
    existing_python_path = child_environment.get("PYTHONPATH")
    import_paths = os.pathsep.join((str(PROJECT_ROOT / "src"), str(PROJECT_ROOT)))
    child_environment["PYTHONPATH"] = (
        import_paths
        if not existing_python_path
        else import_paths + os.pathsep + existing_python_path
    )
    completed = subprocess.run(
        [sys.executable, str(WORKER_PATH), command, str(project_root), *arguments],
        cwd=PROJECT_ROOT,
        env=child_environment,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
        check=False,
    )
    lines = [line for line in completed.stdout.splitlines() if line.strip()]
    payload = json.loads(lines[-1]) if lines else {}
    if expect_success:
        assert completed.returncode == 0, {
            "returncode": completed.returncode,
            "error_type": payload.get("error_type", "missing_result"),
        }
        assert payload.get("ok") is True
    else:
        assert completed.returncode != 0
        assert payload.get("ok") is False
    return payload, completed.stdout + completed.stderr


def _toggle(project_root: Path, expected: bool, enabled: bool, **kwargs) -> dict:
    result, _ = _run_worker(
        project_root,
        "toggle",
        "--expected-enabled",
        str(expected).lower(),
        "--enabled",
        str(enabled).lower(),
        **kwargs,
    )
    return result


def _inspect(project_root: Path, marker: str | None = None, **kwargs) -> dict:
    arguments = ("--marker", marker) if marker is not None else ()
    result, _ = _run_worker(project_root, "inspect", *arguments, **kwargs)
    return result


def _memory_document_path(project_root: Path) -> Path:
    return project_root / "memory_data" / "local_default" / "elysia_mvp_001.json"


def test_fresh_interpreters_round_trip_enabled_state_and_new_session(
    tmp_path: Path,
) -> None:
    project_root = _create_project(tmp_path)

    enabled = _toggle(project_root, False, True)
    enabled_restart = _inspect(project_root)
    disabled = _toggle(project_root, True, False)
    disabled_restart = _inspect(project_root)

    assert enabled["changed"] is True
    assert enabled["enabled"] is True
    assert enabled["runtime_enabled"] is True
    assert enabled["default_context_enabled"] is True
    assert enabled["session_enabled"] is True
    assert enabled_restart["enabled"] is True
    assert enabled_restart["can_create"] is True
    assert disabled["changed"] is True
    assert disabled["enabled"] is False
    assert disabled["runtime_enabled"] is False
    assert disabled["default_context_enabled"] is False
    assert disabled["session_enabled"] is False
    assert disabled_restart["enabled"] is False
    assert disabled_restart["can_create"] is False
    assert list(project_root.glob(".conf.yaml.*.tmp")) == []


def test_restart_inspection_is_read_only_and_ignores_stale_temporary_file(
    tmp_path: Path,
) -> None:
    project_root = _create_project(tmp_path, enabled=True)
    config_path = project_root / "conf.yaml"
    stale_temporary = project_root / ".conf.yaml.interrupted.tmp"
    stale_temporary.write_text("not: [a valid replacement", encoding="utf-8")
    original_bytes = config_path.read_bytes()
    original_mtime = config_path.stat().st_mtime_ns

    result = _inspect(project_root)

    assert result["enabled"] is True
    assert config_path.read_bytes() == original_bytes
    assert config_path.stat().st_mtime_ns == original_mtime
    assert stale_temporary.read_text(encoding="utf-8") == "not: [a valid replacement"
    assert not _memory_document_path(project_root).exists()


def test_memory_document_survives_disabled_and_reenabled_restarts(
    tmp_path: Path,
) -> None:
    project_root = _create_project(tmp_path, enabled=True)
    seeded, _ = _run_worker(project_root, "seed", "--marker", MEMORY_MARKER)
    memory_path = _memory_document_path(project_root)
    original_memory = memory_path.read_bytes()

    _toggle(project_root, True, False)
    disabled = _inspect(project_root, MEMORY_MARKER)

    assert disabled["enabled"] is False
    assert disabled["can_create"] is False
    assert disabled["can_delete"] is True
    assert disabled["can_clear"] is True
    assert disabled["disabled_create_status"] == "rejected"
    assert disabled["disabled_create_reason"] == "memory_disabled"
    assert disabled["list_item_count"] == 1
    assert disabled["list_contains_marker"] is True
    assert disabled["context_empty"] is True
    assert disabled["context_contains_marker"] is False
    assert memory_path.read_bytes() == original_memory

    _toggle(project_root, False, True)
    reenabled = _inspect(project_root, MEMORY_MARKER)

    assert seeded["revision"] == reenabled["revision"] == 1
    assert reenabled["enabled"] is True
    assert reenabled["can_create"] is True
    assert reenabled["list_contains_marker"] is True
    assert reenabled["context_empty"] is False
    assert reenabled["context_contains_marker"] is True
    assert memory_path.read_bytes() == original_memory


def test_environment_placeholder_and_secret_survive_restart_toggle(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_root = _create_project(tmp_path)
    monkeypatch.setenv("ELYSIA_LLM_API_KEY", SECRET)

    _, toggle_output = _run_worker(
        project_root,
        "toggle",
        "--expected-enabled",
        "false",
        "--enabled",
        "true",
    )
    _, inspect_output = _run_worker(project_root, "inspect")

    persisted = (project_root / "conf.yaml").read_text(encoding="utf-8")
    assert "${ELYSIA_LLM_API_KEY}" in persisted
    assert SECRET not in persisted
    assert SECRET not in toggle_output
    assert SECRET not in inspect_output


@pytest.mark.parametrize(
    ("content", "expected_error"),
    [
        (None, "FileNotFoundError"),
        ("memory_config:\n  enabled: [PRIVATE_BROKEN_CONFIG", "ParserError"),
    ],
)
def test_invalid_or_missing_config_fails_safely_without_rewrite(
    tmp_path: Path,
    content: str | None,
    expected_error: str,
) -> None:
    config_path = tmp_path / "conf.yaml"
    if content is not None:
        config_path.write_text(content, encoding="utf-8")
    original_bytes = config_path.read_bytes() if config_path.exists() else None

    result, output = _run_worker(
        tmp_path,
        "inspect",
        expect_success=False,
    )

    assert result["error_type"] == expected_error
    assert "PRIVATE_BROKEN_CONFIG" not in output
    if original_bytes is None:
        assert not config_path.exists()
    else:
        assert config_path.read_bytes() == original_bytes
    assert list(tmp_path.glob(".conf.yaml.*.tmp")) == []
