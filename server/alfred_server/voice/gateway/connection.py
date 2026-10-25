"""One device's WebSocket: authentication, the receive loop and the turn in progress.

The receive loop never blocks on a turn: each turn runs as a task, so audio frames,
`turn.cancel` and `ping` keep flowing while the reply is being generated and spoken. Only
one turn at a time. Sends from the loop and the turn are serialised by a lock.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from fastapi import WebSocket, WebSocketDisconnect

from alfred_server import __version__
from alfred_server.agents.context import RequestContext
from alfred_server.auth import TokenCheck, check_api_token
from alfred_server.voice.gateway.hub import DeviceHub
from alfred_server.voice.gateway.pipeline import VoiceServices, VoiceTurn, now
from alfred_server.voice.gateway.protocol import (
    INPUT_SAMPLE_RATE,
    PROTOCOL_VERSION,
    ConfirmReply,
    Hello,
    Ping,
    ProtocolError,
    TurnCancel,
    TurnEnd,
    TurnStart,
    message,
    parse_message,
)
from alfred_server.voice.tts import OUTPUT_SAMPLE_RATE

logger = logging.getLogger(__name__)

# WebSocket close codes (4000–4999 are for applications). Listed in docs/protocol.md.
CLOSE_REPLACED = 4000
CLOSE_BAD_HELLO = 4400
CLOSE_UNAUTHORIZED = 4401
CLOSE_TIMEOUT = 4408
CLOSE_NOT_CONFIGURED = 4503


@dataclass
class _OpenTurn:
    id: str
    turn: VoiceTurn
    # Audio from the device for STT; None marks `turn.end`.
    audio: asyncio.Queue[bytes | None] = field(default_factory=asyncio.Queue)
    receiving: bool = True
    task: asyncio.Task[None] | None = None


async def _drain(queue: asyncio.Queue[bytes | None]) -> AsyncIterator[bytes]:
    while (chunk := await queue.get()) is not None:
        yield chunk


class DeviceConnection:
    def __init__(self, websocket: WebSocket, services: VoiceServices, hub: DeviceHub) -> None:
        self.ws = websocket
        self.services = services
        self.hub = hub
        self.device_id: str | None = None
        self.session_id: str | None = None
        self._turn: _OpenTurn | None = None
        self._send_lock = asyncio.Lock()
        self._closed = False

    async def serve(self) -> None:
        await self.ws.accept()
        hello = await self._authenticate()
        if hello is None:
            return
        self.device_id = hello.device_id
        self.session_id = self.hub.session_for(hello.device_id, hello.session_id)
        await self.hub.register(hello.device_id, self)
        logger.info("device %s connected (app %s)", hello.device_id, hello.app_version)
        try:
            await self.send_json(
                message(
                    "hello.ok",
                    protocol=PROTOCOL_VERSION,
                    server_version=__version__,
                    session_id=self.session_id,
                    input_sample_rate=INPUT_SAMPLE_RATE,
                    output_sample_rate=OUTPUT_SAMPLE_RATE,
                    max_audio_s=self.services.settings.stt_max_audio_s,
                    voice=self.services.voice_ready,
                )
            )
            await self._receive_loop()
        finally:
            self.hub.unregister(hello.device_id, self)
            await self._cancel_turn(None, notify=False)
            await self._close(1000, "")
            logger.info("device %s disconnected", hello.device_id)

    # --- Sending (also the `Sender` a turn uses) ---------------------------------------------

    async def send_json(self, data: dict[str, Any]) -> None:
        await self._send(lambda: self.ws.send_json(data))

    async def send_bytes(self, data: bytes) -> None:
        await self._send(lambda: self.ws.send_bytes(data))

    async def _send(self, send: Callable[[], Awaitable[None]]) -> None:
        if self._closed:
            return
        async with self._send_lock:
            try:
                await send()
            except Exception:
                # The device has gone; the receive loop will notice and clean up.
                logger.debug("send to device %s failed", self.device_id, exc_info=True)
                self._closed = True

    async def _error(self, code: str, text: str, turn_id: str | None = None) -> None:
        fields = {"turn_id": turn_id} if turn_id is not None else {}
        await self.send_json(message("error", code=code, message=text, **fields))

    async def _close(self, code: int, reason: str) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            await self.ws.close(code, reason)
        except Exception:
            logger.debug("closing device %s's socket failed", self.device_id, exc_info=True)

    async def replace(self) -> None:
        """The device connected again: stop this connection's turn and close it."""
        await self._cancel_turn(None, notify=False)
        await self._close(CLOSE_REPLACED, "Replaced by a newer connection")

    # --- Receiving -----------------------------------------------------------------------------

    async def _receive(self) -> str | bytes | None:
        """The next frame, or None once the device has disconnected."""
        try:
            frame = await self.ws.receive()
        except (WebSocketDisconnect, RuntimeError):
            return None
        if frame["type"] == "websocket.disconnect":
            return None
        if frame.get("bytes") is not None:
            return bytes(frame["bytes"])
        return str(frame.get("text") or "")

    async def _authenticate(self) -> Hello | None:
        """The device's `hello`, once its token checks out; None if the socket was closed.

        The token comes from the upgrade request's `Authorization: Bearer` header or, for
        clients that can't set headers, the `hello`. Nothing else is read before this.
        """
        settings = self.services.settings
        scheme, _, value = self.ws.headers.get("authorization", "").partition(" ")
        header_token = value.strip() if scheme.lower() == "bearer" and value.strip() else None
        early = check_api_token(settings, header_token)
        if early == "unconfigured" or (header_token is not None and early != "ok"):
            await self._refuse(early)
            return None
        try:
            frame = await asyncio.wait_for(self._receive(), settings.voice_auth_timeout_s)
        except TimeoutError:
            await self._close(CLOSE_TIMEOUT, "No hello received")
            return None
        if frame is None:
            return None
        hello: Hello | None = None
        if isinstance(frame, str):
            try:
                parsed = parse_message(frame)
            except ProtocolError:
                parsed = None
            hello = parsed if isinstance(parsed, Hello) else None
        if hello is None:
            await self._close(CLOSE_BAD_HELLO, "Expected a valid hello")
            return None
        if header_token is None:
            result = check_api_token(settings, hello.token)
            if result != "ok":
                await self._refuse(result)
                return None
        if hello.protocol != PROTOCOL_VERSION:
            await self._error(
                "unsupported_protocol", f"This server speaks protocol {PROTOCOL_VERSION}"
            )
            await self._close(CLOSE_BAD_HELLO, "Unsupported protocol version")
            return None
        return hello

    async def _refuse(self, result: TokenCheck) -> None:
        if result == "unconfigured":
            # Fail closed, as the HTTP routes do with a 503.
            await self._close(CLOSE_NOT_CONFIGURED, "API token not configured")
        else:
            await self._close(CLOSE_UNAUTHORIZED, "Invalid or missing API token")

    async def _receive_loop(self) -> None:
        idle_timeout = self.services.settings.voice_idle_timeout_s
        while not self._closed:
            try:
                frame = await asyncio.wait_for(self._receive(), idle_timeout)
            except TimeoutError:
                await self._close(CLOSE_TIMEOUT, "Idle timeout")
                return
            if frame is None:
                return
            if isinstance(frame, bytes):
                self._on_audio(frame)
                continue
            try:
                msg = parse_message(frame)
            except ProtocolError as exc:
                await self._error(exc.code, exc.message)
                continue
            await self._dispatch(msg)

    async def _dispatch(
        self, msg: Hello | Ping | TurnStart | TurnEnd | TurnCancel | ConfirmReply
    ) -> None:
        match msg:
            case Ping():
                await self.send_json(message("pong", t=msg.t))
            case TurnStart():
                await self._start_turn(msg)
            case TurnEnd():
                self._end_turn(msg.turn_id)
            case TurnCancel():
                await self._cancel_turn(msg.turn_id, notify=True)
            case ConfirmReply():
                await self._confirm_reply(msg)
            case Hello():
                await self._error("bad_message", "Already connected")

    # --- Turns ---------------------------------------------------------------------------------

    def _context(self, persona_id: str | None = None, **fields: Any) -> RequestContext:
        assert self.session_id is not None
        return RequestContext.build(
            timezone=self.services.settings.timezone,
            device_type="phone",
            session_id=self.session_id,
            device_id=self.device_id,
            persona_id=persona_id or "default",
            **fields,
        )

    async def _can_start(self, turn_id: str) -> bool:
        if not self.services.voice_ready:
            await self._error(
                "voice_unavailable",
                "Speech isn't enabled on the server (ALFRED_STT_ENABLED, ALFRED_TTS_ENABLED)",
                turn_id,
            )
            return False
        if self._turn is not None:
            await self._error("busy", f"Turn {self._turn.id} is still in progress", turn_id)
            return False
        return True

    def _spawn(self, open_turn: _OpenTurn, work: Callable[[], Awaitable[None]]) -> None:
        self._turn = open_turn
        task = asyncio.create_task(open_turn.turn.guarded(work))
        open_turn.task = task

        def finished(_: asyncio.Task[None]) -> None:
            if self._turn is open_turn:
                self._turn = None

        task.add_done_callback(finished)

    async def _start_turn(self, msg: TurnStart) -> None:
        if not await self._can_start(msg.turn_id):
            return
        if msg.sample_rate != INPUT_SAMPLE_RATE:
            await self._error(
                "bad_sample_rate", f"Send {INPUT_SAMPLE_RATE} Hz mono PCM16", msg.turn_id
            )
            return
        ctx = self._context(
            msg.persona_id,
            active_room=msg.context.active_room,
            location=msg.context.location,
        )
        open_turn = _OpenTurn(msg.turn_id, VoiceTurn(msg.turn_id, self, self.services, ctx))
        # STT reads the audio as it arrives, so an over-long utterance fails straight away.
        self._spawn(open_turn, lambda: open_turn.turn.from_audio(_drain(open_turn.audio)))

    def _on_audio(self, data: bytes) -> None:
        turn = self._turn
        if turn is None or not turn.receiving:
            # Late frames after `turn.end`, a cancel or a failed turn: nothing to do with them.
            logger.debug("dropped %d audio bytes with no open turn", len(data))
            return
        turn.audio.put_nowait(data)

    def _end_turn(self, turn_id: str) -> None:
        turn = self._turn
        if turn is None or turn.id != turn_id or not turn.receiving:
            return  # already finished (e.g. failed on too much audio) or never started
        turn.receiving = False
        turn.turn.timings.ended_at = now()
        turn.audio.put_nowait(None)

    async def _cancel_turn(self, turn_id: str | None, *, notify: bool) -> None:
        turn = self._turn
        if turn is None or (turn_id is not None and turn.id != turn_id):
            return  # already finished: its last message has been sent
        self._turn = None
        if turn.task is not None:
            turn.task.cancel()
            await asyncio.wait([turn.task])
        if notify:
            await self.send_json(message("turn.cancelled", turn_id=turn.id))

    async def _confirm_reply(self, msg: ConfirmReply) -> None:
        turn_id = msg.turn_id or f"confirm-{msg.confirmation_id}"
        if not await self._can_start(turn_id):
            return
        voice_turn = VoiceTurn(turn_id, self, self.services, self._context())
        voice_turn.timings.ended_at = now()
        open_turn = _OpenTurn(turn_id, voice_turn, receiving=False)
        self._spawn(open_turn, lambda: voice_turn.from_confirm_reply(msg))
