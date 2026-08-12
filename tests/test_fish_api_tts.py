from pathlib import Path
from unittest.mock import Mock, patch

import httpx
from loguru import logger

from open_llm_vtuber.config_manager.tts import FishAPITTSConfig
from open_llm_vtuber.tts.fish_api_tts import TTSEngine


API_KEY = "PRIVATE_FISH_API_KEY"
REFERENCE_ID = "aca922eff5f0446fbfd395fe03e48f35"
WAV_BYTES = b"RIFF\x04\x00\x00\x00WAVE"


def _build_engine(client: Mock) -> TTSEngine:
    with patch("open_llm_vtuber.tts.fish_api_tts.httpx.Client", return_value=client):
        return TTSEngine(
            api_key=API_KEY,
            reference_id=REFERENCE_ID,
            model="s2.1-pro-free",
        )


def test_generate_audio_uses_free_model_and_wav(tmp_path: Path) -> None:
    response = Mock(status_code=200, content=WAV_BYTES)
    client = Mock()
    client.post.return_value = response
    engine = _build_engine(client)
    audio_path = tmp_path / "fish.wav"
    engine.generate_cache_file_name = Mock(return_value=str(audio_path))

    result = engine.generate_audio("你好，欢迎回来。")

    assert result == str(audio_path)
    assert audio_path.read_bytes() == WAV_BYTES
    client.post.assert_called_once_with(
        "v1/tts",
        headers={"model": "s2.1-pro-free"},
        json={
            "text": "你好，欢迎回来。",
            "reference_id": REFERENCE_ID,
            "latency": "balanced",
            "format": "wav",
        },
    )


def test_api_key_and_text_are_not_logged_on_provider_error() -> None:
    private_text = "PRIVATE_TTS_TEXT"
    client = Mock()
    client.post.return_value = Mock(status_code=401, content=b"")
    logs: list[str] = []
    sink_id = logger.add(
        lambda message: logs.append(str(message)),
        format="{level} {message}",
        level="DEBUG",
    )

    try:
        engine = _build_engine(client)
        result = engine.generate_audio(private_text)
    finally:
        logger.remove(sink_id)

    assert result is None
    output = "".join(logs)
    assert "status=401" in output
    assert API_KEY not in output
    assert private_text not in output


def test_timeout_is_reported_without_leaking_request() -> None:
    client = Mock()
    client.post.side_effect = httpx.ReadTimeout("PRIVATE_PROVIDER_RESPONSE")
    logs: list[str] = []
    sink_id = logger.add(
        lambda message: logs.append(str(message)),
        format="{level} {message}",
        level="DEBUG",
    )

    try:
        engine = _build_engine(client)
        result = engine.generate_audio("PRIVATE_TTS_TEXT")
    finally:
        logger.remove(sink_id)

    assert result is None
    output = "".join(logs)
    assert "timed out" in output
    assert "PRIVATE_PROVIDER_RESPONSE" not in output
    assert "PRIVATE_TTS_TEXT" not in output
    assert API_KEY not in output


def test_fish_config_defaults_to_free_model() -> None:
    config = FishAPITTSConfig.model_validate(
        {
            "api_key": "placeholder",
            "reference_id": REFERENCE_ID,
            "latency": "balanced",
            "base_url": "https://api.fish.audio",
        }
    )

    assert config.model == "s2.1-pro-free"
