"""The device WebSocket `/ws/device` (docs/protocol.md): auth, voice turns, confirmations.

The router runs for real against a mocked LiteLLM proxy (as in test_chat.py). STT is the real
`FasterWhisperSTT` around a fake Whisper model; TTS is a fake that "speaks" each sentence as
`<sentence>` bytes, so tests can read back what was said.
"""

import asyncio
import difflib
import json
import os
import re
import wave
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest
from agno.run.agent import RunOutput
from starlette.testclient import WebSocketTestSession
from starlette.websockets import WebSocketDisconnect

from alfred_server.config import Settings
from alfred_server.connectors.builtin.test_messages import SENT_TEST_MESSAGES
from alfred_server.voice.gateway.connection import (
    CLOSE_BAD_HELLO,
    CLOSE_NOT_CONFIGURED,
    CLOSE_REPLACED,
    CLOSE_TIMEOUT,
    CLOSE_UNAUTHORIZED,
)
from alfred_server.voice.gateway.protocol import (
    ProtocolError,
    TurnStart,
    audio_frame,
    parse_audio_frame,
    parse_message,
)
from alfred_server.voice.gateway.routes import voice_stt_dependency, voice_tts_dependency
from alfred_server.voice.gateway.yes_no import classify_yes_no
from alfred_server.voice.stt import FasterWhisperSTT
from alfred_server.voice.tts import OUTPUT_SAMPLE_RATE, VoiceConfig, split_sentences
from tests.llm_fakes import (
    completion,
    reply,
    route_completion,
    streamed_completion,
    streamed_tool_call,
)
from tests.test_chat import AUTH, TOKEN, ChatHarness, RecordingDb
from tests.test_confirmations import ASK, DENIED, SAM, SUMMARY, set_risk

FIXTURES = Path(__file__).parent / "fixtures" / "audio"
HELLO = {"type": "hello", "device_id": "pixel-9", "app_version": "0.1.0"}
CHUNK = 3200  # 100 ms of 16 kHz PCM16


def fixture_pcm(name: str = "timer.wav") -> bytes:
    with wave.open(str(FIXTURES / name), "rb") as wav:
        assert (wav.getframerate(), wav.getnchannels(), wav.getsampwidth()) == (16000, 1, 2)
        return wav.readframes(wav.getnframes())


class FakeWhisper:
    """Stands in for `faster_whisper.WhisperModel`: returns queued texts, records the audio."""

    def __init__(self) -> None:
        self.texts: list[str] = []
        self.audio: list[np.ndarray] = []

    def transcribe(self, audio: np.ndarray, **_: Any) -> tuple[list[Any], Any]:
        self.audio.append(audio)
        text = self.texts.pop(0)
        return [SimpleNamespace(text=text)], SimpleNamespace(language="en")


class FakeTTS:
    """`TextToSpeech` that renders each sentence as the bytes `<sentence>`."""

    def __init__(self) -> None:
        self.default_voice = VoiceConfig("piper", "en_GB-alan-medium")
        self.spoken: list[str] = []
        self.fail = False

    async def synthesize(
        self, text_stream: AsyncIterator[str], voice: VoiceConfig
    ) -> AsyncIterator[bytes]:
        async for sentence in split_sentences(text_stream):
            if self.fail:
                raise RuntimeError("TTS broke")
            self.spoken.append(sentence)
            yield f"<{sentence}>".encode()

    def voices(self) -> list[Any]:
        return []


class VoiceHarness(ChatHarness):
    def __init__(self, db: RecordingDb, stt_settings: Settings | None = None) -> None:
        super().__init__(db)
        self.whisper = FakeWhisper()
        self.stt = FasterWhisperSTT(stt_settings or Settings(_env_file=None), model=self.whisper)
        self.tts = FakeTTS()
        self.voice_enabled = True
        self.app.dependency_overrides[voice_stt_dependency] = lambda: (
            self.stt if self.voice_enabled else None
        )
        self.app.dependency_overrides[voice_tts_dependency] = lambda: (
            self.tts if self.voice_enabled else None
        )

    def connect(self, headers: dict[str, str] | None = None) -> WebSocketTestSession:
        return self.client.websocket_connect(
            "/ws/device", headers=AUTH if headers is None else headers
        )

    def hear(self, *texts: str) -> None:
        """What STT will "hear" in the next turns."""
        self.whisper.texts.extend(texts)


@pytest.fixture
def harness(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> VoiceHarness:
    monkeypatch.setenv("ALFRED_API_TOKEN", TOKEN)
    return VoiceHarness(RecordingDb(tmp_path / "agno.db"))


@pytest.fixture(autouse=True)
def clear_outbox() -> Iterator[None]:
    SENT_TEST_MESSAGES.clear()
    yield
    SENT_TEST_MESSAGES.clear()


def hello(ws: WebSocketTestSession, **fields: Any) -> dict[str, Any]:
    ws.send_json({**HELLO, **fields})
    answer: dict[str, Any] = ws.receive_json()
    assert answer["type"] == "hello.ok", answer
    return answer


class Turn:
    """Everything the server sent for one turn."""

    def __init__(self) -> None:
        self.messages: list[dict[str, Any]] = []
        self.audio: list[tuple[str, bytes]] = []

    def types(self) -> list[str]:
        return [m["type"] for m in self.messages]

    def first(self, type_: str) -> dict[str, Any]:
        return next(m for m in self.messages if m["type"] == type_)

    def text(self) -> str:
        return "".join(m["text"] for m in self.messages if m["type"] == "reply.text.delta")

    def said(self) -> str:
        """The sentences the fake TTS spoke, joined with spaces."""
        pcm = b"".join(pcm for _, pcm in self.audio).decode()
        assert pcm.startswith("<") and pcm.endswith(">"), pcm
        return " ".join(pcm[1:-1].split("><"))


def receive_turn(ws: WebSocketTestSession) -> Turn:
    """Read frames until the turn ends (`reply.audio.end`, `error` or `turn.cancelled`)."""
    turn = Turn()
    while True:
        frame = ws.receive()
        if frame["type"] == "websocket.close":
            raise AssertionError(f"socket closed mid-turn: {frame}")
        if frame.get("bytes") is not None:
            turn.audio.append(parse_audio_frame(frame["bytes"]))
            continue
        data = json.loads(frame["text"])
        turn.messages.append(data)
        if data["type"] in ("reply.audio.end", "error", "turn.cancelled"):
            return turn


def speak(ws: WebSocketTestSession, turn_id: str = "t1", pcm: bytes | None = None) -> Turn:
    """Send one utterance as a turn and read the reply."""
    pcm = fixture_pcm() if pcm is None else pcm
    ws.send_json({"type": "turn.start", "turn_id": turn_id, "sample_rate": 16000})
    for start in range(0, len(pcm), CHUNK):
        ws.send_bytes(pcm[start : start + CHUNK])
    ws.send_json({"type": "turn.end", "turn_id": turn_id})
    return receive_turn(ws)


def close_code(ws: WebSocketTestSession) -> int:
    with pytest.raises(WebSocketDisconnect) as closed:
        ws.receive_json()
    return closed.value.code


# --- Auth and connection ----------------------------------------------------------------------


def test_header_token_then_hello(harness: VoiceHarness) -> None:
    with harness.connect() as ws:
        answer = hello(ws)
    assert answer["protocol"] == 1
    assert answer["input_sample_rate"] == 16000
    assert answer["output_sample_rate"] == OUTPUT_SAMPLE_RATE
    assert answer["voice"] is True
    assert answer["session_id"]


def test_token_in_hello(harness: VoiceHarness) -> None:
    with harness.connect(headers={}) as ws:
        assert hello(ws, token=TOKEN)["type"] == "hello.ok"


@pytest.mark.parametrize(
    ("headers", "token"),
    [
        ({"Authorization": "Bearer wrong"}, None),
        ({}, "wrong"),
        ({}, None),
        # A wrong header isn't rescued by a right hello token.
        ({"Authorization": "Bearer wrong"}, TOKEN),
    ],
)
def test_bad_token_is_refused(
    harness: VoiceHarness, headers: dict[str, str], token: str | None
) -> None:
    with harness.connect(headers=headers) as ws:
        if "Authorization" not in headers:
            ws.send_json({**HELLO, "token": token})
        assert close_code(ws) == CLOSE_UNAUTHORIZED


def test_refused_when_no_token_is_configured(
    harness: VoiceHarness, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("ALFRED_API_TOKEN")
    with harness.connect() as ws:
        assert close_code(ws) == CLOSE_NOT_CONFIGURED


def test_nothing_is_processed_before_hello(harness: VoiceHarness) -> None:
    with harness.connect() as ws:
        ws.send_json({"type": "ping"})
        assert close_code(ws) == CLOSE_BAD_HELLO


def test_no_hello_times_out(harness: VoiceHarness, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALFRED_VOICE_AUTH_TIMEOUT_S", "0.1")
    with harness.connect() as ws:
        assert close_code(ws) == CLOSE_TIMEOUT


def test_idle_connection_is_closed(harness: VoiceHarness, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALFRED_VOICE_IDLE_TIMEOUT_S", "0.2")
    with harness.connect() as ws:
        hello(ws)
        assert close_code(ws) == CLOSE_TIMEOUT


def test_unsupported_protocol(harness: VoiceHarness) -> None:
    with harness.connect() as ws:
        ws.send_json({**HELLO, "protocol": 2})
        assert ws.receive_json()["code"] == "unsupported_protocol"
        assert close_code(ws) == CLOSE_BAD_HELLO


def test_ping_pong(harness: VoiceHarness) -> None:
    with harness.connect() as ws:
        hello(ws)
        ws.send_json({"type": "ping", "t": 12.5})
        assert ws.receive_json() == {"type": "pong", "t": 12.5}


def test_bad_messages_get_an_error_and_the_connection_stays(harness: VoiceHarness) -> None:
    with harness.connect() as ws:
        hello(ws)
        ws.send_text("not json")
        assert ws.receive_json()["code"] == "bad_message"
        ws.send_json({"type": "turn.start"})  # no turn_id
        assert ws.receive_json()["code"] == "bad_message"
        ws.send_json({"type": "tool.result"})  # reserved for device tools
        assert ws.receive_json()["code"] == "unsupported"
        ws.send_json({"type": "ping"})
        assert ws.receive_json()["type"] == "pong"


def test_reconnecting_device_keeps_its_conversation(harness: VoiceHarness) -> None:
    harness.hear("My name is Dan.", "What's my name?")
    harness.reply_with(
        route_completion(),
        streamed_completion("Nice to meet you, Dan."),
        route_completion(),
        streamed_completion("You're Dan."),
    )
    with harness.connect() as ws:
        first = hello(ws)["session_id"]
        speak(ws)
    with harness.connect() as ws:
        assert hello(ws)["session_id"] == first
        speak(ws, "t2")
    history = [str(m["content"]) for m in harness.proxy.body(3)["messages"]]
    assert "My name is Dan." in history
    assert "Nice to meet you, Dan." in history


def test_hello_can_start_a_new_conversation(harness: VoiceHarness) -> None:
    with harness.connect() as ws:
        first = hello(ws)["session_id"]
    with harness.connect() as ws:
        assert hello(ws, session_id="fresh-1")["session_id"] == "fresh-1"
    assert first != "fresh-1"


def test_a_second_connection_replaces_the_first(harness: VoiceHarness) -> None:
    with harness.connect() as old:
        hello(old)
        with harness.connect() as new:
            hello(new)
            assert close_code(old) == CLOSE_REPLACED
            new.send_json({"type": "ping"})
            assert new.receive_json()["type"] == "pong"


# --- Voice turns ------------------------------------------------------------------------------


def test_a_full_turn(harness: VoiceHarness) -> None:
    harness.hear("Set a timer for ten minutes.")
    harness.reply_with(
        route_completion(level="simple", tools=0),
        streamed_completion("I can't set timers yet. ", "Sorry about that!"),
    )
    pcm = fixture_pcm()

    with harness.connect() as ws:
        hello(ws)
        turn = speak(ws, "t1", pcm)

    assert turn.types()[0] == "transcript.final"
    assert turn.first("transcript.final") == {
        "type": "transcript.final",
        "turn_id": "t1",
        "text": "Set a timer for ten minutes.",
        "language": "en",
        "audio_s": round(len(pcm) / 32000, 2),
    }
    assert turn.text() == "I can't set timers yet. Sorry about that!"
    assert turn.first("reply.audio.start") == {
        "type": "reply.audio.start",
        "turn_id": "t1",
        "sample_rate": OUTPUT_SAMPLE_RATE,
        "encoding": "pcm_s16le",
        "channels": 1,
    }
    # Every audio frame is tagged with the turn; the TTS spoke the streamed reply.
    assert {turn_id for turn_id, _ in turn.audio} == {"t1"}
    assert turn.said() == "I can't set timers yet. Sorry about that!"
    assert harness.tts.spoken == ["I can't set timers yet.", "Sorry about that!"]
    assert turn.types()[-2:] == ["turn.metadata", "reply.audio.end"]
    # STT got exactly the audio that was sent.
    assert len(harness.whisper.audio[0]) == len(pcm) // 2


def test_turn_metadata_says_how_it_ran_and_how_long_it_took(harness: VoiceHarness) -> None:
    harness.hear("Hello.")
    harness.reply_with(route_completion(level="simple", tools=0), streamed_completion("Hi."))

    with harness.connect() as ws:
        hello(ws)
        meta = speak(ws).first("turn.metadata")

    assert meta["specialist"] == "general"
    assert (meta["alias"], meta["tier"], meta["reason"]) == ("local-router", "local", "simple")
    assert meta["cost_gbp"] is None
    timings = meta["timings"]
    assert set(timings) == {
        "audio_s",
        "stt_ms",
        "transcript_ms",
        "first_token_ms",
        "first_audio_ms",
        "total_ms",
    }
    stages = [timings[k] for k in ("transcript_ms", "first_token_ms", "first_audio_ms")]
    assert all(v is not None and v >= 0 for v in stages)
    assert stages == sorted(stages)
    assert timings["total_ms"] >= timings["first_audio_ms"]


def test_timings_are_stored_on_the_run(harness: VoiceHarness) -> None:
    harness.hear("Hello.")
    harness.reply_with(route_completion(level="simple", tools=0), streamed_completion("Hi."))

    with harness.connect() as ws:
        session_id = hello(ws)["session_id"]
        timings = speak(ws).first("turn.metadata")["timings"]

    session: Any = asyncio.run(harness.db.get_session(session_id, session_type=_agent()))
    run: RunOutput = session.runs[-1]
    assert run.metadata is not None
    assert run.metadata["voice"] == {"timings": timings}
    assert run.metadata["routing"]["specialist"] == "general"  # kept


def _agent() -> Any:
    from agno.db.base import SessionType

    return SessionType.AGENT


def test_request_context_comes_from_the_turn(harness: VoiceHarness) -> None:
    harness.hear("Lights on.")
    harness.reply_with(route_completion(), streamed_completion("Done."))

    with harness.connect() as ws:
        hello(ws)
        ws.send_json(
            {
                "type": "turn.start",
                "turn_id": "t1",
                "persona_id": "butler",
                "context": {"active_room": "lounge", "location": "home"},
            }
        )
        ws.send_bytes(fixture_pcm())
        ws.send_json({"type": "turn.end", "turn_id": "t1"})
        receive_turn(ws)

    system = harness.proxy.body(1)["messages"][0]["content"]
    for line in (
        "device_type: phone",
        "device_id: pixel-9",
        "active_room: lounge",
        "location: home",
        "persona_id: butler",
    ):
        assert line in system


def test_no_speech_ends_the_turn(harness: VoiceHarness) -> None:
    harness.hear("  ")
    with harness.connect() as ws:
        hello(ws)
        turn = speak(ws)
    assert turn.types() == ["transcript.final", "error"]
    assert turn.messages[-1]["code"] == "no_speech"
    assert harness.proxy.requests == []


def test_too_much_audio_fails_while_it_streams(harness: VoiceHarness) -> None:
    h = VoiceHarness(harness.db, Settings(_env_file=None, stt_max_audio_s=0.5))
    with h.connect() as ws:
        hello(ws)
        ws.send_json({"type": "turn.start", "turn_id": "t1"})
        ws.send_bytes(b"\x00\x00" * 16000)  # 1 s
        error = ws.receive_json()
        # Late audio and the turn's own end are ignored; the connection carries on.
        ws.send_bytes(b"\x00\x00" * 1600)
        ws.send_json({"type": "turn.end", "turn_id": "t1"})
        ws.send_json({"type": "ping"})
        assert ws.receive_json()["type"] == "pong"
    assert (error["type"], error["code"], error["turn_id"]) == ("error", "audio_too_long", "t1")


def test_odd_audio_is_rejected(harness: VoiceHarness) -> None:
    with harness.connect() as ws:
        hello(ws)
        turn = speak(ws, pcm=b"\x00\x00\x00")
    assert turn.messages[-1]["code"] == "bad_audio"


def test_wrong_sample_rate_is_rejected(harness: VoiceHarness) -> None:
    with harness.connect() as ws:
        hello(ws)
        ws.send_json({"type": "turn.start", "turn_id": "t1", "sample_rate": 48000})
        error = ws.receive_json()
    assert (error["code"], error["turn_id"]) == ("bad_sample_rate", "t1")


def test_one_turn_at_a_time(harness: VoiceHarness) -> None:
    harness.hear("Hello.")
    harness.reply_with(route_completion(level="simple", tools=0), streamed_completion("Hi."))
    with harness.connect() as ws:
        hello(ws)
        ws.send_json({"type": "turn.start", "turn_id": "t1"})
        ws.send_json({"type": "turn.start", "turn_id": "t2"})
        busy = ws.receive_json()
        ws.send_bytes(fixture_pcm())
        ws.send_json({"type": "turn.end", "turn_id": "t1"})
        turn = receive_turn(ws)
    assert (busy["code"], busy["turn_id"]) == ("busy", "t2")
    assert turn.types()[-1] == "reply.audio.end"


def test_audio_without_a_turn_is_dropped(harness: VoiceHarness) -> None:
    with harness.connect() as ws:
        hello(ws)
        ws.send_bytes(b"\x00\x00" * 160)
        ws.send_json({"type": "ping"})
        assert ws.receive_json()["type"] == "pong"


def test_cancel_stops_the_turn(harness: VoiceHarness) -> None:
    with harness.connect() as ws:
        hello(ws)
        ws.send_json({"type": "turn.start", "turn_id": "t1"})
        ws.send_bytes(fixture_pcm())
        ws.send_json({"type": "turn.cancel", "turn_id": "t1"})
        turn = receive_turn(ws)
        # The next turn starts cleanly.
        harness.hear("Hello.")
        harness.reply_with(route_completion(level="simple", tools=0), streamed_completion("Hi."))
        second = speak(ws, "t2")
    assert turn.types() == ["turn.cancelled"]
    assert turn.messages[0]["turn_id"] == "t1"
    assert second.types()[-1] == "reply.audio.end"
    assert len(harness.whisper.audio) == 1  # only t2 was transcribed


def test_agent_failure_ends_the_turn_with_an_error(harness: VoiceHarness) -> None:
    # Only the classifier's answer is queued: the specialist's call fails.
    harness.hear("Hello.")
    harness.reply_with(route_completion())
    with harness.connect() as ws:
        hello(ws)
        turn = speak(ws)
    assert turn.messages[-1]["code"] == "agent_failed"


def test_tts_failure_ends_the_turn_with_an_error(harness: VoiceHarness) -> None:
    harness.hear("Hello.")
    harness.reply_with(route_completion(level="simple", tools=0), streamed_completion("Hi."))
    harness.tts.fail = True
    with harness.connect() as ws:
        hello(ws)
        turn = speak(ws)
    assert turn.text() == "Hi."  # the text still reached the app
    assert turn.messages[-1]["code"] == "tts_failed"


def test_voice_disabled(harness: VoiceHarness) -> None:
    harness.voice_enabled = False
    with harness.connect() as ws:
        assert hello(ws)["voice"] is False
        ws.send_json({"type": "turn.start", "turn_id": "t1"})
        assert ws.receive_json()["code"] == "voice_unavailable"


# --- Confirmations ----------------------------------------------------------------------------


@pytest.fixture
def tools_harness(harness: VoiceHarness, monkeypatch: pytest.MonkeyPatch) -> VoiceHarness:
    monkeypatch.setenv("ALFRED_TEST_TOOLS_ENABLED", "true")
    return harness


def ask_to_send(h: VoiceHarness, ws: WebSocketTestSession) -> Turn:
    h.hear("Text Sam that I'm running 10 mins late.")
    h.reply_with(route_completion(), streamed_tool_call("send_test_message", SAM))
    return speak(ws, "ask")


def test_a_write_tool_asks_out_loud(tools_harness: VoiceHarness) -> None:
    h = tools_harness
    with h.connect() as ws:
        hello(ws)
        turn = ask_to_send(h, ws)
    assert turn.said() == ASK
    request = turn.first("confirm.request")
    assert (request["tier"], request["summary"]) == ("write", SUMMARY)
    assert turn.types()[-2:] == ["turn.metadata", "reply.audio.end"]
    assert SENT_TEST_MESSAGES == []


def test_saying_yes_sends_it(tools_harness: VoiceHarness) -> None:
    h = tools_harness
    with h.connect() as ws:
        hello(ws)
        ask_to_send(h, ws)
        h.hear("Yes.")
        h.reply_with(reply("Sent."))  # no classifier: the answer isn't routed
        turn = speak(ws, "yes")
    assert turn.said() == "Sent."
    assert turn.first("turn.metadata")["tools"] == ["send_test_message"]
    assert SENT_TEST_MESSAGES == [("Sam", "Running 10 mins late")]


def test_a_loose_yes_is_understood_by_the_model(tools_harness: VoiceHarness) -> None:
    h = tools_harness
    with h.connect() as ws:
        hello(ws)
        ask_to_send(h, ws)
        h.hear("Yeah, go on then.")
        h.reply_with(
            completion({"role": "assistant", "content": '{"answer":"yes"}'}),
            reply("Sent."),
        )
        turn = speak(ws, "yes")
    assert h.proxy.body(2)["model"] == "local-router"
    assert turn.said() == "Sent."
    assert SENT_TEST_MESSAGES == [("Sam", "Running 10 mins late")]


def test_saying_no_cancels_it(tools_harness: VoiceHarness) -> None:
    h = tools_harness
    with h.connect() as ws:
        hello(ws)
        ask_to_send(h, ws)
        h.hear("No.")
        turn = speak(ws, "no")
    assert turn.said() == DENIED
    assert SENT_TEST_MESSAGES == []


def test_moving_on_cancels_and_routes_the_request(tools_harness: VoiceHarness) -> None:
    h = tools_harness
    with h.connect() as ws:
        hello(ws)
        ask_to_send(h, ws)
        h.hear("What's the weather like?")
        h.reply_with(
            completion({"role": "assistant", "content": '{"answer":"other"}'}),
            route_completion(),
            streamed_completion("It's sunny."),
        )
        turn = speak(ws, "next")
        h.hear("Yes.")
        h.reply_with(route_completion(), streamed_completion("Yes to what?"))
        late = speak(ws, "late")
    assert turn.said() == "It's sunny."
    assert late.said() == "Yes to what?"
    assert SENT_TEST_MESSAGES == []


def test_confirm_reply_approves(tools_harness: VoiceHarness) -> None:
    h = tools_harness
    with h.connect() as ws:
        hello(ws)
        c = ask_to_send(h, ws).first("confirm.request")
        h.reply_with(reply("Sent."))
        ws.send_json({"type": "confirm.reply", "confirmation_id": c["id"], "approve": True})
        turn = receive_turn(ws)
    assert {t for t, _ in turn.audio} == {f"confirm-{c['id']}"}
    assert turn.said() == "Sent."
    assert SENT_TEST_MESSAGES == [("Sam", "Running 10 mins late")]


def test_confirm_reply_denies(tools_harness: VoiceHarness) -> None:
    h = tools_harness
    with h.connect() as ws:
        hello(ws)
        c = ask_to_send(h, ws).first("confirm.request")
        ws.send_json(
            {"type": "confirm.reply", "confirmation_id": c["id"], "approve": False, "turn_id": "n"}
        )
        turn = receive_turn(ws)
        # Answered: a second reply is refused.
        ws.send_json({"type": "confirm.reply", "confirmation_id": c["id"], "approve": True})
        again = receive_turn(ws)
    assert turn.said() == DENIED
    assert again.messages[-1]["code"] == "confirmation_closed"
    assert SENT_TEST_MESSAGES == []


def test_confirm_reply_for_an_unknown_confirmation(tools_harness: VoiceHarness) -> None:
    with tools_harness.connect() as ws:
        hello(ws)
        ws.send_json({"type": "confirm.reply", "confirmation_id": "nope", "approve": True})
        assert receive_turn(ws).messages[-1]["code"] == "unknown_confirmation"


def test_irreversible_cannot_be_approved_by_voice(tools_harness: VoiceHarness) -> None:
    h = tools_harness
    assert set_risk(h, "irreversible").status_code == 200
    with h.connect() as ws:
        hello(ws)
        asked = ask_to_send(h, ws)
        c = asked.first("confirm.request")
        h.hear("Yes.")
        said = speak(ws, "yes")
        ws.send_json({"type": "confirm.reply", "confirmation_id": c["id"], "approve": True})
        tapped = receive_turn(ws)
    assert c["tier"] == "irreversible"
    assert asked.said().endswith("Please approve it in the Alfred app.")
    assert said.said() == tapped.said() == "That one needs approving in the Alfred app."
    assert tapped.first("confirm.request")["id"] == c["id"]  # still pending, for the app
    assert SENT_TEST_MESSAGES == []
    pending = h.client.get("/confirmations/pending", headers=AUTH).json()["pending"]
    assert [p["id"] for p in pending] == [c["id"]]


# --- Units ------------------------------------------------------------------------------------


def test_audio_frames_round_trip() -> None:
    frame = audio_frame("turn-1", b"\x01\x02\x03")
    assert frame[:2] == bytes([0x01, 6])
    assert parse_audio_frame(frame) == ("turn-1", b"\x01\x02\x03")
    with pytest.raises(ProtocolError):
        parse_audio_frame(b"\x02\x00")


def test_turn_ids_and_context_are_validated() -> None:
    assert isinstance(parse_message('{"type":"turn.start","turn_id":"a:b.c-1"}'), TurnStart)
    for bad in (
        '{"type":"turn.start","turn_id":"has space"}',
        '{"type":"turn.start","turn_id":"' + "x" * 65 + '"}',
        '{"type":"turn.start","turn_id":"t","context":{"active_room":"a\\nb"}}',
        '{"type":"turn.start","turn_id":"t","context":{"location":"<x>"}}',
    ):
        with pytest.raises(ProtocolError):
            parse_message(bad)


def test_errors_never_echo_the_token() -> None:
    with pytest.raises(ProtocolError) as error:
        parse_message(json.dumps({"type": "hello", "device_id": "bad id", "token": "s3cret"}))
    assert "s3cret" not in error.value.message


@pytest.mark.parametrize(
    ("content", "expected"),
    [('{"answer":"yes"}', "yes"), ('{"answer":"no"}', "no"), ('{"answer":"other"}', None)],
)
def test_classify_yes_no(harness: VoiceHarness, content: str, expected: str | None) -> None:
    harness.reply_with(completion({"role": "assistant", "content": content}))
    factory = harness._model
    assert asyncio.run(classify_yes_no("yeah go on", factory)) == expected
    assert harness.proxy.body(0)["model"] == "local-router"


def test_classify_yes_no_failure_is_neither(harness: VoiceHarness) -> None:
    # Nothing queued: the proxy call fails.
    assert asyncio.run(classify_yes_no("yeah go on", harness._model)) is None


@pytest.mark.model
def test_fixture_audio_through_real_stt(harness: VoiceHarness) -> None:
    """Integration: fixture audio → real faster-whisper → mocked router → TTS frames.

    `tiny.en` on CPU by default, like test_stt.py; STT_TEST_MODEL / STT_TEST_DEVICE override.
    """
    settings = Settings(
        _env_file=None,
        stt_model=os.environ.get("STT_TEST_MODEL", "tiny.en"),
        stt_device=os.environ.get("STT_TEST_DEVICE", "cpu"),
    )
    harness.stt = FasterWhisperSTT(settings)
    harness.reply_with(route_completion(), streamed_completion("Timer set for ten minutes."))

    with harness.connect() as ws:
        hello(ws)
        turn = speak(ws, "t1", fixture_pcm("timer.wav"))

    heard = turn.first("transcript.final")["text"]
    expected = json.loads((FIXTURES / "transcripts.json").read_text())["timer.wav"]
    similarity = difflib.SequenceMatcher(None, words(heard), words(expected)).ratio()
    assert similarity >= 0.8, f"got {heard!r}, expected {expected!r}"
    assert "Set a timer" in str(harness.proxy.body(0)["messages"])  # the router got the text
    assert turn.said() == "Timer set for ten minutes."
    assert turn.types()[-1] == "reply.audio.end"


def words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9']+", text.lower())
