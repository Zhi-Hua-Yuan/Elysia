from copy import deepcopy
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from open_llm_vtuber.config_manager import MemoryConfig, validate_config


PROJECT_ROOT = Path(__file__).resolve().parents[1]
TEMPLATE_PATHS = (
    PROJECT_ROOT / "config_templates" / "conf.default.yaml",
    PROJECT_ROOT / "config_templates" / "conf.ZH.default.yaml",
    PROJECT_ROOT / "config_templates" / "conf.elysia.example.yaml",
)
EXPECTED_MEMORY_CONFIG = {
    "enabled": False,
    "profile_id": "local_default",
    "storage_dir": "memory_data",
    "max_items": 24,
    "max_item_chars": 160,
    "max_context_chars": 800,
    "explicit_capture": True,
    "implicit_capture": False,
}


def test_memory_config_defaults_are_disabled_and_bounded() -> None:
    config = MemoryConfig()

    assert config.enabled is False
    assert config.profile_id == "local_default"
    assert config.storage_dir == "memory_data"
    assert config.max_items == 24
    assert config.max_item_chars == 160
    assert config.max_context_chars == 800
    assert config.explicit_capture is True
    assert config.implicit_capture is False


def test_old_config_without_memory_section_remains_valid() -> None:
    template_data = yaml.safe_load(TEMPLATE_PATHS[-1].read_text(encoding="utf-8"))
    old_config_data = deepcopy(template_data)
    old_config_data.pop("memory_config")

    config = validate_config(old_config_data)

    assert config.memory_config == MemoryConfig()
    assert config.memory_config.enabled is False


@pytest.mark.parametrize(
    "profile_id",
    ["", "contains space", "../profile", "profile/name", "资料", "a" * 65],
)
def test_profile_id_rejects_unsafe_or_unstable_values(profile_id: str) -> None:
    with pytest.raises(ValidationError):
        MemoryConfig(profile_id=profile_id)


@pytest.mark.parametrize(
    "storage_dir",
    ["", ".", "/tmp/memory", "C:\\memory", "../memory", "memory/../other"],
)
def test_storage_dir_rejects_absolute_or_traversing_paths(storage_dir: str) -> None:
    with pytest.raises(ValidationError):
        MemoryConfig(storage_dir=storage_dir)


def test_storage_dir_allows_a_controlled_nested_relative_path() -> None:
    config = MemoryConfig(storage_dir="local_data/memory")

    assert config.storage_dir == "local_data/memory"


@pytest.mark.parametrize(
    ("field_name", "invalid_value"),
    [
        ("max_items", 0),
        ("max_items", 101),
        ("max_item_chars", 19),
        ("max_item_chars", 501),
        ("max_context_chars", 99),
        ("max_context_chars", 4001),
    ],
)
def test_memory_limits_enforce_contract_ranges(
    field_name: str, invalid_value: int
) -> None:
    with pytest.raises(ValidationError):
        MemoryConfig(**{field_name: invalid_value})


def test_implicit_capture_cannot_be_enabled_in_m5() -> None:
    with pytest.raises(ValidationError):
        MemoryConfig(implicit_capture=True)


@pytest.mark.parametrize("template_path", TEMPLATE_PATHS, ids=lambda path: path.name)
def test_all_config_templates_share_the_frozen_memory_contract(
    template_path: Path,
) -> None:
    template_data = yaml.safe_load(template_path.read_text(encoding="utf-8"))

    assert template_data["memory_config"] == EXPECTED_MEMORY_CONFIG
    assert validate_config(template_data).memory_config == MemoryConfig()
