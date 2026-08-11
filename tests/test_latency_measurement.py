import asyncio
import json
from time import perf_counter
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import numpy as np
from loguru import logger

from open_llm_vtuber.agent.output_types import DisplayText
from open_llm_vtuber.conversations.conversation_utils import process_user_input
from open_llm_vtuber.conversations.tts_manager import TTSTaskManager
from open_llm_vtuber.websocket_handler import WebSocketHandler


class _FakeTTS:
    def __init__(self, audio_path: str | None) -> None:
        self.audio_path = audio_path
        self.removed: list[str] = []

    async def async_generate_audio(self, text: str, file_name_no_ext=None):
        return self.audio_path

    def remove_file(self, filepath: str) -> None:
        self.removed.append(filepath)


def _payload(audio_path, display_text=None, actions=None, **_kwargs):
    if isinstance(display_text, DisplayText):
        display_text = display_text.to_dict()
    return {
        "type": "audio",
        "audio": "ENCODED_AUDIO" if audio_path else None,
        "volumes": [0.5] if audio_path else [],
        "slice_length": 20,
        "display_text": display_text,
        "actions": actions.to_dict() if actions else None,
        "forwarded": False,
    }


async def _run_tts_scenario(audio_path: str | None) -> list[dict]:
    sent: list[dict] = []

    async def websocket_send(message: str) -> None:
        sent.append(json.loads(message))

    manager = TTSTaskManager(
        turn_id="turn-tts-test",
        conversation_started_at=perf_counter() - 0.01,
    )
    tts = _FakeTTS(audio_path)

    with patch(
        "open_llm_vtuber.conversations.tts_manager.prepare_audio_payload",
        side_effect=_payload,
    ):
        await manager.speak(
            tts_text="测试语音",
            display_text=DisplayText(text="测试语音"),
            actions=None,
            live2d_model=SimpleNamespace(),
            tts_engine=tts,
            websocket_send=websocket_send,
        )
        await asyncio.gather(*manager.task_list)
        await asyncio.wait_for(manager._payload_queue.join(), timeout=1)

    sender_task = manager._sender_task
    manager.clear()
    if sender_task:
        await asyncio.gather(sender_task, return_exceptions=True)
    return sent


def _capture_logs(callback) -> str:
    logs: list[str] = []
    sink_id = logger.add(
        lambda message: logs.append(str(message)),
        format="{level} {message}",
        level="DEBUG",
    )
    try:
        callback()
    finally:
        logger.remove(sink_id)
    return "".join(logs)


def test_non_empty_audio_records_first_payload_metric() -> None:
    logs = _capture_logs(lambda: asyncio.run(_run_tts_scenario("audio.wav")))

    assert "turn_id=turn-tts-test stage=tts" in logs
    assert "success=True has_audio=True" in logs
    assert "stage=first_audio_payload_sent" in logs


def test_silent_payload_is_not_recorded_as_first_audio() -> None:
    logs = _capture_logs(lambda: asyncio.run(_run_tts_scenario(None)))

    assert "turn_id=turn-tts-test stage=tts" in logs
    assert "success=False has_audio=False" in logs
    assert "stage=first_audio_payload_sent" not in logs


def test_asr_metric_contains_turn_id() -> None:
    asr = SimpleNamespace(
        async_transcribe_np=AsyncMock(return_value="识别结果"),
    )
    websocket_send = AsyncMock()

    logs = _capture_logs(
        lambda: asyncio.run(
            process_user_input(
                np.zeros(160, dtype=np.float32),
                asr,
                websocket_send,
                turn_id="turn-asr-test",
            )
        )
    )

    assert "turn_id=turn-asr-test stage=asr" in logs


def test_frontend_signal_is_marked_as_unverified() -> None:
    handler = WebSocketHandler(default_context_cache=None)
    handler.conversation_started_at["client-test"] = (
        perf_counter() - 0.01,
        "text-input",
        "turn-frontend-test",
    )

    logs = _capture_logs(
        lambda: asyncio.run(
            handler._handle_audio_play_start(
                AsyncMock(),
                "client-test",
                {"type": "audio-play-start"},
            )
        )
    )

    assert "turn_id=turn-frontend-test" in logs
    assert "stage=frontend_audio_start_signal" in logs
    assert "audio_presence=unverified" in logs
    assert "stage=first_audio_playback" not in logs


def test_trigger_passes_same_turn_id_to_conversation() -> None:
    handler = WebSocketHandler(default_context_cache=None)
    handler.client_contexts["client-test"] = SimpleNamespace()

    with patch(
        "open_llm_vtuber.websocket_handler.handle_conversation_trigger",
        new_callable=AsyncMock,
    ) as trigger:
        asyncio.run(
            handler._handle_conversation_trigger(
                AsyncMock(),
                "client-test",
                {"type": "text-input", "text": "你好"},
            )
        )

    stored_started_at, stored_input_type, stored_turn_id = (
        handler.conversation_started_at["client-test"]
    )
    kwargs = trigger.await_args.kwargs
    assert stored_input_type == "text-input"
    assert kwargs["turn_id"] == stored_turn_id
    assert kwargs["conversation_started_at"] == stored_started_at
