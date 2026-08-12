from typing import Literal

import httpx
from loguru import logger

from .tts_interface import TTSInterface


class TTSEngine(TTSInterface):
    """Fish Audio TTS client using the current HTTP API."""

    file_extension: str = "wav"

    def __init__(
        self,
        api_key: str,
        reference_id: str,
        latency: Literal["normal", "balanced"] = "balanced",
        base_url: str = "https://api.fish.audio",
        model: str = "s2.1-pro-free",
    ):
        if not api_key:
            raise ValueError("Fish Audio API key is required")
        if not reference_id:
            raise ValueError("Fish Audio reference_id is required")

        self.reference_id = reference_id
        self.latency = latency
        self.model = model
        self.client = httpx.Client(
            base_url=f"{base_url.rstrip('/')}/",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            timeout=httpx.Timeout(60.0),
        )

        logger.info(
            "Fish TTS API initialized (model={}, reference_id={}, latency={})",
            self.model,
            self.reference_id,
            self.latency,
        )

    @staticmethod
    def _is_wav(audio: bytes) -> bool:
        return len(audio) >= 12 and audio[:4] == b"RIFF" and audio[8:12] == b"WAVE"

    def generate_audio(self, text: str, file_name_no_ext=None):
        file_name = self.generate_cache_file_name(file_name_no_ext, self.file_extension)

        logger.debug(
            "Fish TTS request summary "
            "(text_chars={}, model={}, reference_id={}, latency={})",
            len(text),
            self.model,
            self.reference_id,
            self.latency,
        )

        try:
            response = self.client.post(
                "v1/tts",
                headers={"model": self.model},
                json={
                    "text": text,
                    "reference_id": self.reference_id,
                    "latency": self.latency,
                    "format": self.file_extension,
                },
            )

            if response.status_code != httpx.codes.OK:
                reason = {
                    400: "invalid reference_id or request parameters",
                    401: "invalid or missing API key",
                    402: "insufficient API credit",
                    429: "rate limit exceeded",
                }.get(response.status_code, "provider request failed")
                logger.error(
                    "Fish TTS API request failed (status={}, reason={})",
                    response.status_code,
                    reason,
                )
                return None

            if not self._is_wav(response.content):
                logger.error(
                    "Fish TTS API returned invalid WAV audio (status={}, bytes={})",
                    response.status_code,
                    len(response.content),
                )
                return None

            with open(file_name, "wb") as audio_file:
                audio_file.write(response.content)
            return file_name
        except httpx.TimeoutException:
            logger.error("Fish TTS API request timed out")
        except httpx.HTTPError as exc:
            logger.error(
                "Fish TTS API transport error (error_type={})",
                type(exc).__name__,
            )
        except Exception as exc:
            logger.error(
                "Fish TTS audio generation failed (error_type={})",
                type(exc).__name__,
            )

        return None
