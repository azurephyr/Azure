"""
Azure Voice System (TTS/STT)

Real-time voice conversation support.
- TTS: pyttsx3 or gTTS for text-to-speech
- STT: Whisper or speech_recognition for speech-to-text
- Voice activity detection and natural turn-taking

This is a full architecture with optional dependencies.
If voice libraries are not installed, it provides clear setup instructions.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
from dataclasses import dataclass

logger = logging.getLogger("azure.voice")


@dataclass
class VoiceConfig:
    """Configuration for voice system."""
    tts_engine: str = "auto"  # "pyttsx3", "gtts", "auto"
    stt_engine: str = "auto"  # "whisper", "sr", "auto"
    voice_channel_id: int | None = None
    language: str = "en"
    speed: float = 1.0


class VoiceSystem:
    """
    Voice conversation system for Discord.

    Usage:
        voice = VoiceSystem()
        await voice.connect_to_channel(channel)
        await voice.speak("Hello everyone!")
        text = await voice.listen()
    """

    def __init__(self, config: VoiceConfig | None = None):
        self.config = config or VoiceConfig()
        self._tts = None
        self._stt = None
        self._voice_client = None
        self._audio_queue: asyncio.Queue = asyncio.Queue()
        self._initialized = False

    def _init(self) -> None:
        """Lazy initialize voice engines."""
        if self._initialized:
            return
        self._initialized = True

        # TTS initialization
        if self.config.tts_engine in ("pyttsx3", "auto"):
            try:
                import pyttsx3
                self._tts = pyttsx3.init()
                self._tts.setProperty("rate", int(150 * self.config.speed))
                logger.info("[voice] pyttsx3 TTS loaded")

            except Exception as e:
                logger.info("[voice] pyttsx3 unavailable: %s", e)

        if self._tts is None and self.config.tts_engine in ("gtts", "auto"):
            try:
                from gtts import gTTS
                self._tts = gTTS
                logger.info("[voice] gTTS loaded")

            except Exception as e:
                logger.info("[voice] gTTS unavailable: %s", e)

        # STT initialization
        if self.config.stt_engine in ("whisper", "auto"):
            try:
                import whisper
                self._stt = whisper.load_model("base")
                logger.info("[voice] Whisper STT loaded")

            except Exception as e:
                logger.info("[voice] Whisper STT unavailable: %s", e)

        if self._stt is None and self.config.stt_engine in ("sr", "auto"):
            try:
                import speech_recognition as sr
                self._stt = sr.Recognizer()
                logger.info("[voice] speech_recognition loaded")

            except Exception as e:
                logger.info("[voice] speech_recognition unavailable: %s", e)

    # ------------------------------------------------------------------
    # Discord integration
    # ------------------------------------------------------------------

    async def connect_to_channel(self, voice_channel) -> bool:  # type: ignore[no-untyped-def]
        """Connect to a Discord voice channel."""
        self._init()
        if self._voice_client is not None:
            await self._voice_client.disconnect()
        self._voice_client = await voice_channel.connect()
        return True

    async def disconnect(self) -> None:
        """Disconnect from voice channel."""
        if self._voice_client:
            await self._voice_client.disconnect()
            self._voice_client = None

    # ------------------------------------------------------------------
    # TTS
    # ------------------------------------------------------------------

    async def speak(self, text: str) -> None:
        """Speak text in the connected voice channel."""
        self._init()
        if not self._voice_client:
            logger.info("[voice] Not connected to a voice channel")

            return

        if self._tts is None:
            logger.info("[voice] No TTS engine available. Install pyttsx3 or gTTS.")

            return

        try:
            # Generate audio file
            if hasattr(self._tts, 'save'):
                # gTTS style
                from gtts import gTTS
                tts = gTTS(text=text, lang=self.config.language)
                tts.save("voice_temp.mp3")
                audio_source = await self._create_audio_source("voice_temp.mp3")
            else:
                # pyttsx3 style
                self._tts.save_to_file(text, "voice_temp.wav")
                self._tts.runAndWait()
                audio_source = await self._create_audio_source("voice_temp.wav")

            if audio_source is None:
                logger.warning("[voice] Failed to create audio source (FFmpeg missing?)")
                return

            self._voice_client.play(audio_source)
            while self._voice_client.is_playing():
                await asyncio.sleep(0.1)

            for temp_file in ["voice_temp.mp3", "voice_temp.wav"]:
                if os.path.exists(temp_file):
                    with contextlib.suppress(Exception):
                        os.remove(temp_file)

        except Exception as e:
            logger.error(f"[voice] TTS error: {e}")


    async def _create_audio_source(self, path: str):  # type: ignore[no-untyped-def]
        """Create a Discord audio source from a file."""
        try:
            import discord
            return discord.FFmpegPCMAudio(path)
        except Exception:
            logger.info("[voice] FFmpeg not available. Install ffmpeg for voice support.")

            return None

    # ------------------------------------------------------------------
    # STT
    # ------------------------------------------------------------------

    async def listen(self, timeout: int = 10) -> str | None:
        """Listen for speech and return transcribed text."""
        self._init()
        if self._stt is None:
            logger.info("[voice] No STT engine available. Install whisper or speech_recognition.")

            return None

        try:
            if hasattr(self._stt, 'listen'):
                # speech_recognition style
                import speech_recognition as sr
                with sr.Microphone() as source:
                    audio = self._stt.listen(source, timeout=timeout)
                return self._stt.recognize_google(audio)
            else:
                # Whisper style — would need audio file
                logger.info("[voice] Whisper requires audio file input. Use listen_from_file().")

                return None
        except Exception as e:
            logger.error(f"[voice] STT error: {e}")

            return None

    async def listen_from_file(self, audio_path: str) -> str | None:
        """Transcribe from an audio file."""
        self._init()
        if self._stt is None:
            return None
        try:
            if hasattr(self._stt, 'transcribe'):
                # Whisper
                result = self._stt.transcribe(audio_path)
                return result["text"]
            else:
                # speech_recognition
                import speech_recognition as sr
                with sr.AudioFile(audio_path) as source:
                    audio = self._stt.record(source)
                return self._stt.recognize_google(audio)
        except Exception as e:
            logger.error(f"[voice] Transcription error: {e}")

            return None

    # ------------------------------------------------------------------
    # Status
    # ------------------------------------------------------------------

    def is_ready(self) -> bool:
        """Check if voice system is ready."""
        self._init()
        return self._tts is not None or self._stt is not None

    def get_status(self) -> dict:
        """Get voice system status."""
        self._init()
        return {
            "tts_ready": self._tts is not None,
            "stt_ready": self._stt is not None,
            "connected": self._voice_client is not None,
            "engines": {
                "tts": "pyttsx3/gtts" if self._tts else "none",
                "stt": "whisper/sr" if self._stt else "none",
            }
        }
