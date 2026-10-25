"""One voice turn: audio → STT → router (streamed) → TTS → audio, with per-stage timings.

Reply text is spoken as it streams: tokens go to the TTS, which starts on the first sentence
(or clause) while the model is still writing. A turn ends with `turn.metadata` and then
`reply.audio.end`, or with one `error`. See docs/protocol.md and
docs/decisions/0015-voice-gateway.md.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Coroutine
from dataclasses import dataclass
from typing import Any, Protocol

from agno.db.base import AsyncBaseDb
from agno.run.agent import RunOutput

from alfred_server.agents.context import RequestContext
from alfred_server.agents.registry import get_registry
from alfred_server.agents.router import (
    AgentRunError,
    Done,
    ModelFactory,
    Route,
    Router,
    Token,
    ToolLoader,
)
from alfred_server.agents.storage import StorageUnavailableError, check_agent_db
from alfred_server.chat import Answered, ChatBudget, answer_pending, turn_info
from alfred_server.config import Settings
from alfred_server.confirmations.models import PendingConfirmation, can_approve
from alfred_server.confirmations.service import (
    APP_ONLY_REPLY,
    ConfirmationService,
    ConfirmationsUnavailableError,
    YesNo,
    cancel_paused_run,
    denied_reply,
)
from alfred_server.models.gateway import GatewayNotConfiguredError
from alfred_server.voice.gateway.protocol import ConfirmReply, audio_frame, message
from alfred_server.voice.gateway.yes_no import classify_yes_no
from alfred_server.voice.stt import AudioTooLongError, Partial, SpeechToText
from alfred_server.voice.tts import OUTPUT_SAMPLE_RATE, TextToSpeech, VoiceConfig

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class VoiceServices:
    """Everything a turn needs, resolved once per connection (dependencies, overridable)."""

    settings: Settings
    db: AsyncBaseDb
    model_factory: ModelFactory
    budget: ChatBudget
    confirmations: ConfirmationService
    tools: ToolLoader
    stt: SpeechToText | None
    tts: TextToSpeech | None
    voice: VoiceConfig | None

    @property
    def voice_ready(self) -> bool:
        """STT and TTS are both loaded (`ALFRED_STT_ENABLED`, `ALFRED_TTS_ENABLED`)."""
        return self.stt is not None and self.tts is not None and self.voice is not None

    def router(self) -> Router:
        """A router for one turn, like `chat.router_dependency`."""
        return Router(
            get_registry(),
            self.budget,
            self.model_factory,
            self.db,
            self.confirmations,
            self.tools,
        )


class Sender(Protocol):
    """The connection, as a turn sees it. Sends are no-ops once the device has gone."""

    async def send_json(self, data: dict[str, Any]) -> None: ...

    async def send_bytes(self, data: bytes) -> None: ...


def now() -> float:
    return time.perf_counter()


@dataclass
class Timings:
    """When each stage of a turn finished (`perf_counter` seconds).

    Measured from `ended_at`, when the server got `turn.end` (or `confirm.reply`): the end of
    speech as far as the server can tell. The phone's own VAD delay comes before that.
    """

    ended_at: float | None = None
    transcribed: float | None = None
    first_token: float | None = None
    first_audio: float | None = None
    done: float | None = None
    audio_s: float | None = None
    stt_s: float | None = None

    def _ms(self, at: float | None) -> float | None:
        if at is None or self.ended_at is None:
            return None
        return round((at - self.ended_at) * 1000, 1)

    def as_dict(self) -> dict[str, float | None]:
        return {
            "audio_s": None if self.audio_s is None else round(self.audio_s, 2),
            "stt_ms": None if self.stt_s is None else round(self.stt_s * 1000, 1),
            "transcript_ms": self._ms(self.transcribed),
            "first_token_ms": self._ms(self.first_token),
            "first_audio_ms": self._ms(self.first_audio),
            "total_ms": self._ms(self.done),
        }


class TurnFailed(Exception):
    """End the turn with an `error` message."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class VoiceTurn:
    """One turn on one connection. `guarded(...)` runs it and turns failures into `error`."""

    def __init__(
        self, turn_id: str, sender: Sender, services: VoiceServices, ctx: RequestContext
    ) -> None:
        self.turn_id = turn_id
        self.sender = sender
        self.services = services
        self.ctx = ctx
        self.timings = Timings()

    async def send(self, type_: str, **fields: Any) -> None:
        await self.sender.send_json(message(type_, turn_id=self.turn_id, **fields))

    async def guarded(self, work: Callable[[], Awaitable[None]]) -> None:
        try:
            await work()
        except TurnFailed as exc:
            await self.send("error", code=exc.code, message=exc.message)
        except ConfirmationsUnavailableError as exc:
            logger.error("voice turn %s: %s", self.turn_id, exc)
            await self.send("error", code="confirmations_unavailable", message=str(exc))
        except GatewayNotConfiguredError as exc:
            logger.error("voice turn %s: %s", self.turn_id, exc)
            await self.send("error", code="gateway_unavailable", message=str(exc))
        except AgentRunError as exc:
            logger.error("voice turn %s: agent run failed: %s", self.turn_id, exc)
            await self.send("error", code="agent_failed", message="The agent run failed")
        except Exception:
            logger.exception("voice turn %s failed", self.turn_id)
            await self.send("error", code="internal", message="Something went wrong")

    # --- Entry points -----------------------------------------------------------------------

    async def from_audio(self, chunks: AsyncIterator[bytes]) -> None:
        """Transcribe the streamed utterance, then answer it."""
        stt = self.services.stt
        assert stt is not None  # checked before the turn started
        try:
            transcript = await stt.transcribe(chunks, on_partial=self._send_partial)
        except AudioTooLongError as exc:
            raise TurnFailed("audio_too_long", str(exc)) from exc
        except ValueError as exc:  # e.g. an odd number of PCM bytes
            raise TurnFailed("bad_audio", str(exc)) from exc
        except Exception as exc:
            logger.exception("STT failed (turn %s)", self.turn_id)
            raise TurnFailed("stt_failed", "Speech recognition failed") from exc
        self.timings.transcribed = now()
        self.timings.audio_s = transcript.audio_duration_s
        self.timings.stt_s = transcript.processing_s
        text = transcript.text.strip()
        await self.send(
            "transcript.final",
            text=text,
            language=transcript.language,
            audio_s=round(transcript.audio_duration_s, 2),
        )
        if not text:
            raise TurnFailed("no_speech", "No speech was recognised")
        await self.respond(text)

    async def _send_partial(self, partial: Partial) -> None:
        await self.send("transcript.partial", stable=partial.stable, unstable=partial.unstable)

    async def respond(self, text: str) -> None:
        """Answer a pending confirmation with `text`, or route it as a new request."""
        router = self.services.router()
        await self._check_storage()
        # Shielded: a "yes" here resumes the paused run, which must finish (see `_detached`).
        answered = await _detached(
            answer_pending(text, self.ctx, self.services.confirmations, router, parse=self._yes_no)
        )
        if answered is not None:
            await self._speak_answer(answered)
            return
        route = await router.route(text, self.ctx)
        await self._stream(router, route, text)

    async def from_confirm_reply(self, reply: ConfirmReply) -> None:
        """Answer a confirmation from the app's buttons (channel `voice`, like a spoken one)."""
        router = self.services.router()
        await self._check_storage()
        confirmations = self.services.confirmations
        try:
            c = await asyncio.to_thread(confirmations.get, reply.confirmation_id)
        except Exception as exc:
            raise ConfirmationsUnavailableError("Confirmations are unavailable") from exc
        # Only this conversation's: a device can't answer another device's confirmation.
        if c is None or c.session_id != self.ctx.session_id:
            raise TurnFailed("unknown_confirmation", "No such confirmation")
        if c.status == "pending" and confirmations.is_expired(c):
            await confirmations.expire_due(router.db)
            raise TurnFailed("confirmation_closed", "This confirmation has expired")
        if c.status != "pending":
            raise TurnFailed("confirmation_closed", f"This confirmation was already {c.status}")
        await self._speak_answer(await _detached(self._answer(c, reply.approve, router)))

    # --- Stages -----------------------------------------------------------------------------

    async def _yes_no(self, text: str) -> YesNo | None:
        return await classify_yes_no(text, self.services.model_factory)

    async def _check_storage(self) -> None:
        try:
            await check_agent_db(self.services.db)
        except StorageUnavailableError as exc:
            logger.error("voice turn %s refused: %s", self.turn_id, exc)
            raise TurnFailed("storage_unavailable", "Conversation storage is unavailable") from exc

    async def _answer(self, c: PendingConfirmation, approve: bool, router: Router) -> Answered:
        """Mirrors `POST /confirmations/{id}`, on the voice channel."""
        confirmations = self.services.confirmations
        if not approve:
            if await asyncio.to_thread(confirmations.resolve, c, "denied"):
                await cancel_paused_run(router.db, c, denied_reply(c))
            return Answered(denied_reply(c))
        tier = await asyncio.to_thread(confirmations.current_tier, c)
        # `irreversible` needs the app's confirmation screen (`POST /confirmations/{id}` with
        # channel `app_ui`), never the voice connection (ADR 0013, 0015).
        if not can_approve(tier, "voice"):
            return Answered(APP_ONLY_REPLY, confirmation=c)
        if not await asyncio.to_thread(confirmations.resolve, c, "approved"):
            return Answered("That's already been dealt with.")
        routed = await router.resume(c, self.ctx)
        return Answered(routed.reply, routed, routed.confirmation)

    async def _stream(self, router: Router, route: Route, text: str) -> None:
        speaker = _Speaker(self)
        done: Done | None = None
        failed = False
        try:
            async for item in router.stream(route, text, self.ctx):
                if isinstance(item, Token):
                    await speaker.say(item.text)
                elif isinstance(item, Done):
                    done = item
                else:
                    failed = True
            if failed or done is None:
                # Stop speaking a half-finished reply; the error says what happened.
                raise TurnFailed("agent_failed", "The agent run failed")
            await speaker.finish()
        finally:
            speaker.close()
        await self._finish(done, done.confirmation)

    async def _speak_answer(self, answered: Answered) -> None:
        speaker = _Speaker(self)
        try:
            await speaker.say(answered.reply)
            await speaker.finish()
        finally:
            speaker.close()
        done = None
        if answered.routed is not None:
            run = answered.routed.run
            tools = tuple(t.tool_name for t in run.tools or [] if t.tool_name)
            done = Done(
                answered.reply,
                answered.routed.route,
                tools,
                run.metrics,
                answered.confirmation,
                run.run_id,
            )
        await self._finish(done, answered.confirmation)

    async def _finish(self, done: Done | None, confirmation: PendingConfirmation | None) -> None:
        if confirmation is not None:
            await self.send(
                "confirm.request",
                id=confirmation.id,
                summary=confirmation.summary,
                tier=confirmation.tier,
                expires_at=confirmation.expires_at.isoformat(),
            )
        self.timings.done = now()
        timings = self.timings.as_dict()
        info = await turn_info(done, self.services.budget) if done is not None else {}
        await self.send("turn.metadata", **info, timings=timings)
        logger.info(
            "voice turn %s: %s",
            self.turn_id,
            ", ".join(f"{k}={v}" for k, v in timings.items() if v is not None),
        )
        # Before the last message: the device may hang up as soon as it has it.
        if done is not None and done.run_id is not None:
            await self._store_timings(done.run_id, timings)
        await self.send("reply.audio.end")

    async def _store_timings(self, run_id: str, timings: dict[str, float | None]) -> None:
        """Add the timings to the stored run's metadata, next to `routing`. Best effort."""
        db = self.services.db
        try:
            run = await db.get_run(run_id)
            if not isinstance(run, RunOutput):
                return
            run.metadata = {**(run.metadata or {}), "voice": {"timings": timings}}
            await db.upsert_run(run, session_id=self.ctx.session_id)
        except Exception:
            logger.warning("couldn't store voice timings on run %s", run_id, exc_info=True)


# Answers still running after their turn was cancelled; held so they aren't garbage collected.
_DETACHED: set[asyncio.Task[Any]] = set()


async def _detached[T](work: Coroutine[Any, Any, T]) -> T:
    """Run `work` to completion even if the turn awaiting it is cancelled.

    Used for answering a confirmation: once it's resolved `approved`, the run must continue
    and be stored, whether or not the device is still there to hear the result. A disconnect,
    a reconnect (which replaces the connection) or `turn.cancel` cancels the turn, and with it
    only the spoken reply; the action and its record in the conversation still complete.
    """
    task = asyncio.create_task(work)
    _DETACHED.add(task)
    task.add_done_callback(_DETACHED.discard)
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        task.add_done_callback(_log_abandoned)
        raise


def _log_abandoned(task: asyncio.Task[Any]) -> None:
    """Nobody is waiting for this answer any more: log how it ended."""
    if task.cancelled():
        return
    if (exc := task.exception()) is not None:
        logger.error("answering a confirmation failed after its turn ended", exc_info=exc)
    else:
        logger.info("a confirmation was answered after its turn ended; the reply wasn't spoken")


class _Speaker:
    """Sends reply text as `reply.text.delta` and speaks it, streaming tagged audio frames."""

    def __init__(self, turn: VoiceTurn) -> None:
        self._turn = turn
        self._text: asyncio.Queue[str | None] = asyncio.Queue()
        self._task = asyncio.create_task(self._pump())

    async def say(self, text: str) -> None:
        if self._turn.timings.first_token is None:
            self._turn.timings.first_token = now()
        await self._turn.send("reply.text.delta", text=text)
        self._text.put_nowait(text)

    async def finish(self) -> None:
        """Wait until everything said so far has been spoken."""
        self._text.put_nowait(None)
        try:
            await self._task
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.exception("TTS failed (turn %s)", self._turn.turn_id)
            raise TurnFailed("tts_failed", "Speech synthesis failed") from exc

    def close(self) -> None:
        if not self._task.done():
            self._task.cancel()
        elif not self._task.cancelled():
            self._task.exception()  # retrieved, so asyncio doesn't log it again

    async def _texts(self) -> AsyncIterator[str]:
        while (text := await self._text.get()) is not None:
            yield text

    async def _pump(self) -> None:
        turn = self._turn
        tts, voice = turn.services.tts, turn.services.voice
        assert tts is not None and voice is not None  # checked before the turn started
        started = False
        async for chunk in tts.synthesize(self._texts(), voice):
            if not started:
                started = True
                turn.timings.first_audio = now()
                await turn.send(
                    "reply.audio.start",
                    sample_rate=OUTPUT_SAMPLE_RATE,
                    encoding="pcm_s16le",
                    channels=1,
                )
            await turn.sender.send_bytes(audio_frame(turn.turn_id, chunk))
