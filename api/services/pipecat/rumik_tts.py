"""Rumik AI Silk real-time streaming Text-to-Speech service for Pipecat pipelines."""

from __future__ import annotations

import asyncio
import base64
import json
from dataclasses import dataclass, field
from typing import Any

from loguru import logger
from websockets.protocol import State

from pipecat import version as pipecat_version
from pipecat.frames.frames import (
    CancelFrame,
    EndFrame,
    ErrorFrame,
    Frame,
    StartFrame,
    TTSAudioRawFrame,
    TTSStoppedFrame,
)
from pipecat.services.settings import NOT_GIVEN, TTSSettings, _NotGiven
from pipecat.services.tts_service import InterruptibleTTSService
from pipecat.transcriptions.language import Language


@dataclass
class RumikTTSSettings(TTSSettings):
    """Runtime-updatable settings for RumikTTSService.

    Attributes:
        model: Rumik streaming model ('mulberry' or 'muga').
        voice: Natural language voice prompt or voice identifier.
        language: Language code for synthesis (e.g. 'hi', 'en', 'hinglish').
        sample_rate: Target audio sample rate in Hz (8000, 16000, 24000).
    """

    model: str | None | _NotGiven = field(default_factory=lambda: NOT_GIVEN)
    voice: str | None | _NotGiven = field(default_factory=lambda: NOT_GIVEN)
    language: str | None | _NotGiven = field(default_factory=lambda: NOT_GIVEN)
    sample_rate: int | None | _NotGiven = field(default_factory=lambda: NOT_GIVEN)


class RumikTTSService(InterruptibleTTSService):
    """Rumik AI Silk real-time text-to-speech service using WebSocket streaming.

    Designed for interactive voice agents and telephony (Smartflo, Twilio, SIP trunks)
    requiring ultra-low latency, natural Indic/Hinglish speech, and instant barge-in
    interruption handling.
    """

    Settings = RumikTTSSettings
    _settings: Settings

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str = "wss://silk-api.rumik.ai/v1/stream",
        sample_rate: int | None = 8000,
        model: str = "mulberry",
        voice: str = "warm professional female, Indian accent",
        language: str = "hi",
        settings: Settings | None = None,
        **kwargs: Any,
    ):
        """Initialize the Rumik AI WebSocket streaming TTS service.

        Args:
            api_key: Rumik AI Silk API key.
            base_url: WebSocket streaming endpoint URL.
            sample_rate: Output audio sample rate. Defaults to 8000 Hz (native PSTN telephony).
            model: Streaming model ('mulberry' or 'muga').
            voice: Voice description prompt or voice name.
            language: Synthesis language code.
            settings: Runtime-updatable settings.
            **kwargs: Extra arguments passed to InterruptibleTTSService.
        """
        resolved_sample_rate = sample_rate or 8000

        default_settings = self.Settings(
            model=model,
            voice=voice,
            language=language,
            sample_rate=resolved_sample_rate,
        )

        if settings is not None:
            default_settings.apply_update(settings)

        super().__init__(
            push_stop_frames=True,
            push_start_frame=True,
            pause_frame_processing=True,
            sample_rate=resolved_sample_rate,
            push_text_frames=True,
            settings=default_settings,
            **kwargs,
        )

        self._api_key = api_key
        # Ensure WebSocket URL scheme
        if base_url.startswith("https://"):
            base_url = base_url.replace("https://", "wss://", 1)
        elif base_url.startswith("http://"):
            base_url = base_url.replace("http://", "ws://", 1)
        self._base_url = base_url.rstrip("/")
        self._receive_task: asyncio.Task | None = None
        self._keepalive_task: asyncio.Task | None = None
        self._websocket = None

    def can_generate_metrics(self) -> bool:
        return True

    def _build_websocket_url(self) -> str:
        sample_rate = self._settings.sample_rate or self.sample_rate or 8000
        url = self._base_url
        if "?" in url:
            return f"{url}&sample_rate={sample_rate}&format=pcm"
        return f"{url}?sample_rate={sample_rate}&format=pcm"

    async def _connect_websocket(self):
        """Establish WebSocket connection to Rumik Silk API."""
        try:
            if self._websocket and self._websocket.state is State.OPEN:
                return

            logger.debug(f"[Rumik TTS] Connecting to {self._base_url}")
            headers = {
                "Authorization": f"Bearer {self._api_key}",
                "X-Source": "dograh-voice",
                "X-Pipecat-Version": pipecat_version(),
            }

            self._websocket = await self._websocket_connect(
                self._build_websocket_url(),
                additional_headers=headers,
            )

            await self._call_event_handler("on_connected")
            logger.info("[Rumik TTS] Connected to Rumik Silk streaming endpoint")
        except Exception as e:
            await self.push_error(error_msg=f"Rumik TTS connection error: {e}", exception=e)
            self._websocket = None
            await self._call_event_handler("on_connection_error", f"{e}")

    async def _disconnect_websocket(self):
        """Disconnect WebSocket and cancel background workers."""
        try:
            await self.stop_all_metrics()
            if self._keepalive_task:
                self._keepalive_task.cancel()
                self._keepalive_task = None

            if self._websocket:
                logger.debug("[Rumik TTS] Closing WebSocket connection")
                await self._websocket.close()
        except Exception as e:
            logger.debug(f"[Rumik TTS] Error closing websocket: {e}")
        finally:
            self._websocket = None
            await self._call_event_handler("on_disconnected")

    def _get_websocket(self):
        if self._websocket:
            return self._websocket
        raise ConnectionError("Rumik TTS WebSocket is not connected")

    async def start(self, frame: StartFrame):
        await super().start(frame)
        await self._connect_websocket()
        if not self._receive_task or self._receive_task.done():
            self._receive_task = self.create_task(self._receive_messages())
        if not self._keepalive_task or self._keepalive_task.done():
            self._keepalive_task = self.create_task(self._keepalive_worker())

    async def stop(self, frame: EndFrame):
        if self._keepalive_task:
            self._keepalive_task.cancel()
            self._keepalive_task = None
        if self._receive_task:
            self._receive_task.cancel()
            self._receive_task = None
        await self._disconnect_websocket()
        await super().stop(frame)

    async def cancel(self, frame: CancelFrame):
        """Instantly stop audio playback upon user interruption / barge-in."""
        await super().cancel(frame)
        context_id = self.get_active_audio_context_id()
        if context_id:
            await self.broadcast_frame(TTSStoppedFrame(context_id=context_id))
        # Reconnect WebSocket to flush in-flight server buffers immediately
        await self._disconnect_websocket()
        await self._connect_websocket()

    async def _keepalive_worker(self):
        """Send periodic keepalive pings during long pauses."""
        while True:
            try:
                await asyncio.sleep(20)
                if self._websocket and self._websocket.state is State.OPEN:
                    ping_payload = {"type": "ping"}
                    await self._websocket.send(json.dumps(ping_payload))
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.trace(f"[Rumik TTS] Keepalive ping exception: {e}")

    async def run_tts(self, text: str) -> None:
        """Stream LLM text chunk to Rumik Silk TTS endpoint."""
        if not text.strip():
            return

        await self._connect_websocket()
        ws = self._get_websocket()

        sample_rate = self._settings.sample_rate or self.sample_rate or 8000
        msg = {
            "text": text,
            "model": self._settings.model or "mulberry",
            "voice": self._settings.voice or "warm professional female, Indian accent",
            "language": self._settings.language or "hi",
            "sample_rate": sample_rate,
            "format": "pcm",
        }

        await self.start_ttfb_metrics()
        await ws.send(json.dumps(msg))

    async def flush_audio(self, context_id: str | None = None):
        """Send end of turn/flush signal to Rumik Silk."""
        if self._websocket and self._websocket.state is State.OPEN:
            try:
                flush_msg = {"type": "flush"}
                await self._websocket.send(json.dumps(flush_msg))
            except Exception:
                pass

    async def _receive_messages(self):
        """Receive and forward synthesized audio frames into the pipeline."""
        while True:
            try:
                ws = self._get_websocket()
                async for message in ws:
                    sample_rate = self._settings.sample_rate or self.sample_rate or 8000

                    if isinstance(message, bytes):
                        # Raw binary PCM audio frame
                        await self.stop_ttfb_metrics()
                        context_id = self.get_active_audio_context_id()
                        frame = TTSAudioRawFrame(
                            audio=message,
                            sample_rate=sample_rate,
                            num_channels=1,
                            context_id=context_id,
                        )
                        await self.push_frame(frame)
                    elif isinstance(message, str):
                        # JSON protocol message
                        try:
                            data = json.loads(message)
                        except Exception:
                            continue

                        msg_type = data.get("type") or data.get("status")

                        if msg_type in ("complete", "done"):
                            await self.stop_all_metrics()
                        elif msg_type in ("chunk", "audio"):
                            await self.stop_ttfb_metrics()
                            raw_b64 = data.get("data", {}).get("audio") or data.get("audio")
                            if raw_b64:
                                audio_bytes = base64.b64decode(raw_b64)
                                context_id = self.get_active_audio_context_id()
                                frame = TTSAudioRawFrame(
                                    audio=audio_bytes,
                                    sample_rate=sample_rate,
                                    num_channels=1,
                                    context_id=context_id,
                                )
                                await self.push_frame(frame)
                        elif msg_type == "error":
                            err = data.get("message", "Unknown Rumik Silk error")
                            logger.error(f"[Rumik TTS] Server error: {err}")
                            await self.push_error(error_msg=f"Rumik TTS error: {err}")
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.debug(f"[Rumik TTS] WebSocket receiver loop error: {e}")
                await asyncio.sleep(0.5)
                # Attempt auto-reconnect
                try:
                    await self._connect_websocket()
                except Exception:
                    pass
