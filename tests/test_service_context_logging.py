import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from loguru import logger

from open_llm_vtuber.service_context import ServiceContext


def _character_config(secret: str) -> SimpleNamespace:
    return SimpleNamespace(
        conf_name="test-character",
        conf_uid="test-character-001",
        live2d_model_name="mao_pro",
        persona_prompt=secret,
        agent_config={"llm_api_key": secret},
        model_dump=lambda: {
            "conf_name": "test-character",
            "llm_api_key": secret,
            "persona_prompt": secret,
        },
    )


def test_service_context_summary_does_not_include_configuration_secrets() -> None:
    secret = "PRIVATE_SERVICE_CONTEXT_SECRET"
    context = ServiceContext()
    context.system_config = SimpleNamespace(model_dump=lambda: {"secret": secret})
    context.character_config = _character_config(secret)
    context.system_prompt = secret

    summary = str(context)

    assert "test-character" in summary
    assert "mao_pro" in summary
    assert secret not in summary
    assert "llm_api_key" not in summary
    assert "persona_prompt" not in summary


def test_config_switch_log_does_not_include_configuration_secrets() -> None:
    secret = "PRIVATE_CONFIG_SWITCH_SECRET"
    context = ServiceContext()
    context.system_config = SimpleNamespace(model_dump=lambda: {"secret": secret})
    context.character_config = _character_config(secret)
    context.live2d_model = SimpleNamespace(model_info={"name": "mao_pro"})
    context.load_from_config = AsyncMock()
    websocket = AsyncMock()
    logs: list[str] = []
    sink_id = logger.add(
        lambda message: logs.append(str(message)),
        format="{level} {message}",
        level="DEBUG",
    )

    try:
        with (
            patch(
                "open_llm_vtuber.service_context.read_yaml",
                return_value={"character_config": {"llm_api_key": secret}},
            ),
            patch(
                "open_llm_vtuber.service_context.validate_config",
                return_value=SimpleNamespace(),
            ),
        ):
            asyncio.run(context.handle_config_switch(websocket, "conf.yaml"))
    finally:
        logger.remove(sink_id)

    output = "".join(logs)
    assert "Configuration switched to conf.yaml" in output
    assert secret not in output
    assert "llm_api_key" not in output
