from loguru import logger
from pydantic import ValidationError
import pytest

from open_llm_vtuber.config_manager import validate_config


def test_validation_failure_does_not_log_configuration_values() -> None:
    private_api_key = "PRIVATE_FISH_API_KEY"
    private_endpoint = "https://private-endpoint.example/v1"
    private_persona = "PRIVATE_PERSONA_PROMPT"
    logs: list[str] = []
    sink_id = logger.add(
        lambda message: logs.append(str(message)),
        format="{level} {message}",
        level="DEBUG",
    )

    try:
        with pytest.raises(ValidationError):
            validate_config(
                {
                    "api_key": private_api_key,
                    "base_url": private_endpoint,
                    "persona_prompt": private_persona,
                }
            )
    finally:
        logger.remove(sink_id)

    output = "".join(logs)
    assert "Configuration validation failed" in output
    assert private_api_key not in output
    assert private_endpoint not in output
    assert private_persona not in output
