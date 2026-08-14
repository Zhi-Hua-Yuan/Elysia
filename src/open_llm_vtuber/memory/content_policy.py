"""Local, conservative safety policy for persistent-memory values."""

from __future__ import annotations

import re
import unicodedata

from .command_types import MemoryPolicyDecision
from .normalization import normalize_memory_text
from .types import MemoryCategory, MemoryReasonCode


MAX_PREFERRED_ADDRESS_CHARS = 32

_KNOWN_SECRET_PATTERN = re.compile(
    r"\b(?:sk|pk|rk|ghp|github_pat|xox[baprs])[-_][A-Za-z0-9_-]{8,}\b",
    re.IGNORECASE,
)
_CREDENTIAL_CONTEXT_PATTERN = re.compile(
    r"(?:api[ _-]?key|access[ _-]?token|refresh[ _-]?token|密钥|密码|口令|secret)"
    r"\s*(?:是|为|[:：=])\s*\S+",
    re.IGNORECASE,
)
_VERIFICATION_CODE_PATTERN = re.compile(
    r"(?:验证码|校验码|动态码|otp)\s*(?:是|为|[:：=])?\s*\d{4,8}",
    re.IGNORECASE,
)
_IDENTITY_PATTERN = re.compile(
    r"(?:身份证(?:号|号码)?|护照(?:号|号码)?|社会保障号|社保号)"
    r"\s*(?:是|为|[:：=])?\s*[A-Za-z0-9]{6,24}",
    re.IGNORECASE,
)
_BANK_PATTERN = re.compile(
    r"(?:银行卡(?:号|号码)?|银行账号|信用卡(?:号|号码)?)"
    r"\s*(?:是|为|[:：=])?\s*\d{12,19}"
)
_CONTACT_PATTERN = re.compile(
    r"(?:手机号|手机号码|电话号码|联系方式|邮箱(?:地址)?)"
    r"\s*(?:是|为|[:：=])\s*(?:\+?\d[\d -]{6,}|[\w.+-]+@[\w.-]+\.[A-Za-z]{2,})",
    re.IGNORECASE,
)
_FINANCIAL_PATTERN = re.compile(
    r"(?:我的|本人)(?:工资|收入|存款|资产|负债|账户余额)"
    r"\s*(?:是|为|[:：=])\s*\S+"
)
_MEDICAL_PATTERN = re.compile(
    r"(?:我|本人)(?:被诊断为|确诊为|患有|得了|正在服用|正在吃|病历(?:是|为)|"
    r"处方(?:是|为)|用药(?:是|为))|我的(?:诊断|病历|处方|用药记录)"
)
_PRECISE_ADDRESS_PATTERN = re.compile(
    r"(?:我住在|我的住址(?:是|为)?|我的地址(?:是|为)?|家庭地址(?:是|为)?)"
    r".{0,80}(?:路|街|巷|弄|号|栋|幢|单元|室)"
)
_INSTRUCTION_PATTERNS = (
    re.compile(r"忽略(?:之前|以上|所有|系统).{0,20}(?:规则|指令|提示词|要求)?"),
    re.compile(r"(?:覆盖|修改|绕过|取消).{0,12}(?:系统|安全|权限|提示词|规则)"),
    re.compile(r"(?:系统提示词|system\s*prompt)", re.IGNORECASE),
    re.compile(r"(?:调用|执行).{0,8}(?:mcp|工具|命令|脚本|终端|shell)", re.IGNORECASE),
    re.compile(r"(?:删除|读取|修改).{0,8}(?:文件|目录|配置|数据库)"),
    re.compile(r"无需.{0,12}(?:确认|授权)"),
    re.compile(r"\[/?persistent\s+user\s+facts\]", re.IGNORECASE),
)


class MemoryContentPolicy:
    """Validate explicit values locally without retaining sensitive matches."""

    def evaluate(
        self,
        *,
        category: MemoryCategory,
        value: str,
        max_item_chars: int,
    ) -> MemoryPolicyDecision:
        """Return normalized text only when every local rule allows storage."""
        if not isinstance(category, MemoryCategory):
            raise TypeError("category must be a MemoryCategory")
        if not isinstance(value, str):
            raise TypeError("memory value must be a string")
        if type(max_item_chars) is not int or max_item_chars <= 0:
            raise ValueError("max_item_chars must be a positive integer")

        if any(unicodedata.category(char).startswith("C") for char in value):
            return self._reject(MemoryReasonCode.INVALID_VALUE)

        normalized = normalize_memory_text(value)
        if not normalized or len(normalized) > max_item_chars:
            return self._reject(MemoryReasonCode.INVALID_VALUE)
        if category == MemoryCategory.PREFERRED_ADDRESS and len(normalized) > min(
            max_item_chars, MAX_PREFERRED_ADDRESS_CHARS
        ):
            return self._reject(MemoryReasonCode.INVALID_VALUE)

        if self._contains_sensitive_content(normalized):
            return self._reject(MemoryReasonCode.SENSITIVE_CONTENT)
        if self._contains_instructional_content(normalized):
            return self._reject(MemoryReasonCode.INVALID_VALUE)
        return MemoryPolicyDecision(allowed=True, normalized_value=normalized)

    @staticmethod
    def _contains_sensitive_content(value: str) -> bool:
        return any(
            pattern.search(value)
            for pattern in (
                _KNOWN_SECRET_PATTERN,
                _CREDENTIAL_CONTEXT_PATTERN,
                _VERIFICATION_CODE_PATTERN,
                _IDENTITY_PATTERN,
                _BANK_PATTERN,
                _CONTACT_PATTERN,
                _FINANCIAL_PATTERN,
                _MEDICAL_PATTERN,
                _PRECISE_ADDRESS_PATTERN,
            )
        )

    @staticmethod
    def _contains_instructional_content(value: str) -> bool:
        return any(pattern.search(value) for pattern in _INSTRUCTION_PATTERNS)

    @staticmethod
    def _reject(reason_code: MemoryReasonCode) -> MemoryPolicyDecision:
        return MemoryPolicyDecision(allowed=False, reason_code=reason_code)


def evaluate_memory_content(
    *,
    category: MemoryCategory,
    value: str,
    max_item_chars: int,
) -> MemoryPolicyDecision:
    """Evaluate a value with the stateless default policy."""
    return MemoryContentPolicy().evaluate(
        category=category,
        value=value,
        max_item_chars=max_item_chars,
    )
