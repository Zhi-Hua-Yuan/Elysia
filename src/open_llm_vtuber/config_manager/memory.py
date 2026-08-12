"""Configuration contract for lightweight persistent memory."""

from pathlib import PurePosixPath, PureWindowsPath
from typing import ClassVar, Dict, Literal

from pydantic import Field, field_validator

from ..memory.types import validate_profile_id
from .i18n import Description, I18nMixin


class MemoryConfig(I18nMixin):
    """Settings for the opt-in lightweight persistent memory feature."""

    enabled: bool = Field(False, alias="enabled")
    profile_id: str = Field("local_default", alias="profile_id")
    storage_dir: str = Field("memory_data", alias="storage_dir")
    max_items: int = Field(24, ge=1, le=100, alias="max_items")
    max_item_chars: int = Field(160, ge=20, le=500, alias="max_item_chars")
    max_context_chars: int = Field(800, ge=100, le=4000, alias="max_context_chars")
    explicit_capture: bool = Field(True, alias="explicit_capture")
    implicit_capture: Literal[False] = Field(False, alias="implicit_capture")

    DESCRIPTIONS: ClassVar[Dict[str, Description]] = {
        "enabled": Description(
            en="Enable lightweight persistent memory",
            zh="启用轻量持久记忆",
        ),
        "profile_id": Description(
            en="Stable local profile identifier used to isolate memory",
            zh="用于隔离记忆的稳定本地资料标识",
        ),
        "storage_dir": Description(
            en="Controlled relative directory for local memory data",
            zh="本地记忆数据使用的受控相对目录",
        ),
        "max_items": Description(
            en="Maximum number of active memory items per scope",
            zh="每个记忆作用域允许的最大有效条目数",
        ),
        "max_item_chars": Description(
            en="Maximum character count for one memory value",
            zh="单条记忆值允许的最大字符数",
        ),
        "max_context_chars": Description(
            en="Maximum character budget for memory context per LLM turn",
            zh="每轮 LLM 记忆上下文的最大字符预算",
        ),
        "explicit_capture": Description(
            en="Allow explicit user memory commands",
            zh="允许处理用户明确发出的记忆命令",
        ),
        "implicit_capture": Description(
            en="Implicit memory capture; fixed to false for M5",
            zh="隐式记忆提取；M5 阶段固定为关闭",
        ),
    }

    @field_validator("profile_id")
    @classmethod
    def validate_config_profile_id(cls, value: str) -> str:
        """Keep the configured profile identifier safe and stable."""
        return validate_profile_id(value)

    @field_validator("storage_dir")
    @classmethod
    def validate_storage_dir(cls, value: str) -> str:
        """Accept only controlled relative paths on POSIX and Windows."""
        if not value or value != value.strip():
            raise ValueError("storage_dir must be a non-empty trimmed path")
        if any(ord(char) < 32 or ord(char) == 127 for char in value):
            raise ValueError("storage_dir must not contain control characters")

        posix_path = PurePosixPath(value)
        windows_path = PureWindowsPath(value)
        if posix_path.is_absolute() or windows_path.is_absolute() or windows_path.drive:
            raise ValueError("storage_dir must be a relative path without a drive")
        if ".." in posix_path.parts or ".." in windows_path.parts:
            raise ValueError("storage_dir must not contain parent traversal")
        if value in {".", "./", ".\\"}:
            raise ValueError("storage_dir must identify a child directory")

        return value
