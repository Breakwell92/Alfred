"""Write a sample WAV for each installed TTS voice.

Usage (from `server/`, after scripts/download_tts_models.py):
    uv run python scripts/tts_demo.py                       # every Kokoro and Piper voice
    uv run python scripts/tts_demo.py --engine piper --out /tmp/voices
    uv run python scripts/tts_demo.py --voice bm_george --voice bf_emma --speed 1.1 --pitch -1

Files are named <engine>-<voice>.wav (24 kHz mono 16-bit, the server's output format).
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
import time
from collections.abc import AsyncIterator
from pathlib import Path

from alfred_server.config import get_settings
from alfred_server.voice.tts import (
    OUTPUT_SAMPLE_RATE,
    LocalTTS,
    VoiceConfig,
    build_tts,
    write_wav,
)

DEFAULT_TEXT = (
    "Good evening, sir. The kitchen lights are off, the heating is set to nineteen degrees, "
    "and Mrs. Hudson's parcel arrives at 10 a.m. tomorrow. Will there be anything else?"
)


async def _words(text: str) -> AsyncIterator[str]:
    for word in text.split(" "):
        yield word + " "


async def _speak(tts: LocalTTS, voice: VoiceConfig, text: str) -> bytes:
    return b"".join([chunk async for chunk in tts.synthesize(_words(text), voice)])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, default=Path("tts-samples"))
    parser.add_argument("--engine", choices=["kokoro", "piper"], help="only this engine")
    parser.add_argument("--voice", action="append", help="only these voice ids (repeatable)")
    parser.add_argument("--text", default=DEFAULT_TEXT)
    parser.add_argument("--speed", type=float, default=1.0)
    parser.add_argument("--pitch", type=float, default=0.0, help="semitones")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    tts = build_tts(get_settings())
    voices = [
        v
        for v in tts.voices()
        if (args.engine is None or v.engine == args.engine)
        and (args.voice is None or v.id in args.voice)
    ]
    if not voices:
        print("No matching voices installed.", file=sys.stderr)
        return 1
    args.out.mkdir(parents=True, exist_ok=True)
    for info in voices:
        voice = VoiceConfig(info.engine, info.id, args.speed, args.pitch)
        started = time.perf_counter()
        pcm = asyncio.run(_speak(tts, voice, args.text))
        path = args.out / f"{info.engine}-{info.id}.wav"
        write_wav(path, pcm)
        seconds = len(pcm) / 2 / OUTPUT_SAMPLE_RATE
        print(f"{path}  {seconds:.1f} s audio in {time.perf_counter() - started:.2f} s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
