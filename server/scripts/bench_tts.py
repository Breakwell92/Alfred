"""TTS benchmark: time to first audio, total synthesis time, process CPU time and VRAM.

Usage (from `server/`, after scripts/download_tts_models.py):
    uv run python scripts/bench_tts.py
    uv run python scripts/bench_tts.py --kokoro-device cpu --runs 20

Text is streamed into `LocalTTS.synthesize` word by word with no delay, as from a fast LLM.
Every run uses a different random text (words drawn from a fixed pool, seeded), because Kokoro
on CUDA is ~2x faster on an input length it has already seen: repeating one sentence would
flatter it.
Time to first audio runs from starting the stream to the first PCM chunk, so it includes
sentence splitting, synthesis of the first segment and resampling. VRAM is the GPU's total use
(nvidia-smi) before and after loading, so other processes on the card must be idle.
"""

from __future__ import annotations

import argparse
import asyncio
import random
import shutil
import statistics
import subprocess
import time
from collections.abc import AsyncIterator

from alfred_server.config import get_settings
from alfred_server.voice.tts import OUTPUT_SAMPLE_RATE, LocalTTS, VoiceConfig, build_tts

WORD_POOL = (
    "the kitchen lights are now off and heating is set to nineteen degrees your next meeting "
    "starts at half past two with design team in usual room shall I remind you before then"
)
WORDS = WORD_POOL.split()
# Text kind -> (sentences, words per sentence)
KINDS = {"short": (1, (3, 5)), "sentence": (1, (8, 12)), "reply": (3, (8, 12))}


def make_text(rng: random.Random, kind: str) -> str:
    sentences, (low, high) = KINDS[kind]
    return " ".join(
        " ".join(rng.sample(WORDS, rng.randint(low, high))).capitalize() + "."
        for _ in range(sentences)
    )


def gpu_memory_mib() -> int | None:
    if not shutil.which("nvidia-smi"):
        return None
    out = subprocess.run(
        ["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
        capture_output=True,
        text=True,
        check=False,
    ).stdout
    return int(out.split()[0]) if out.strip() else None


async def _words(text: str) -> AsyncIterator[str]:
    for word in text.split(" "):
        yield word + " "


async def measure(tts: LocalTTS, voice: VoiceConfig, text: str) -> tuple[float, float, float]:
    """Return (time to first audio, total time, audio seconds)."""
    started = time.perf_counter()
    first: float | None = None
    total_bytes = 0
    async for chunk in tts.synthesize(_words(text), voice):
        if first is None:
            first = time.perf_counter() - started
        total_bytes += len(chunk)
    return first or 0.0, time.perf_counter() - started, total_bytes / 2 / OUTPUT_SAMPLE_RATE


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--runs", type=int, default=10)
    parser.add_argument("--kokoro-device", choices=["auto", "cuda", "cpu"])
    parser.add_argument("--kokoro-model", help="e.g. kokoro-v1.0.fp16.onnx")
    args = parser.parse_args()

    settings = get_settings()
    updates = {}
    if args.kokoro_device:
        updates["tts_kokoro_device"] = args.kokoro_device
    if args.kokoro_model:
        updates["tts_kokoro_model"] = args.kokoro_model
    settings = settings.model_copy(update=updates)

    before = gpu_memory_mib()
    started = time.perf_counter()
    tts = build_tts(settings)
    print(f"Loaded in {time.perf_counter() - started:.1f} s")
    after = gpu_memory_mib()
    if before is not None and after is not None:
        print(f"GPU memory: {before} -> {after} MiB (+{after - before} MiB)")

    voices = [
        VoiceConfig("kokoro", settings.tts_kokoro_voice),
        VoiceConfig("piper", settings.tts_piper_voice),
    ]
    print("| Engine | Voice | Text | First audio mean / p95 | Total mean | Audio | CPU s/run |")
    print("| --- | --- | --- | --- | --- | --- | --- |")
    for voice in voices:
        if voice.voice not in {v.id for v in tts.voices() if v.engine == voice.engine}:
            print(f"| {voice.engine} | {voice.voice} | not installed | | | | |")
            continue
        rng = random.Random(0)
        for _ in range(3):  # warm-up
            asyncio.run(measure(tts, voice, make_text(rng, "sentence")))
        for name in KINDS:
            texts = [make_text(rng, name) for _ in range(args.runs)]
            cpu_started = time.process_time()
            results = [asyncio.run(measure(tts, voice, text)) for text in texts]
            cpu = (time.process_time() - cpu_started) / args.runs
            firsts = sorted(r[0] * 1000 for r in results)
            p95 = firsts[min(len(firsts) - 1, round(0.95 * (len(firsts) - 1)))]
            print(
                f"| {voice.engine} | {voice.voice} | {name} "
                f"| {statistics.mean(firsts):.0f} / {p95:.0f} ms "
                f"| {statistics.mean(r[1] for r in results) * 1000:.0f} ms "
                f"| {results[0][2]:.1f} s | {cpu:.2f} |"
            )


if __name__ == "__main__":
    main()
