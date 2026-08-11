import asyncio
import sys
import os

import edge_tts
from loguru import logger
from .tts_interface import TTSInterface

current_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.append(current_dir)


# Check out doc at https://github.com/rany2/edge-tts
# Use `edge-tts --list-voices` to list all available voices


class TTSEngine(TTSInterface):
    def __init__(self, voice="en-US-AvaMultilingualNeural"):
        self.voice = voice

        self.temp_audio_file = "temp"
        self.file_extension = "mp3"
        self.new_audio_dir = "cache"

        if not os.path.exists(self.new_audio_dir):
            os.makedirs(self.new_audio_dir)

    @staticmethod
    def _remove_incomplete_file(file_name: str) -> None:
        """Remove a partial audio file left by a failed or cancelled request."""
        try:
            os.remove(file_name)
        except FileNotFoundError:
            return
        except OSError as error:
            logger.warning(
                "Unable to remove incomplete Edge TTS file (error_type={})",
                type(error).__name__,
            )

    @staticmethod
    def _log_generation_error(error: Exception) -> None:
        """Log an actionable error without exposing input text or endpoints."""
        logger.error(
            "Edge TTS generation failed (error_type={})", type(error).__name__
        )
        logger.error("Edge TTS may be unavailable or blocked in the current network.")

    async def async_generate_audio(self, text: str, file_name_no_ext=None):
        """Generate audio using Edge TTS' native asynchronous API.

        Cancellation is deliberately propagated so conversation interruption can
        close the in-flight Edge connection. Any partially written cache file is
        removed before control returns to the caller.
        """
        file_name = self.generate_cache_file_name(
            file_name_no_ext, self.file_extension
        )
        completed = False

        try:
            communicate = edge_tts.Communicate(text, self.voice)
            await communicate.save(file_name)
            completed = True
            return file_name
        except asyncio.CancelledError:
            logger.debug("Edge TTS generation cancelled")
            raise
        except Exception as error:
            self._log_generation_error(error)
            return None
        finally:
            if not completed:
                self._remove_incomplete_file(file_name)

    def generate_audio(self, text, file_name_no_ext=None):
        """
        Generate speech audio file using TTS.
        text: str
            the text to speak
        file_name_no_ext: str
            name of the file without extension


        Returns:
        str: the path to the generated audio file

        """
        file_name = self.generate_cache_file_name(file_name_no_ext, self.file_extension)

        completed = False
        try:
            communicate = edge_tts.Communicate(text, self.voice)
            communicate.save_sync(file_name)
            completed = True
        except Exception as error:
            self._log_generation_error(error)
            return None
        finally:
            if not completed:
                self._remove_incomplete_file(file_name)

        return file_name


# en-US-AvaMultilingualNeural
# en-US-EmmaMultilingualNeural
# en-US-JennyNeural
