from pathlib import Path
from unittest.mock import Mock, patch

import httpx
import pytest
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


@pytest.mark.parametrize(
    ("status_code", "reason"),
    [
        (400, "invalid reference_id or request parameters"),
        (401, "invalid or missing API key"),
        (402, "insufficient API credit"),
        (429, "rate limit exceeded"),
        (500, "provider request failed"),
    ],
)
def test_provider_errors_have_safe_actionable_summaries(
    status_code: int, reason: str
) -> None:
    private_response = "PRIVATE_PROVIDER_RESPONSE"
    client = Mock()
    client.post.return_value = Mock(
        status_code=status_code,
        content=private_response.encode(),
    )
    logs: list[str] = []
    sink_id = logger.add(
        lambda message: logs.append(str(message)),
        format="{level} {message}",
        level="DEBUG",
    )

    try:
        result = _build_engine(client).generate_audio("PRIVATE_TTS_TEXT")
    finally:
        logger.remove(sink_id)

    output = "".join(logs)
    assert result is None
    assert f"status={status_code}" in output
    assert reason in output
    assert private_response not in output
    assert "PRIVATE_TTS_TEXT" not in output
    assert API_KEY not in output


def test_invalid_wav_is_rejected_without_creating_cache_file(tmp_path: Path) -> None:
    client = Mock()
    client.post.return_value = Mock(status_code=200, content=b"not audio")
    engine = _build_engine(client)
    audio_path = tmp_path / "fish.wav"
    engine.generate_cache_file_name = Mock(return_value=str(audio_path))

    result = engine.generate_audio("你好。")

    assert result is None
    assert not audio_path.exists()
    assert not Path(f"{audio_path}.part").exists()


def test_transport_error_is_reported_without_provider_details() -> None:
    client = Mock()
    client.post.side_effect = httpx.ConnectError("PRIVATE_PROVIDER_RESPONSE")
    logs: list[str] = []
    sink_id = logger.add(
        lambda message: logs.append(str(message)),
        format="{level} {message}",
        level="DEBUG",
    )

    try:
        result = _build_engine(client).generate_audio("PRIVATE_TTS_TEXT")
    finally:
        logger.remove(sink_id)

    output = "".join(logs)
    assert result is None
    assert "transport error" in output
    assert "ConnectError" in output
    assert "PRIVATE_PROVIDER_RESPONSE" not in output
    assert "PRIVATE_TTS_TEXT" not in output
    assert API_KEY not in output


def test_failed_cache_commit_removes_partial_audio(tmp_path: Path) -> None:
    client = Mock()
    client.post.return_value = Mock(status_code=200, content=WAV_BYTES)
    engine = _build_engine(client)
    audio_path = tmp_path / "fish.wav"
    engine.generate_cache_file_name = Mock(return_value=str(audio_path))

    with patch(
        "open_llm_vtuber.tts.fish_api_tts.os.replace",
        side_effect=OSError("PRIVATE_FILE_ERROR"),
    ):
        result = engine.generate_audio("PRIVATE_TTS_TEXT")

    assert result is None
    assert not audio_path.exists()
    assert not Path(f"{audio_path}.part").exists()


@pytest.mark.parametrize("api_key", ["", "${ELYSIA_FISH_API_KEY}"])
def test_missing_or_unresolved_api_key_fails_at_startup(api_key: str) -> None:
    with pytest.raises(ValueError, match="Fish Audio API key is required"):
        TTSEngine(
            api_key=api_key,
            reference_id=REFERENCE_ID,
            model="s2.1-pro-free",
        )


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
