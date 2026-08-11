import asyncio
import json
import re
import uuid
from datetime import datetime
from time import perf_counter
from typing import List, Optional, Dict
from loguru import logger

from ..agent.output_types import DisplayText, Actions
from ..live2d_model import Live2dModel
from ..tts.tts_interface import TTSInterface
from ..utils.stream_audio import prepare_audio_payload
from .types import WebSocketSend


class TTSTaskManager:
    """Manages TTS tasks and ensures ordered delivery to frontend while allowing parallel TTS generation"""

    def __init__(
        self,
        turn_id: Optional[str] = None,
        conversation_started_at: Optional[float] = None,
    ) -> None:
        self.task_list: List[asyncio.Task] = []
        self._lock = asyncio.Lock()
        # Queue to store ordered payloads
        self._payload_queue: asyncio.Queue[Dict] = asyncio.Queue()
        # Task to handle sending payloads in order
        self._sender_task: Optional[asyncio.Task] = None
        # Counter for maintaining order
        self._sequence_counter = 0
        self._next_sequence_to_send = 0
        self._llm_started_at: Optional[float] = None
        self._first_tts_queued = False
        self._first_tts_sequence: Optional[int] = None
        self._first_tts_done = asyncio.Event()
        self._turn_id = turn_id or uuid.uuid4().hex[:12]
        self._conversation_started_at = conversation_started_at
        self._first_audio_payload_sent = False

    @property
    def turn_id(self) -> str:
        """Return the correlation identifier used by performance logs."""
        return self._turn_id

    def mark_llm_started(self) -> None:
        """Mark the start of LLM processing for first-sentence latency logging."""
        self._llm_started_at = perf_counter()

    async def speak(
        self,
        tts_text: str,
        display_text: DisplayText,
        actions: Optional[Actions],
        live2d_model: Live2dModel,
        tts_engine: TTSInterface,
        websocket_send: WebSocketSend,
    ) -> None:
        """
        Queue a TTS task while maintaining order of delivery.

        Args:
            tts_text: Text to synthesize
            display_text: Text to display in UI
            actions: Live2D model actions
            live2d_model: Live2D model instance
            tts_engine: TTS engine instance
            websocket_send: WebSocket send function
        """
        if len(re.sub(r'[\s.,!?，。！？\'"』」）】\s]+', "", tts_text)) == 0:
            logger.debug("Empty TTS text, sending silent display payload")
            # Get current sequence number for silent payload
            current_sequence = self._sequence_counter
            self._sequence_counter += 1

            # Start sender task if not running
            if not self._sender_task or self._sender_task.done():
                self._sender_task = asyncio.create_task(
                    self._process_payload_queue(websocket_send)
                )

            await self._send_silent_payload(display_text, actions, current_sequence)
            return

        logger.debug("🏃 Queuing TTS task (text_chars={})", len(tts_text))

        if not self._first_tts_queued:
            self._first_tts_queued = True
            if self._llm_started_at is not None:
                duration_ms = (perf_counter() - self._llm_started_at) * 1000
                logger.info(
                    f"[PERF] turn_id={self._turn_id} "
                    f"stage=llm_first_sentence duration_ms={duration_ms:.1f}"
                )

        # Get current sequence number
        current_sequence = self._sequence_counter
        self._sequence_counter += 1
        if self._first_tts_sequence is None:
            self._first_tts_sequence = current_sequence

        # Start sender task if not running
        if not self._sender_task or self._sender_task.done():
            self._sender_task = asyncio.create_task(
                self._process_payload_queue(websocket_send)
            )

        # Create and queue the TTS task
        task = asyncio.create_task(
            self._process_tts(
                tts_text=tts_text,
                display_text=display_text,
                actions=actions,
                live2d_model=live2d_model,
                tts_engine=tts_engine,
                sequence_number=current_sequence,
            )
        )
        self.task_list.append(task)

    async def _process_payload_queue(self, websocket_send: WebSocketSend) -> None:
        """
        Process and send payloads in correct order.
        Runs continuously until all payloads are processed.
        """
        buffered_payloads: Dict[int, Dict] = {}

        while True:
            try:
                # Get payload from queue
                payload, sequence_number = await self._payload_queue.get()
                buffered_payloads[sequence_number] = payload

                # Send payloads in order
                while self._next_sequence_to_send in buffered_payloads:
                    next_payload = buffered_payloads.pop(self._next_sequence_to_send)
                    await websocket_send(json.dumps(next_payload))

                    has_audio = bool(next_payload.get("audio"))
                    if has_audio and not self._first_audio_payload_sent:
                        self._first_audio_payload_sent = True
                        if self._conversation_started_at is not None:
                            duration_ms = (
                                perf_counter() - self._conversation_started_at
                            ) * 1000
                            logger.info(
                                f"[PERF] turn_id={self._turn_id} "
                                "stage=first_audio_payload_sent "
                                f"duration_ms={duration_ms:.1f} has_audio=True"
                            )
                    self._next_sequence_to_send += 1

                self._payload_queue.task_done()

            except asyncio.CancelledError:
                break

    async def _send_silent_payload(
        self,
        display_text: DisplayText,
        actions: Optional[Actions],
        sequence_number: int,
    ) -> None:
        """Queue a silent audio payload"""
        audio_payload = prepare_audio_payload(
            audio_path=None,
            display_text=display_text,
            actions=actions,
        )
        await self._payload_queue.put((audio_payload, sequence_number))

    async def _process_tts(
        self,
        tts_text: str,
        display_text: DisplayText,
        actions: Optional[Actions],
        live2d_model: Live2dModel,
        tts_engine: TTSInterface,
        sequence_number: int,
    ) -> None:
        """Process TTS generation and queue the result for ordered delivery"""
        is_first_tts = sequence_number == self._first_tts_sequence
        first_tts_done = self._first_tts_done
        gate_wait_started_at = perf_counter()
        if not is_first_tts:
            await first_tts_done.wait()
        gate_wait_ms = (perf_counter() - gate_wait_started_at) * 1000

        audio_file_path = None
        started_at = perf_counter()
        audio_ready_at = None
        payload_ready_at = None
        has_audio = False
        success = False
        try:
            audio_file_path = await self._generate_audio(tts_engine, tts_text)
            audio_ready_at = perf_counter()
            payload = prepare_audio_payload(
                audio_path=audio_file_path,
                display_text=display_text,
                actions=actions,
            )
            payload_ready_at = perf_counter()
            has_audio = bool(payload.get("audio"))
            success = bool(audio_file_path) and has_audio
            # Queue the payload with its sequence number
            await self._payload_queue.put((payload, sequence_number))

        except Exception as e:
            logger.error(f"Error preparing audio payload: {e}")
            # Queue silent payload for error case
            payload = prepare_audio_payload(
                audio_path=None,
                display_text=display_text,
                actions=actions,
            )
            payload_ready_at = perf_counter()
            await self._payload_queue.put((payload, sequence_number))

        finally:
            # Always release subsequent TTS tasks, including when the first
            # task is cancelled or fails before it can enqueue a payload.
            if is_first_tts:
                first_tts_done.set()
            completed_at = payload_ready_at or perf_counter()
            synthesis_completed_at = audio_ready_at or completed_at
            logger.info(
                f"[PERF] turn_id={self._turn_id} stage=tts "
                f"sequence={sequence_number} text_chars={len(tts_text)} "
                f"first_exclusive={is_first_tts} gate_wait_ms={gate_wait_ms:.1f} "
                f"synthesis_ms={(synthesis_completed_at - started_at) * 1000:.1f} "
                f"payload_ms={(completed_at - synthesis_completed_at) * 1000:.1f} "
                f"total_ms={(completed_at - started_at) * 1000:.1f} "
                f"success={success} has_audio={has_audio}"
            )
            if audio_file_path:
                tts_engine.remove_file(audio_file_path)
                logger.debug("Audio cache file cleaned.")

    async def _generate_audio(self, tts_engine: TTSInterface, text: str) -> str:
        """Generate audio file from text"""
        logger.debug("🏃 Generating audio (text_chars={})", len(text))
        return await tts_engine.async_generate_audio(
            text=text,
            file_name_no_ext=f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{str(uuid.uuid4())[:8]}",
        )

    def clear(self) -> None:
        """Clear all pending tasks and reset state"""
        for task in self.task_list:
            if not task.done():
                task.cancel()
        # Wake any gate waiters as a defensive fallback. The tasks above are
        # cancelled first so they cannot begin new synthesis after cleanup.
        self._first_tts_done.set()
        self.task_list.clear()
        if self._sender_task:
            self._sender_task.cancel()
            self._sender_task = None
        self._sequence_counter = 0
        self._next_sequence_to_send = 0
        self._llm_started_at = None
        self._first_tts_queued = False
        self._first_tts_sequence = None
        self._first_tts_done = asyncio.Event()
        self._first_audio_payload_sent = False
        # Create a new queue to clear any pending items
        self._payload_queue = asyncio.Queue()
