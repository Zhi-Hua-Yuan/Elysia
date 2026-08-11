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


class _ExclusiveTTS:
    def __init__(self) -> None:
        self.started: list[str] = []
        self.first_release = asyncio.Event()
        self.later_release = asyncio.Event()
        self.two_later_started = asyncio.Event()

    async def async_generate_audio(self, text: str, file_name_no_ext=None):
        self.started.append(text)
        if text == "第一段":
            await self.first_release.wait()
        else:
            later_count = len([item for item in self.started if item != "第一段"])
            if later_count == 2:
                self.two_later_started.set()
            await self.later_release.wait()
        return f"{text}.wav"

    def remove_file(self, filepath: str) -> None:
        return None


class _FirstFailureTTS:
    def __init__(self) -> None:
        self.started: list[str] = []

    async def async_generate_audio(self, text: str, file_name_no_ext=None):
        self.started.append(text)
        if text == "第一段":
            raise RuntimeError("simulated first TTS failure")
        return f"{text}.wav"

    def remove_file(self, filepath: str) -> None:
        return None


class _ConcurrencyLimitedTTS:
    def __init__(self, later_texts: tuple[str, ...]) -> None:
        self.started: list[str] = []
        self.first_release = asyncio.Event()
        self.releases = {text: asyncio.Event() for text in later_texts}
        self.active_later: set[str] = set()
        self.max_active_later = 0
        self.two_later_started = asyncio.Event()
        self.third_later_started = asyncio.Event()

    async def async_generate_audio(self, text: str, file_name_no_ext=None):
        self.started.append(text)
        if text == "第一段":
            await self.first_release.wait()
            return f"{text}.wav"

        self.active_later.add(text)
        self.max_active_later = max(
            self.max_active_later, len(self.active_later)
        )
        later_started_count = len(
            [item for item in self.started if item != "第一段"]
        )
        if later_started_count == 2:
            self.two_later_started.set()
        if later_started_count == 3:
            self.third_later_started.set()

        try:
            await self.releases[text].wait()
        finally:
            self.active_later.remove(text)
        return f"{text}.wav"

    def remove_file(self, filepath: str) -> None:
        return None


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


def test_first_tts_is_exclusive_then_later_tasks_resume_in_parallel() -> None:
    async def scenario() -> None:
        sent: list[dict] = []

        async def websocket_send(message: str) -> None:
            sent.append(json.loads(message))

        manager = TTSTaskManager(turn_id="turn-exclusive-test")
        tts = _ExclusiveTTS()

        with patch(
            "open_llm_vtuber.conversations.tts_manager.prepare_audio_payload",
            side_effect=_payload,
        ):
            for text in ("第一段", "第二段", "第三段"):
                await manager.speak(
                    tts_text=text,
                    display_text=DisplayText(text=text),
                    actions=None,
                    live2d_model=SimpleNamespace(),
                    tts_engine=tts,
                    websocket_send=websocket_send,
                )

            await asyncio.sleep(0)
            assert tts.started == ["第一段"]

            tts.first_release.set()
            await asyncio.wait_for(tts.two_later_started.wait(), timeout=1)
            assert set(tts.started) == {"第一段", "第二段", "第三段"}

            tts.later_release.set()
            await asyncio.gather(*manager.task_list)
            await asyncio.wait_for(manager._payload_queue.join(), timeout=1)

        assert [item["display_text"]["text"] for item in sent] == [
            "第一段",
            "第二段",
            "第三段",
        ]
        sender_task = manager._sender_task
        manager.clear()
        if sender_task:
            await asyncio.gather(sender_task, return_exceptions=True)

    asyncio.run(scenario())


def test_later_tts_concurrency_is_limited_to_two() -> None:
    async def scenario() -> None:
        sent: list[dict] = []

        async def websocket_send(message: str) -> None:
            sent.append(json.loads(message))

        later_texts = ("第二段", "第三段", "第四段", "第五段")
        manager = TTSTaskManager(turn_id="turn-concurrency-limit")
        tts = _ConcurrencyLimitedTTS(later_texts)

        with patch(
            "open_llm_vtuber.conversations.tts_manager.prepare_audio_payload",
            side_effect=_payload,
        ):
            for text in ("第一段", *later_texts):
                await manager.speak(
                    tts_text=text,
                    display_text=DisplayText(text=text),
                    actions=None,
                    live2d_model=SimpleNamespace(),
                    tts_engine=tts,
                    websocket_send=websocket_send,
                )

            await asyncio.sleep(0)
            assert tts.started == ["第一段"]

            tts.first_release.set()
            await asyncio.wait_for(tts.two_later_started.wait(), timeout=1)
            assert len(tts.active_later) == 2
            assert tts.max_active_later == 2
            assert len([text for text in tts.started if text != "第一段"]) == 2

            first_active = next(iter(tts.active_later))
            tts.releases[first_active].set()
            await asyncio.wait_for(tts.third_later_started.wait(), timeout=1)
            assert tts.max_active_later == 2

            for release in tts.releases.values():
                release.set()
            await asyncio.gather(*manager.task_list)
            await asyncio.wait_for(manager._payload_queue.join(), timeout=1)

        assert [item["display_text"]["text"] for item in sent] == [
            "第一段",
            *later_texts,
        ]
        assert tts.max_active_later == 2
        sender_task = manager._sender_task
        manager.clear()
        if sender_task:
            await asyncio.gather(sender_task, return_exceptions=True)

    asyncio.run(scenario())


def test_first_tts_failure_releases_later_tasks_and_preserves_order() -> None:
    async def scenario() -> None:
        sent: list[dict] = []

        async def websocket_send(message: str) -> None:
            sent.append(json.loads(message))

        manager = TTSTaskManager(turn_id="turn-exclusive-failure")
        tts = _FirstFailureTTS()

        with patch(
            "open_llm_vtuber.conversations.tts_manager.prepare_audio_payload",
            side_effect=_payload,
        ):
            for text in ("第一段", "第二段"):
                await manager.speak(
                    tts_text=text,
                    display_text=DisplayText(text=text),
                    actions=None,
                    live2d_model=SimpleNamespace(),
                    tts_engine=tts,
                    websocket_send=websocket_send,
                )
            await asyncio.gather(*manager.task_list)
            await asyncio.wait_for(manager._payload_queue.join(), timeout=1)

        assert tts.started == ["第一段", "第二段"]
        assert [item["display_text"]["text"] for item in sent] == [
            "第一段",
            "第二段",
        ]
        assert sent[0]["audio"] is None
        assert sent[1]["audio"] == "ENCODED_AUDIO"
        sender_task = manager._sender_task
        manager.clear()
        if sender_task:
            await asyncio.gather(sender_task, return_exceptions=True)

    asyncio.run(scenario())


def test_silent_payload_does_not_claim_first_tts_exclusivity() -> None:
    async def scenario() -> None:
        sent: list[dict] = []

        async def websocket_send(message: str) -> None:
            sent.append(json.loads(message))

        manager = TTSTaskManager(turn_id="turn-exclusive-silent")
        tts = _ExclusiveTTS()

        with patch(
            "open_llm_vtuber.conversations.tts_manager.prepare_audio_payload",
            side_effect=_payload,
        ):
            await manager.speak(
                tts_text="...",
                display_text=DisplayText(text="静默片段"),
                actions=None,
                live2d_model=SimpleNamespace(),
                tts_engine=tts,
                websocket_send=websocket_send,
            )
            for text in ("第一段", "第二段", "第三段"):
                await manager.speak(
                    tts_text=text,
                    display_text=DisplayText(text=text),
                    actions=None,
                    live2d_model=SimpleNamespace(),
                    tts_engine=tts,
                    websocket_send=websocket_send,
                )

            await asyncio.sleep(0)
            assert manager._first_tts_sequence == 1
            assert tts.started == ["第一段"]
            tts.first_release.set()
            await asyncio.wait_for(tts.two_later_started.wait(), timeout=1)
            tts.later_release.set()
            await asyncio.gather(*manager.task_list)
            await asyncio.wait_for(manager._payload_queue.join(), timeout=1)

        assert [item["display_text"]["text"] for item in sent] == [
            "静默片段",
            "第一段",
            "第二段",
            "第三段",
        ]
        sender_task = manager._sender_task
        manager.clear()
        if sender_task:
            await asyncio.gather(sender_task, return_exceptions=True)

    asyncio.run(scenario())


def test_clear_cancels_first_tts_and_gate_waiters() -> None:
    async def scenario() -> None:
        manager = TTSTaskManager(turn_id="turn-exclusive-cancel")
        tts = _ExclusiveTTS()

        with patch(
            "open_llm_vtuber.conversations.tts_manager.prepare_audio_payload",
            side_effect=_payload,
        ):
            for text in ("第一段", "第二段"):
                await manager.speak(
                    tts_text=text,
                    display_text=DisplayText(text=text),
                    actions=None,
                    live2d_model=SimpleNamespace(),
                    tts_engine=tts,
                    websocket_send=AsyncMock(),
                )

            await asyncio.sleep(0)
            assert tts.started == ["第一段"]
            tasks = list(manager.task_list)
            sender_task = manager._sender_task
            manager.clear()
            await asyncio.gather(*tasks, return_exceptions=True)
            if sender_task:
                await asyncio.gather(sender_task, return_exceptions=True)

        assert all(task.cancelled() for task in tasks)
        assert tts.started == ["第一段"]
        assert manager._first_tts_sequence is None
        assert not manager._first_tts_done.is_set()

    asyncio.run(scenario())


def test_clear_cancels_active_and_semaphore_waiting_later_tasks() -> None:
    async def scenario() -> None:
        later_texts = ("第二段", "第三段", "第四段", "第五段")
        manager = TTSTaskManager(turn_id="turn-concurrency-cancel")
        tts = _ConcurrencyLimitedTTS(later_texts)

        with patch(
            "open_llm_vtuber.conversations.tts_manager.prepare_audio_payload",
            side_effect=_payload,
        ):
            for text in ("第一段", *later_texts):
                await manager.speak(
                    tts_text=text,
                    display_text=DisplayText(text=text),
                    actions=None,
                    live2d_model=SimpleNamespace(),
                    tts_engine=tts,
                    websocket_send=AsyncMock(),
                )

            tts.first_release.set()
            await asyncio.wait_for(tts.two_later_started.wait(), timeout=1)
            tasks = list(manager.task_list)
            sender_task = manager._sender_task
            manager.clear()
            await asyncio.gather(*tasks, return_exceptions=True)
            if sender_task:
                await asyncio.gather(sender_task, return_exceptions=True)

        later_tasks = tasks[1:]
        assert all(task.cancelled() for task in later_tasks)
        assert len([text for text in tts.started if text != "第一段"]) == 2
        assert tts.max_active_later == 2
        assert manager._later_tts_semaphore._value == 2

    asyncio.run(scenario())
