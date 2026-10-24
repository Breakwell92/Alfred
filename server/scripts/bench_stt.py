"""Benchmark the STT engine on the WAV fixtures: latency, real-time factor, accuracy, VRAM.

Run from `server/` (CUDA needs `uv sync --extra cuda`):

    uv run python scripts/bench_stt.py --model distil-large-v3 --device cuda
    uv run python scripts/bench_stt.py --model small.en --compute-type int8_float16

One model per run, so the VRAM figure isn't polluted by a previously loaded model.
Latency is measured end to end through `FasterWhisperSTT.transcribe` (chunks streamed in):
from the end of the stream (after the last chunk has been consumed) to the transcript being
returned, so it includes PCM conversion and any wait for a concurrency slot, not just decoding.
Results are recorded in docs/decisions/0002-stt.md.
"""

from __future__ import annotations

import argparse
import asyncio
import difflib
import json
import re
import shutil
import statistics
import subprocess
import time
import wave
from collections.abc import AsyncIterator
from pathlib import Path

from alfred_server.config import Settings
from alfred_server.voice.stt import FasterWhisperSTT

AUDIO_DIR = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "audio"
CHUNK_BYTES = 640  # 20 ms of 16 kHz 16-bit mono, like the phone will send


def gpu_memory_used_mib() -> int | None:
    """Total VRAM in use on GPU 0 (all processes), or None without nvidia-smi."""
    if shutil.which("nvidia-smi") is None:
        return None
    out = subprocess.run(
        ["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits", "-i", "0"],
        capture_output=True,
        text=True,
        check=True,
    )
    return int(out.stdout.strip())


def words(text: str) -> str:
    return " ".join(re.findall(r"[a-z0-9']+", text.lower()))


async def timed_transcribe(stt: FasterWhisperSTT, data: bytes) -> tuple[str, float]:
    """Stream `data` in phone-sized chunks; return the text and ms from end of stream."""
    stream_ended = 0.0

    async def stream() -> AsyncIterator[bytes]:
        nonlocal stream_ended
        for start in range(0, len(data), CHUNK_BYTES):
            yield data[start : start + CHUNK_BYTES]
        stream_ended = time.perf_counter()

    transcript = await stt.transcribe(stream())
    return transcript.text, (time.perf_counter() - stream_ended) * 1000


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--model", default=Settings(_env_file=None).stt_model)
    parser.add_argument("--device", default="cuda", choices=["auto", "cuda", "cpu"])
    parser.add_argument("--compute-type", default="auto")
    parser.add_argument("--beam-size", type=int, default=1)
    parser.add_argument("--runs", type=int, default=10, help="timed runs per clip")
    parser.add_argument(
        "--max-concurrency", type=int, default=1, help="CTranslate2 workers (VRAM per worker)"
    )
    args = parser.parse_args()

    settings = Settings(
        _env_file=None,
        stt_model=args.model,
        stt_device=args.device,
        stt_compute_type=args.compute_type,
        stt_beam_size=args.beam_size,
        stt_max_concurrency=args.max_concurrency,
    )
    transcripts: dict[str, str] = json.loads((AUDIO_DIR / "transcripts.json").read_text("utf-8"))

    vram_before = gpu_memory_used_mib()
    started = time.perf_counter()
    stt = FasterWhisperSTT(settings)
    load_s = time.perf_counter() - started
    stt.warmup()
    vram_loaded = gpu_memory_used_mib()

    print(
        f"model={args.model} device={args.device} compute_type={args.compute_type} "
        f"beam={args.beam_size} workers={args.max_concurrency} load={load_s:.1f}s runs={args.runs}"
    )
    print("| clip | audio s | mean ms | p50 ms | p95 ms | RTF | similarity | text |")
    print("| --- | --- | --- | --- | --- | --- | --- | --- |")
    for name, expected in transcripts.items():
        with wave.open(str(AUDIO_DIR / name), "rb") as wav:
            pcm = wav.readframes(wav.getnframes())
        latencies: list[float] = []
        text = ""
        for _ in range(args.runs):
            text, latency_ms = asyncio.run(timed_transcribe(stt, pcm))
            latencies.append(latency_ms)
        duration = len(pcm) / 2 / 16000
        p95 = statistics.quantiles(latencies, n=20)[-1] if len(latencies) > 1 else latencies[0]
        mean = statistics.fmean(latencies)
        similarity = difflib.SequenceMatcher(None, words(text), words(expected)).ratio()
        print(
            f"| {name} | {duration:.2f} | {mean:.0f} | {statistics.median(latencies):.0f} "
            f"| {p95:.0f} | {mean / 1000 / duration:.3f} | {similarity:.2f} | {text} |"
        )

    vram_after = gpu_memory_used_mib()
    if vram_before is not None and vram_loaded is not None and vram_after is not None:
        print(
            f"VRAM (GPU total, MiB): before={vram_before} loaded={vram_loaded} "
            f"after runs={vram_after} -> STT uses ~{max(vram_loaded, vram_after) - vram_before}"
        )


if __name__ == "__main__":
    main()
