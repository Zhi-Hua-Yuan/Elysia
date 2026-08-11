import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

import pytest
from loguru import logger

from open_llm_vtuber.tts.edge_tts import TTSEngine


def _engine_for_path(audio_path: Path) -> TTSEngine:
    engine = TTSEngine(voice="zh-CN-XiaoxiaoNeural")
    engine.generate_cache_file_name = Mock(return_value=str(audio_path))
    return engine


def test_async_generate_audio_uses_native_save(tmp_path: Path) -> None:
    audio_path = tmp_path / "native-async.mp3"
    engine = _engine_for_path(audio_path)
    communicate = Mock()
    communicate.save_sync = Mock()

    async def save(path: str) -> None:
        Path(path).write_bytes(b"complete-audio")

    communicate.save = AsyncMock(side_effect=save)

    with patch(
        "open_llm_vtuber.tts.edge_tts.edge_tts.Communicate",
        return_value=communicate,
    ) as communicate_class:
        result = asyncio.run(
            engine.async_generate_audio("你好", file_name_no_ext="ignored")
        )

    assert result == str(audio_path)
    assert audio_path.read_bytes() == b"complete-audio"
    communicate_class.assert_called_once_with("你好", "zh-CN-XiaoxiaoNeural")
    communicate.save.assert_awaited_once_with(str(audio_path))
    communicate.save_sync.assert_not_called()


def test_async_generate_audio_removes_partial_file_and_redacts_error(
    tmp_path: Path,
) -> None:
    audio_path = tmp_path / "partial-error.mp3"
    engine = _engine_for_path(audio_path)
    secret_text = "PRIVATE_TTS_INPUT"
    logs: list[str] = []
    sink_id = logger.add(
        lambda message: logs.append(str(message)),
        format="{level} {message}",
        level="DEBUG",
    )

    async def fail(path: str) -> None:
        Path(path).write_bytes(b"partial-audio")
        raise RuntimeError(secret_text)

    communicate = Mock(save=AsyncMock(side_effect=fail))

    try:
        with patch(
            "open_llm_vtuber.tts.edge_tts.edge_tts.Communicate",
            return_value=communicate,
        ):
            result = asyncio.run(engine.async_generate_audio(secret_text))
    finally:
        logger.remove(sink_id)

    assert result is None
    assert not audio_path.exists()
    output = "".join(logs)
    assert "error_type=RuntimeError" in output
    assert secret_text not in output


def test_async_generate_audio_propagates_cancellation_and_cleans_up(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        audio_path = tmp_path / "partial-cancel.mp3"
        engine = _engine_for_path(audio_path)
        save_started = asyncio.Event()
        release_save = asyncio.Event()

        async def save(path: str) -> None:
            Path(path).write_bytes(b"partial-audio")
            save_started.set()
            await release_save.wait()

        communicate = Mock(save=AsyncMock(side_effect=save))

        with patch(
            "open_llm_vtuber.tts.edge_tts.edge_tts.Communicate",
            return_value=communicate,
        ):
            task = asyncio.create_task(engine.async_generate_audio("请取消"))
            await asyncio.wait_for(save_started.wait(), timeout=1)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task

        assert not audio_path.exists()

    asyncio.run(scenario())


def test_sync_generate_audio_remains_available(tmp_path: Path) -> None:
    audio_path = tmp_path / "sync-compatible.mp3"
    engine = _engine_for_path(audio_path)

    def save_sync(path: str) -> None:
        Path(path).write_bytes(b"sync-audio")

    communicate = Mock(save_sync=Mock(side_effect=save_sync))

    with patch(
        "open_llm_vtuber.tts.edge_tts.edge_tts.Communicate",
        return_value=communicate,
    ):
        result = engine.generate_audio("同步兼容")

    assert result == str(audio_path)
    assert audio_path.read_bytes() == b"sync-audio"
    communicate.save_sync.assert_called_once_with(str(audio_path))
