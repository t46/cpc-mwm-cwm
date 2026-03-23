from __future__ import annotations

import asyncio
import logging
import queue
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

import numpy as np
import sounddevice as sd

if TYPE_CHECKING:
    from cpc_mwm.config import MwmConfig

logger = logging.getLogger(__name__)


class AudioTranscriber:
    """Captures audio via sounddevice and transcribes with faster-whisper."""

    def __init__(self, config: MwmConfig) -> None:
        self.config = config
        self.sample_rate = 16000
        self.buffer_seconds = 5
        self._audio_queue: queue.Queue[np.ndarray] = queue.Queue()
        self._running = False
        self._model = None  # Lazy load

    def _get_model(self):
        """Lazy-load the Whisper model to avoid slow startup."""
        if self._model is None:
            from faster_whisper import WhisperModel

            logger.info(
                "Loading Whisper model: %s (this may take a moment...)",
                self.config.whisper_model,
            )
            self._model = WhisperModel(
                self.config.whisper_model,
                device="auto",
                compute_type="auto",
            )
            logger.info("Whisper model loaded successfully")
        return self._model

    def _get_device_id(self) -> int | None:
        """Find the audio device ID by name, or None for default."""
        if not self.config.audio_device:
            return None
        devices = sd.query_devices()
        for i, dev in enumerate(devices):
            if self.config.audio_device.lower() in dev["name"].lower():
                logger.info("Using audio device: %s (id=%d)", dev["name"], i)
                return i
        logger.warning(
            "Audio device '%s' not found. Available devices:", self.config.audio_device
        )
        for i, dev in enumerate(devices):
            if dev["max_input_channels"] > 0:
                logger.warning("  [%d] %s", i, dev["name"])
        return None

    def _audio_callback(
        self,
        indata: np.ndarray,
        frames: int,
        time_info: object,
        status: sd.CallbackFlags,
    ) -> None:
        """Callback for sounddevice InputStream."""
        if status:
            logger.warning("Audio status: %s", status)
        self._audio_queue.put(indata.copy())

    async def start(self, on_transcript: Callable[[str], Awaitable[None]]) -> None:
        """Start audio capture and transcription loop."""
        self._running = True
        device_id = self._get_device_id()

        # Pre-load model
        model = await asyncio.to_thread(self._get_model)

        logger.info(
            "Starting audio capture (device=%s, rate=%d, buffer=%ds)",
            device_id or "default",
            self.sample_rate,
            self.buffer_seconds,
        )

        stream = sd.InputStream(
            device=device_id,
            samplerate=self.sample_rate,
            channels=1,
            dtype="float32",
            blocksize=int(self.sample_rate * 0.5),  # 500ms blocks
            callback=self._audio_callback,
        )

        buffer = np.array([], dtype=np.float32)
        target_samples = self.sample_rate * self.buffer_seconds

        with stream:
            while self._running:
                try:
                    # Collect audio chunks from the queue
                    chunk = await asyncio.to_thread(self._audio_queue.get, timeout=1.0)
                    buffer = np.concatenate([buffer, chunk.flatten()])

                    # When we have enough audio, transcribe
                    if len(buffer) >= target_samples:
                        audio_segment = buffer[:target_samples]
                        buffer = buffer[target_samples:]

                        # Run transcription in a thread to avoid blocking
                        text = await asyncio.to_thread(
                            self._transcribe, model, audio_segment
                        )
                        if text:
                            logger.info("Transcribed: %s", text[:80])
                            await on_transcript(text)

                except queue.Empty:
                    continue
                except Exception:
                    logger.exception("Error in audio capture loop")
                    await asyncio.sleep(1)

    def _transcribe(self, model, audio: np.ndarray) -> str:
        """Run faster-whisper transcription on audio segment."""
        segments, _info = model.transcribe(
            audio,
            language=self.config.whisper_language,
            beam_size=5,
            vad_filter=True,
        )
        texts = [segment.text.strip() for segment in segments if segment.text.strip()]
        return " ".join(texts)

    async def stop(self) -> None:
        """Stop audio capture."""
        self._running = False
        logger.info("Audio capture stopped")
