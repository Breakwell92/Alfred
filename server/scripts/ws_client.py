"""Device WebSocket test client (STU-64): say a WAV to Alfred, save the spoken reply.

Plays the part of the phone on `/ws/device` (docs/protocol.md): sends `hello`, streams the WAV
as one turn, prints the transcript, reply and per-stage timings, and writes the reply audio to
a 24 kHz WAV. With `--runs N` it repeats the turn (a fresh conversation each time) and reports
the median end of speech → first audio, as measured here and by the server.

Needs the server running with `ALFRED_STT_ENABLED=true` and `ALFRED_TTS_ENABLED=true`. The
token is `--token`, else `ALFRED_API_TOKEN` (environment or `server/.env`).

    uv run python scripts/ws_client.py tests/fixtures/audio/timer.wav
    uv run python scripts/ws_client.py clip.wav --runs 10 --realtime --out reply.wav
    uv run python scripts/ws_client.py ask.wav --approve   # answers a confirm.request with yes
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys
import time
import uuid
import wave
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
from websockets.asyncio.client import ClientConnection, connect

from alfred_server.config import get_settings
from alfred_server.voice.gateway.protocol import INPUT_SAMPLE_RATE, parse_audio_frame
from alfred_server.voice.tts import write_wav

CHUNK_S = 0.02  # 20 ms frames, like a phone's audio callback


def read_wav(path: Path) -> bytes:
    """The WAV as 16 kHz mono PCM16, resampled and downmixed if need be."""
    with wave.open(str(path), "rb") as wav:
        if wav.getsampwidth() != 2:
            sys.exit(f"{path}: only 16-bit WAVs are supported")
        rate, channels = wav.getframerate(), wav.getnchannels()
        samples = np.frombuffer(wav.readframes(wav.getnframes()), dtype="<i2")
    audio = samples.reshape(-1, channels).mean(axis=1).astype(np.float32) / 32768.0
    if rate != INPUT_SAMPLE_RATE:
        import soxr

        audio = soxr.resample(audio, rate, INPUT_SAMPLE_RATE)
    return (np.clip(audio, -1.0, 1.0) * 32767).astype("<i2").tobytes()


@dataclass
class TurnResult:
    transcript: str = ""
    reply: str = ""
    audio: bytearray = field(default_factory=bytearray)
    first_audio_ms: float | None = None
    server_timings: dict[str, Any] = field(default_factory=dict)
    error: dict[str, Any] | None = None
    confirmation_id: str | None = None


async def read_turn(
    ws: ClientConnection,
    turn_id: str | None,
    ended: float,
    result: TurnResult,
) -> None:
    """Read until the turn ends (`reply.audio.end` or `error`).

    `turn_id` None takes audio from any turn (a `confirm.reply` answer's is `confirm-<id>`).
    """
    while True:
        frame = await ws.recv()
        if isinstance(frame, bytes):
            tag, pcm = parse_audio_frame(frame)
            if turn_id is not None and tag != turn_id:
                continue
            if result.first_audio_ms is None:
                result.first_audio_ms = (time.perf_counter() - ended) * 1000
            result.audio.extend(pcm)
            continue
        msg = json.loads(frame)
        kind = msg["type"]
        if kind == "transcript.final":
            result.transcript = msg["text"]
        elif kind == "reply.text.delta":
            result.reply += msg["text"]
        elif kind == "turn.metadata":
            result.server_timings = msg.get("timings", {})
            print(
                f"  routed to {msg.get('specialist')} on {msg.get('alias')} ({msg.get('reason')})"
            )
        elif kind == "confirm.request":
            print(f"  confirm.request [{msg['tier']}]: {msg['summary']}")
            result.confirmation_id = msg["id"]
        elif kind == "error":
            result.error = msg
            return
        elif kind == "reply.audio.end":
            return


async def one_turn(args: argparse.Namespace, pcm: bytes, token: str) -> TurnResult:
    session_id = args.session or f"ws-client-{uuid.uuid4()}"
    headers = {"Authorization": f"Bearer {token}"}
    async with connect(args.url, additional_headers=headers, max_size=None) as ws:
        await ws.send(
            json.dumps(
                {
                    "type": "hello",
                    "device_id": args.device_id,
                    "app_version": "ws_client",
                    "session_id": session_id,
                }
            )
        )
        ok = json.loads(await ws.recv())
        if ok.get("type") != "hello.ok":
            sys.exit(f"hello refused: {ok}")
        if not ok.get("voice"):
            sys.exit("the server has STT/TTS off (ALFRED_STT_ENABLED, ALFRED_TTS_ENABLED)")
        turn_id = f"t-{uuid.uuid4().hex[:8]}"
        await ws.send(json.dumps({"type": "turn.start", "turn_id": turn_id}))
        step = int(INPUT_SAMPLE_RATE * CHUNK_S) * 2
        for start in range(0, len(pcm), step):
            await ws.send(pcm[start : start + step])
            if args.realtime:
                await asyncio.sleep(CHUNK_S)
        await ws.send(json.dumps({"type": "turn.end", "turn_id": turn_id}))
        ended = time.perf_counter()
        result = TurnResult()
        await read_turn(ws, turn_id, ended, result)
        answer = True if args.approve else False if args.deny else None
        if answer is not None and result.confirmation_id and result.error is None:
            # Like the app's buttons: answered once the asking turn has ended. Its spoken
            # answer is a turn of its own.
            reply = {"type": "confirm.reply", "confirmation_id": result.confirmation_id}
            await ws.send(json.dumps({**reply, "approve": answer}))
            confirm = TurnResult()
            await read_turn(ws, None, time.perf_counter(), confirm)
            result.reply += " / " + confirm.reply
            if confirm.error is not None:
                result.error = confirm.error
        return result


def _fmt(value: float | None) -> str:
    return "-" if value is None else f"{value:.0f} ms"


async def main() -> None:
    settings = get_settings()
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("wav", type=Path)
    parser.add_argument("--url", default=f"ws://{settings.host}:{settings.port}/ws/device")
    parser.add_argument("--token", default=None)
    parser.add_argument("--device-id", default="ws-client")
    parser.add_argument("--session", default=None, help="continue this conversation")
    parser.add_argument("--runs", type=int, default=1)
    parser.add_argument("--realtime", action="store_true", help="send audio at speaking pace")
    parser.add_argument("--out", type=Path, default=Path("reply.wav"))
    answer = parser.add_mutually_exclusive_group()
    answer.add_argument("--approve", action="store_true", help="say yes to a confirm.request")
    answer.add_argument("--deny", action="store_true", help="say no to a confirm.request")
    args = parser.parse_args()

    token = args.token or (settings.api_token.get_secret_value() if settings.api_token else "")
    if not token:
        sys.exit("No token: pass --token or set ALFRED_API_TOKEN")
    pcm = read_wav(args.wav)
    print(f"{args.wav.name}: {len(pcm) / 2 / INPUT_SAMPLE_RATE:.2f} s of audio -> {args.url}")

    client_ms: list[float] = []
    server_ms: list[float] = []
    result = TurnResult()
    for run in range(1, args.runs + 1):
        result = await one_turn(args, pcm, token)
        print(f"run {run}: heard {result.transcript!r}")
        if result.error is not None:
            print(f"  error: {result.error['code']}: {result.error['message']}")
            continue
        print(f"  reply: {result.reply!r}")
        t = result.server_timings
        print(
            "  server: transcript {}, first token {}, first audio {}, total {} (STT {})".format(
                *(
                    _fmt(t.get(k))
                    for k in (
                        "transcript_ms",
                        "first_token_ms",
                        "first_audio_ms",
                        "total_ms",
                        "stt_ms",
                    )
                )
            )
        )
        print(f"  client: end of speech -> first audio {_fmt(result.first_audio_ms)}")
        if result.first_audio_ms is not None:
            client_ms.append(result.first_audio_ms)
        if t.get("first_audio_ms") is not None:
            server_ms.append(t["first_audio_ms"])

    if result.audio:
        write_wav(args.out, bytes(result.audio))
        print(f"reply audio (last run) -> {args.out}")
    if client_ms:
        print(
            f"median end of speech -> first audio over {len(client_ms)} run(s): "
            f"client {statistics.median(client_ms):.0f} ms, "
            f"server {statistics.median(server_ms):.0f} ms"
        )


if __name__ == "__main__":
    if sys.platform == "win32":
        # The same loop the server needs on Windows; harmless for a client.
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main())
